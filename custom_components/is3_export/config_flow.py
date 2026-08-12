"""Config flow for the IS3 Export integration.

Setup, reconfigure and re-authentication share one validation path: load the
export (from the unit, a local file, or a file dropped straight into the form),
then connect to the unit and *read some addresses back*.

The read is the part that matters.  A unit hands back a session token even when
the password is not the one it wanted, and only then quietly ignores every
request for a value -- so a connection that succeeded proves nothing.  Without
reading something back, a wrong password would sail through setup and leave
every entity blank with nothing to explain why.

Several addresses, not one: any single address may be one this unit will not
answer, and refusing the whole form over that told people a unit it had just
shaken hands with was unreachable.

The upload exists because newer CU3 firmware no longer serves the export over
HTTP, and copying the file onto the Home Assistant machine by hand is a chore.
An uploaded export is saved under the config folder and the entry stores that
path, so everything downstream still deals in plain files.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    FileSelector,
    FileSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import slugify

from .client import Is3Client, async_password_required
from .errors import Is3AuthError, Is3ConnectionError, Is3Error
from .const import (
    CONF_EXPORT_FILE,
    CONF_EXPORT_UPLOAD,
    DEFAULT_HTTP_PORT,
    DEFAULT_PORT,
    DOMAIN,
    SAVED_EXPORT_DIR,
)
from .export import Is3Export, is_press_button, is_readable
from .source import (
    Is3ExportAuthError,
    Is3ExportError,
    async_fetch_export,
    parse_export_text,
    read_export_file,
)

_LOGGER = logging.getLogger(__name__)


def password_field() -> TextSelector:
    """A masked password box.  Optional: most units have no password set."""
    return TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def build_schema(defaults: dict[str, Any]) -> vol.Schema:
    """The setup form, pre-filled from ``defaults``.

    Empty defaults give a fresh install; the reconfigure step passes the entry's
    own values so the form opens on what is currently set.  The password is
    pre-filled there on purpose -- otherwise someone correcting the host would
    silently blank it and break the entry.
    """
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED)
            ): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): int,
            vol.Optional(
                CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")
            ): password_field(),
            vol.Optional(
                CONF_EXPORT_FILE, default=defaults.get(CONF_EXPORT_FILE, "")
            ): str,
            # A drop-in alternative to the path: newer CU3 firmware serves no
            # HTTP export, and this saves copying the file to the HA machine.
            # Never pre-filled -- an upload id is transient, not configuration.
            vol.Optional(CONF_EXPORT_UPLOAD): FileSelector(
                FileSelectorConfig(accept=".is3,.imm,.txt")
            ),
        }
    )


# How many addresses the setup form reads to prove the unit answers.  More than
# one because any single address may be one this unit will not answer, and that
# is not a reason to refuse the whole form; few enough to stay in one datagram.
PROBE_COUNT = 8

# Every installation has relays low in the address space; used when there is no
# export to pick from, as in the re-authentication step.
FALLBACK_PROBES = ("0x01020001", "0x01020002", "0x01020003")


def _probe_addresses(export: Is3Export | None) -> list[str]:
    """A handful of readable addresses, to ask the unit for in one go.

    Buttons are passed over.  Nothing goes wrong here -- the answer is thrown
    away and no event is raised -- but the coordinator refuses to read a button
    for a good reason, and an installation whose export opens with a wall panel
    would otherwise have this form probing nothing else.  Leaving the one place
    that reads them looking deliberate invites someone to reuse it.
    """
    if export is None:
        return list(FALLBACK_PROBES)
    readable = [
        e.address_hex
        for e in export.entries
        if is_readable(e) and not is_press_button(e)
    ]
    return readable[:PROBE_COUNT] or list(FALLBACK_PROBES)


def saved_export_filename(unique_id: str) -> str:
    """The file an uploaded export is saved as, named after the unit."""
    return f"{slugify(unique_id)}.is3"


def unit_identity(export: Is3Export, host: str) -> tuple[str, str]:
    """The unique id and title for a unit.

    The export header carries an installation id and name; without them the host
    is the best identifier and a generic title is used.
    """
    header = export.header
    unique_id = header.unit_id if header and header.unit_id else host
    title = header.name if header and header.name else f"IS3 {host}"
    return unique_id, title


class Is3ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup and reconfigure dialogs for one central unit."""

    # 2: the binary transport.  A major bump, not a minor one, because an entry
    # written here cannot be read by 0.1.x -- and would not fail loudly if it
    # tried, since the old code defaults every key it no longer finds.  Home
    # Assistant refuses an entry from a newer version outright, which is the
    # behaviour we want if anyone rolls back.
    VERSION = 2
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Hold the text of an export dropped into the form.

        For as long as the dialog is open, not just for one submit: Home
        Assistant deletes an uploaded file the moment it is read, so a form
        that comes back with an error has an empty file picker and no way to
        offer the same file again.
        """
        self._uploaded_text: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the unit address, validate it, and load the export."""
        errors: dict[str, str] = {}

        if user_input is not None:
            export, errors = await self._async_validate(user_input)
            if not errors and export is not None:
                unique_id, title = unit_identity(export, user_input[CONF_HOST])
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                await self._async_store_upload(user_input, unique_id)
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=build_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the connection settings of an existing unit."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            export, errors = await self._async_validate(user_input)
            if not errors and export is not None:
                unique_id, _title = unit_identity(export, user_input[CONF_HOST])
                # The identity comes out of the export's header, and the export
                # is the very thing being replaced here -- republishing a
                # project from IDM3 can change it while the unit on the desk
                # stays the same one.  So a changed id is not by itself wrong.
                #
                # What would be wrong is landing on an id another entry already
                # owns: that is the case this guards, repointing this entry at a
                # unit Home Assistant is already talking to elsewhere.
                if self._owned_by_another_entry(unique_id, entry.entry_id):
                    return self.async_abort(reason="wrong_unit")
                await self._async_store_upload(
                    user_input, unique_id, entry.data.get(CONF_EXPORT_FILE, "")
                )
                # Entity ids hang off the entry id, not off this, so adopting the
                # new identity costs nothing that is on screen.
                return self.async_update_reload_and_abort(
                    entry, unique_id=unique_id, data_updates=user_input
                )

        defaults = user_input if user_input is not None else dict(entry.data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=build_schema(defaults),
            errors=errors,
        )

    def _owned_by_another_entry(self, unique_id: str, entry_id: str) -> bool:
        """Whether some other configured unit already answers to this identity."""
        return any(
            other.unique_id == unique_id and other.entry_id != entry_id
            for other in self._async_current_entries()
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The unit stopped accepting the stored password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the unit's password again and check it before storing it."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            errors = await self._async_check_password(
                entry.data[CONF_HOST],
                entry.data.get(CONF_PORT, DEFAULT_PORT),
                user_input.get(CONF_PASSWORD, ""),
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input.get(CONF_PASSWORD, "")},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Optional(CONF_PASSWORD, default=""): password_field()}
            ),
            errors=errors,
        )

    async def _async_validate(
        self, user_input: dict[str, Any]
    ) -> tuple[Is3Export | None, dict[str, str]]:
        """Load the export and check the unit answers; return (export, errors)."""
        errors: dict[str, str] = {}
        export: Is3Export | None = None

        try:
            export = await self._async_load_export(user_input)
        except Is3ExportAuthError as err:
            # The unit's own web server is asking for credentials this
            # integration does not collect; the way out is a local or uploaded
            # export, not a password.
            _LOGGER.debug("Export is protected: %s", err)
            errors[CONF_EXPORT_FILE] = "export_protected"
        except Is3ExportError as err:
            _LOGGER.debug("Cannot load export: %s", err)
            # Blame the field the export actually came from.
            failed = (
                CONF_EXPORT_UPLOAD
                if user_input.get(CONF_EXPORT_UPLOAD)
                else CONF_EXPORT_FILE
            )
            # Nothing was offered and asking the unit came to nothing, which is
            # what newer firmware does: it serves no export over HTTP at all.
            # "The export could not be read" is true and useless there -- there
            # was no export to read, and what is needed is the file.
            errors[failed] = (
                "invalid_export" if self._export_was_offered(user_input)
                else "export_unavailable"
            )

        if not errors and export is not None:
            errors = await self._async_check_password(
                user_input[CONF_HOST],
                user_input[CONF_PORT],
                user_input.get(CONF_PASSWORD, ""),
                export,
            )

        return export, errors

    async def _async_check_password(
        self,
        host: str,
        port: int,
        password: str,
        export: Is3Export | None = None,
    ) -> dict[str, str]:
        """Connect, then read one address back.  Returns the errors to show.

        Reading is what proves the password: the unit issues a token either way
        and only afterwards decides whether to answer.  Where the export is at
        hand a real address from it is used; the reauth step has none loaded, so
        it falls back to an address every unit has.

        Several addresses are asked for at once, in one datagram.  Reading a
        single one made the whole form depend on that one address answering,
        and reported a unit it had just shaken hands with as unreachable when
        it did not.
        """
        client = Is3Client(host, port, password)
        try:
            try:
                await client.async_connect()
            except Is3AuthError:
                # The unit says whether it has a password at all without being
                # authorized, so tell "you left this blank and it needs one"
                # apart from "the one you typed is wrong".
                needs_password = await async_password_required(host, port)
                if needs_password and not password:
                    return {CONF_PASSWORD: "password_required"}
                return {CONF_PASSWORD: "invalid_auth"}
            except Is3ConnectionError:
                return {CONF_HOST: "cannot_connect"}

            try:
                await client.async_get_many(_probe_addresses(export))
            except Is3Error:
                # It answered the handshake, so it is there and reachable.  A
                # data plane that then says nothing is what a password the unit
                # did not want looks like -- it hands out a token either way and
                # only afterwards decides whether to answer.
                _LOGGER.debug("%s connected but answered no reads", host)
                return {CONF_PASSWORD: "invalid_auth"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error connecting to the unit")
            return {"base": "unknown"}
        finally:
            # Load-bearing: an abandoned client keeps a reconnect loop running
            # against the unit for as long as Home Assistant is up.
            await client.async_close()
        return {}

    def _export_was_offered(self, user_input: dict[str, Any]) -> bool:
        """Whether the export came from the person filling the form."""
        return bool(
            user_input.get(CONF_EXPORT_UPLOAD)
            or user_input.get(CONF_EXPORT_FILE, "").strip()
            or self._uploaded_text is not None
        )

    async def _async_load_export(self, user_input: dict[str, Any]) -> Is3Export:
        """Load the export: an uploaded file, a path on disk, or the unit itself."""
        if file_id := user_input.get(CONF_EXPORT_UPLOAD):
            # A new file replaces whatever was being held.  Dropped first, so a
            # file that turns out to be unreadable cannot leave the previous one
            # in place to be used by the next submit -- accepting a file the
            # form has just rejected.
            self._uploaded_text = None
            text = await self.hass.async_add_executor_job(
                self._read_uploaded_file, file_id
            )
            export = parse_export_text(text, "the uploaded file")
            self._uploaded_text = text
            return export

        if self._uploaded_text is not None:
            # An upload from an earlier attempt at this same dialog.  The file
            # picker is empty again -- Home Assistant deleted the file when it
            # was read -- so without this, correcting a password and pressing
            # submit would fall through to the path below and quietly
            # reconfigure onto the *previous* export.  Which looks, from the
            # outside, exactly like an upload that did nothing: the dialog
            # closes, the entry reloads, and none of the new devices are there.
            return parse_export_text(self._uploaded_text, "the uploaded file")

        if path := user_input.get(CONF_EXPORT_FILE, "").strip():
            return await self.hass.async_add_executor_job(read_export_file, Path(path))

        # Classic firmware serves the export over plain HTTP on port 80,
        # unauthenticated.  Newer firmware (CU3-08M) does not serve it at all --
        # that is what the upload above is for.
        return await async_fetch_export(
            async_get_clientsession(self.hass),
            user_input[CONF_HOST],
            DEFAULT_HTTP_PORT,
        )

    def _read_uploaded_file(self, file_id: str) -> str:
        """Read the export the user dropped into the form (executor)."""
        with process_uploaded_file(self.hass, file_id) as path:
            return path.read_text(encoding="utf-8-sig", errors="replace")

    async def _async_store_upload(
        self, user_input: dict[str, Any], unique_id: str, previous: str = ""
    ) -> None:
        """Keep an uploaded export as a file, so the entry stores only a path.

        The coordinator then re-reads it from disk exactly like a hand-copied
        export, and a republished project is a matter of uploading again (or
        overwriting the saved file).  The transient upload id never reaches the
        stored entry.
        """
        user_input.pop(CONF_EXPORT_UPLOAD, None)
        if self._uploaded_text is None:
            return
        user_input[CONF_EXPORT_FILE] = await self.hass.async_add_executor_job(
            self._write_saved_export, unique_id, self._uploaded_text, previous
        )

    def _write_saved_export(self, unique_id: str, text: str, previous: str) -> str:
        """Write the uploaded export under the config folder (executor).

        A unit that already keeps its export here keeps the same file.  The
        name is made from the installation id, and republishing a project is
        exactly what changes that id -- so naming by it alone would drop a new
        file into the folder on every republish, with nothing to say which of
        them the entry is actually reading.
        """
        folder = Path(self.hass.config.path(SAVED_EXPORT_DIR))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / saved_export_filename(unique_id)
        if previous and Path(previous).parent == folder:
            path = Path(previous)
        path.write_text(text, encoding="utf-8")
        return str(path)
