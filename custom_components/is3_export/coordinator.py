"""Coordinator for the IS3 Export integration.

The export file is the address book: it says which addresses exist and what they
are called.  State comes from the unit itself, which pushes an event whenever a
value changes.

So the flow is: read every address to get a baseline, then let the event stream
keep it current.  The unit answers a whole installation in a fraction of a
second -- 313 addresses in 0.13s on the reference unit, eight datagrams -- so
that baseline is simply taken again on every cycle.  It costs almost nothing and
it means any address whose events stop arriving corrects itself within one
interval, instead of needing the machinery a slow transport used to require.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, EXPORT_RELOAD_INTERVAL
from .client import Is3Client
from .protocol import UnitState
from .errors import Is3AuthError, Is3Error
from .export import (
    PLATFORM_SENSOR,
    Is3Export,
    is_press_button,
    is_readable,
    platform_of,
)
from .issues import async_update_export_issue
from .source import (
    Is3ExportAuthError,
    Is3ExportError,
    async_fetch_export,
    read_export_file,
)

_LOGGER = logging.getLogger(__name__)

type Is3ConfigEntry = ConfigEntry[Is3Coordinator]

# How long a value just written stays authoritative over a contradicting event.
# Long enough to cover the unit's echo of a rapid toggle, short enough that a
# genuine change made at the wall is not hidden for long.  A read-back usually
# resolves it sooner, so this is a fallback bound.
WRITE_SETTLE = 2.5

# After a write, the value is read back to see whether the output followed.
# Long enough for the unit to have acted and reported, short enough to correct
# a stuck icon quickly.
WRITE_VERIFY_DELAY = 1.5

# A single analog input can push dozens of events a second as it jitters, and
# writing a state for each floods the event loop -- enough to delay reading the
# next line, which skews how long a button press looks.  So a sensor's listeners
# are woken at most this often; its value is still stored on every event, only
# the notification is coalesced.  Buttons and outputs are never throttled.
NOTIFY_THROTTLE = 1.0

# An address can answer with no value -- a schedule, plan or scene has none, and
# a failed sensor reports an error marker.  Both read back as None, which is not
# a problem to work around: the address is simply read again next cycle along
# with everything else.


@dataclass(slots=True)
class Is3Data:
    """What the platforms read."""

    export: Is3Export
    values: dict[int, int] = field(default_factory=dict)


class Is3Coordinator(DataUpdateCoordinator[Is3Data]):
    """Owns the export file, the connection, and the current values."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: Is3ConfigEntry,
        client: Is3Client,
        export_file: Path | None,
        host: str,
        http_port: int,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        """Initialise the coordinator.

        With no export file the export is fetched from the unit itself, using
        the credentials if the unit is password protected.
        """
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self.export_file = export_file
        self.host = host
        self.http_port = http_port
        self.username = username
        self.password = password
        self._export: Is3Export | None = None
        self._export_read_at: float = 0.0
        # Digest of the project the unit is running; a change means the
        # installer republished and the device list is worth fetching again.
        self._project_digest: bytes | None = None
        # What the unit says about itself, refreshed each cycle for diagnostics.
        self.unit_state: UnitState | None = None
        self._unit_state_listeners: list[CALLBACK_TYPE] = []
        self.reads_supported = False
        self._values: dict[int, int] = {}
        self._seeded = False
        # Per-address subscriptions (kept apart from the base coordinator's own
        # ``_listeners``, which is a different registry).
        self._address_listeners: dict[int, list[CALLBACK_TYPE]] = {}
        # Last availability broadcast to entities; see async_update_listeners.
        self._availability_broadcast = True
        # address -> (time written, value written), while a command is settling.
        self._pending: dict[int, tuple[float, int]] = {}
        # address -> time its value last changed, from an event or a write.
        self._updated_at: dict[int, float] = {}
        # Sensor addresses whose notifications are rate-limited (see below).
        self._throttled: frozenset[int] = frozenset()
        self._notified_at: dict[int, float] = {}
        self._flush_scheduled: set[int] = set()
        # Button addresses: momentary, so delivered on every event, not deduped.
        self._momentary: frozenset[int] = frozenset()
        # Per-relay-blind travel time in seconds, keyed by the cover's open
        # address.  Owned by the travel-time Number entities, read by the covers
        # to time an auto-stop and to scale their position estimate.
        self.cover_travel_times: dict[int, float] = {}

    @property
    def values(self) -> dict[int, int]:
        """The current value of every address that has reported one."""
        return self._values

    @callback
    def async_update_listeners(self) -> None:
        """Wake entities only when their availability changes.

        Every entity follows its own address live through
        ``async_add_address_listener``, so the base coordinator's habit of waking
        *every* listener on each 30s refresh is redundant -- and on a large site
        it is a synchronous burst of hundreds of state writes.  The one thing a
        per-address update does not carry is availability (``last_update_success``),
        so propagate just that, and only when it actually flips.
        """
        if self.last_update_success == self._availability_broadcast:
            return
        self._availability_broadcast = self.last_update_success
        super().async_update_listeners()

    @callback
    def async_add_address_listener(
        self, address: int, update: CALLBACK_TYPE
    ) -> CALLBACK_TYPE:
        """Subscribe to changes of a single address.

        The unit pushes hundreds of events a minute, most of them for one noisy
        analog input.  Waking every entity for each of those would be a state
        write storm, so events are delivered only to the entity concerned.
        """
        self._address_listeners.setdefault(address, []).append(update)

        @callback
        def remove() -> None:
            """Unsubscribe."""
            listeners = self._address_listeners.get(address)
            if listeners is None:
                return
            listeners.remove(update)
            if not listeners:
                del self._address_listeners[address]

        return remove

    @callback
    def async_add_unit_state_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Subscribe to changes in what the unit says about itself.

        Kept apart from the address listeners for the same reason they exist:
        the base coordinator's blanket wake is suppressed here, so anything not
        tied to an address needs somewhere of its own to be told.
        """
        self._unit_state_listeners.append(update)

        @callback
        def remove() -> None:
            """Unsubscribe."""
            if update in self._unit_state_listeners:
                self._unit_state_listeners.remove(update)

        return remove

    @callback
    def handle_event(self, address: int, value: int) -> None:
        """Record a pushed value and notify only the entity that owns it.

        An event that disagrees with a command sent moments ago is a stale echo
        of an earlier state -- the unit emits an event per physical change, and
        toggling fast means the event for the first command can arrive after the
        second was sent.  While a command is settling such events are ignored;
        an event that confirms it clears the wait; and once the window passes,
        events are trusted again so a change made at the wall is not shadowed.
        """
        pending = self._pending.get(address)
        if pending is not None:
            written_at, written_value = pending
            if value == written_value:
                self._pending.pop(address, None)
            elif self.hass.loop.time() - written_at < WRITE_SETTLE:
                _LOGGER.debug(
                    "Ignoring %s=%s, contradicts a value written %.1fs ago",
                    hex(address),
                    value,
                    self.hass.loop.time() - written_at,
                )
                return
            else:
                self._pending.pop(address, None)
        self._async_store(address, value)

    @callback
    def async_note_write(self, address: int, value: int) -> None:
        """Record a value this integration has just written.

        Entities read their state from the shared value map, so without this a
        command would be invisible until the unit reported back -- and relays
        are pushed rarely and late, which made a switch snap back to its old
        state right after being toggled.
        """
        self._pending[address] = (self.hass.loop.time(), value)
        self._async_store(address, value)

    async def async_command(self, address: int, value: int) -> None:
        """Write a value, show it at once, and confirm the output followed.

        The value is reflected immediately so the UI feels instant.  A moment
        later it is read back: if the output did not follow -- the write was
        rejected, or a wall switch moved it meanwhile -- the real value replaces
        the optimistic one, instead of the entity staying stuck on a state the
        device is not in until the next poll.
        """
        await self.client.async_set(f"0x{address:08X}", value)
        # The unit reports values unsigned; a negative lands as two's complement.
        self.async_note_write(address, value & 0xFFFFFFFF)
        self.config_entry.async_create_background_task(
            self.hass,
            self._async_confirm_write(address, value & 0xFFFFFFFF),
            f"is3-confirm-{address:08x}",
        )

    async def _async_confirm_write(self, address: int, value: int) -> None:
        """Read one address back after a write and correct a value that did not take."""
        await asyncio.sleep(WRITE_VERIFY_DELAY)

        pending = self._pending.get(address)
        if pending is None or pending[1] != value:
            # A confirming event already cleared it, or a newer write replaced it.
            return

        try:
            actual = await self.client.async_get(f"0x{address:08X}")
        except Is3Error:
            return
        if actual is None:
            return  # the channel reports no value; nothing to compare

        self._pending.pop(address, None)
        if actual != value:
            _LOGGER.debug(
                "Write to %#010x did not take (wanted %s, got %s); correcting",
                address,
                value,
                actual,
            )
            self._async_store(address, actual)

    @callback
    def _async_store(self, address: int, value: int) -> None:
        """Store a value and wake the entity that owns the address.

        A sensor's wake is rate-limited so a chatty analog input cannot flood the
        loop; the value is stored regardless, so a later read is current.  A
        button is momentary and wakes on every event, even a repeat of the on
        state -- otherwise a press whose release was lost, leaving the value on,
        would be dropped here as no change and go missing.
        """
        if address not in self._momentary and self._values.get(address) == value:
            return
        self._values[address] = value
        self._updated_at[address] = self.hass.loop.time()

        if address in self._throttled:
            self._async_throttled_notify(address)
        else:
            self._async_notify(address)

    @callback
    def _async_notify(self, address: int) -> None:
        """Wake every entity listening on an address, now."""
        for update in self._address_listeners.get(address, ()):
            update()

    @callback
    def _async_throttled_notify(self, address: int) -> None:
        """Wake listeners at most once per NOTIFY_THROTTLE, keeping the latest."""
        now = self.hass.loop.time()
        since = now - self._notified_at.get(address, 0.0)
        if since >= NOTIFY_THROTTLE:
            self._notified_at[address] = now
            self._async_notify(address)
        elif address not in self._flush_scheduled:
            # A change arrived too soon; wake once when the window is up, with
            # whatever the value is by then.
            self._flush_scheduled.add(address)
            self.hass.loop.call_later(
                NOTIFY_THROTTLE - since, self._async_flush, address
            )

    @callback
    def _async_flush(self, address: int) -> None:
        """The deferred wake for a throttled address."""
        self._flush_scheduled.discard(address)
        self._notified_at[address] = self.hass.loop.time()
        self._async_notify(address)

    async def async_detect_capabilities(self) -> None:
        """Check that the unit will actually answer reads.

        Authorization is not proof: a unit hands back a token and then ignores
        the data plane when the password is not the one it wanted, so a read is
        the only thing that settles it.  An address with no value still counts
        as an answer -- what matters is that the unit replied at all.
        """
        export = await self._async_read_export()
        probe = next((e for e in export.entries if is_readable(e)), None)
        if probe is None:
            return

        try:
            await self.client.async_get(probe.address_hex)
        except Is3AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Is3Error as err:
            raise UpdateFailed(f"Unit did not answer a read: {err}") from err
        self.reads_supported = True

    async def _async_update_data(self) -> Is3Data:
        """Re-read the export file and seed any address that has no value yet."""
        export = await self._async_read_export()

        # Sensors change continuously and are the ones that flood; buttons and
        # outputs are not throttled, so their events reach entities at once.
        self._throttled = frozenset(
            entry.address
            for entry in export.entries
            if platform_of(entry) == PLATFORM_SENSOR
        )
        # Buttons are momentary: every press event matters, even one repeating
        # the on state, so they bypass the same-value dedup below.
        self._momentary = frozenset(
            entry.address for entry in export.entries if is_press_button(entry)
        )
        # Buttons are deliberately left out.  A button has no state worth
        # re-reading -- everything it means is in the transition, and that
        # arrives as an event.  Reading one can only do harm: its value is
        # delivered as though the contact had just moved, and an RF input whose
        # release went missing reads as still-down, which would fire a press on
        # every cycle, forever.
        await self._async_seed(
            [
                e.address_hex
                for e in export.entries
                if is_readable(e) and e.address not in self._momentary
            ]
        )
        self._seeded = True
        await self._async_read_unit_state()

        return Is3Data(export=export, values=dict(self._values))

    async def _async_read_unit_state(self) -> None:
        """Ask the unit how it is running, for the diagnostic sensor.

        Best effort: this is one unauthenticated packet, and a unit that will
        not answer it is not a reason to fail a refresh that has otherwise
        worked.
        """
        try:
            state = await self.client.async_unit_state()
        except Is3Error as err:
            _LOGGER.debug("Could not read the unit's state: %s", err)
            return

        if state is None or state == self.unit_state:
            return
        self.unit_state = state
        for update in self._unit_state_listeners:
            update()

    async def _async_seed(self, addresses: list[str]) -> None:
        """Read every readable address and store what came back.

        The unit answers dozens of addresses per datagram, so the whole list is
        a handful of round trips and is simply re-read each cycle.  That is what
        makes an address whose events stop arriving -- a computed controller
        output, say, which some units never push -- correct itself, without
        anything having to know which addresses are at risk.

        A value can still change while the batch is in flight: a wall switch
        pushes an event, or a command lands.  The reply was captured before
        that, so applying it would put the older value back; any address updated
        after the read was issued is therefore left alone.
        """
        if not addresses:
            return

        asked_at = self.hass.loop.time()
        try:
            values = await self.client.async_get_many(addresses)
        except Is3AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Is3Error as err:
            raise UpdateFailed(f"Cannot read the unit: {err}") from err

        for address, value in values.items():
            if value is None:
                continue  # no value for this address right now
            key = int(address, 16)
            if self._updated_at.get(key, 0.0) > asked_at:
                _LOGGER.debug("Ignoring read of %s, changed while in flight", address)
                continue
            self._async_store(key, value)

    async def _async_project_changed(self) -> bool | None:
        """Whether the unit is running a different project than last time.

        The unit will hand over a digest of the project loaded in it, which
        changes exactly when the installer republishes from IDM3 -- the one
        thing that changes the device list.  Asking costs a single packet, so
        a republish is noticed within a cycle instead of within the reload
        interval, and the export does not have to be downloaded to find out.

        None means the unit did not say, and the caller should fall back to
        re-reading on a timer.
        """
        try:
            digest = await self.client.async_project_digest()
        except Is3Error:
            return None
        if digest is None:
            return None

        changed = self._project_digest is not None and digest != self._project_digest
        self._project_digest = digest
        return changed

    async def _async_read_export(self) -> Is3Export:
        """Get the export, re-reading it when the unit's project changes.

        Falls back to a timer where the unit will not say -- and keeps the
        timer regardless for an export read off disk, since a file can be
        replaced by hand without the unit's project changing at all.
        """
        now = self.hass.loop.time()
        changed = await self._async_project_changed()

        if changed:
            _LOGGER.debug("Unit reports a different project; re-reading the export")
        elif (
            self._export is not None
            and changed is False
            and self.export_file is None
        ):
            # The unit is running what it was; nothing to download.
            return self._export
        elif (
            self._export is not None
            and now - self._export_read_at < EXPORT_RELOAD_INTERVAL.total_seconds()
        ):
            return self._export

        export = await self._async_load_export()
        previous = self._export
        self._export = export
        self._export_read_at = now

        # Entities are built when the entry is set up, so a changed device list
        # only takes effect after a reload. The installer republishes from IDM3
        # by hand, so this is rare.
        if previous is not None and previous.fingerprint != export.fingerprint:
            _LOGGER.info(
                "Export changed (%d entries, was %d); reloading to pick up the "
                "new device list",
                len(export.entries),
                len(previous.entries),
            )
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self.config_entry.entry_id)
            )

        return export

    async def _async_load_export(self) -> Is3Export:
        """Read the export, from disk or from the unit."""
        try:
            if self.export_file is not None:
                export = await self.hass.async_add_executor_job(
                    read_export_file, self.export_file
                )
            else:
                export = await async_fetch_export(
                    async_get_clientsession(self.hass),
                    self.host,
                    self.http_port,
                    self.username,
                    self.password,
                )
        except Is3ExportAuthError as err:
            # Deliberately not ConfigEntryAuthFailed: that opens the
            # re-authentication dialog, which collects the unit's own password
            # -- a different credential from the one this web server is asking
            # for.  The user would type the right password, it would validate,
            # and the download would fail again, forever.  The way out is to
            # upload the export, so say that instead.
            async_update_export_issue(
                self.hass, self.config_entry.entry_id, blocked=True
            )
            raise UpdateFailed(f"{err}. Upload the export file instead.") from err
        except Is3ExportError as err:
            raise UpdateFailed(str(err)) from err

        async_update_export_issue(
            self.hass, self.config_entry.entry_id, blocked=False
        )
        return export
