"""Coordinator for the IS3 Export integration.

The export file is the address book: it says which addresses exist and what they
are called.  State comes from the unit itself, which pushes an ``EVENT`` line
whenever a value changes.

So the flow is: read every address once at startup to get a baseline, then let
the event stream keep it current.  The periodic refresh exists only to re-read
the export file and to re-seed anything that never reported.
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

from .api import Is3Client, Is3Error
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, EXPORT_RELOAD_INTERVAL
from .export import (
    PLATFORM_SENSOR,
    Is3Export,
    is_press_button,
    is_readable,
    platform_of,
)
from .source import (
    Is3ExportAuthError,
    Is3ExportError,
    async_fetch_export,
    read_export_file,
)

_LOGGER = logging.getLogger(__name__)

type Is3ConfigEntry = ConfigEntry[Is3Coordinator]

# Reading the whole address book one address at a time takes a while, so it is
# only done at startup and when an address has still never reported.
INITIAL_READ_LIMIT = 250

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

# An address can answer with no value -- a schedule, plan or scene replies "N",
# a failed sensor "???" -- or not answer at all.  Retrying such an address on
# every scan keeps a GET burst on the one shared connection, and the unit
# answers those ahead of pushing its events, so a button press lands late.  So a
# no-value address is retried only this many times, then left to the event
# stream; one that does answer drops out of the retry set at once.
MAX_SEED_ATTEMPTS = 3


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
        # address -> times it was read and answered no value; capped so a
        # permanently-"N" address stops being re-read every scan (see below).
        self._seed_attempts: dict[int, int] = {}

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
        """Find out once whether this unit answers read commands."""
        export = await self._async_read_export()
        probe = next((e for e in export.entries if is_readable(e)), None)
        if probe is None:
            return

        try:
            self.reads_supported = await self.client.async_get(probe.address_hex) is not None
        except Is3Error:
            self.reads_supported = False

        if not self.reads_supported:
            _LOGGER.warning(
                "Unit did not answer a read of %s using %r as delimiter. Entities "
                "will show an assumed state; try the other delimiter",
                probe.address_hex,
                self.client.delimiter,
            )

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

        if not self.reads_supported:
            if not self._seeded:
                # Fall back to the snapshot the export file was written with.
                self._values = {
                    e.address: e.value for e in export.entries if e.value is not None
                }
                self._seeded = True
            return Is3Data(export=export, values=dict(self._values))

        # Read everything readable once, at startup, to establish a baseline.
        # After that the event stream keeps values current, so only addresses
        # that have *still* never reported are re-read.  Re-reading every output
        # on every cycle -- which this used to do -- put a burst of GETs on the
        # one shared connection every scan, and the unit answered them ahead of
        # pushing its own events, so a button press could land seconds late,
        # queued behind the replies.  Listening beats polling: the unit pushes
        # an event for an output when it changes, the same as for a wall switch.
        unread = [
            e.address_hex
            for e in export.entries
            if is_readable(e)
            and e.address not in self._values
            and self._seed_attempts.get(e.address, 0) < MAX_SEED_ATTEMPTS
        ]
        if unread:
            await self._async_seed(unread[:INITIAL_READ_LIMIT])
            self._seeded = True

        return Is3Data(export=export, values=dict(self._values))

    async def _async_seed(self, addresses: list[str]) -> None:
        """Read a batch of addresses to establish a baseline.

        Reading the whole list takes seconds, so a value can change -- from a
        command, or from a wall switch pushing an event -- while a read is in
        flight.  The reply was captured before that change, so applying it would
        overwrite the newer value with a stale one and leave the entity wrong
        until the next cycle.  Any address updated after its read was issued is
        therefore left as it is.
        """
        _LOGGER.debug("Reading %d addresses to seed state", len(addresses))
        for address in addresses:
            key = int(address, 16)
            asked_at = self.hass.loop.time()
            try:
                value = await self.client.async_get(address)
            except Is3Error as err:
                raise UpdateFailed(f"Cannot read {address}: {err}") from err
            if value is None:
                # No value now; count the miss so we eventually stop asking.
                self._seed_attempts[key] = self._seed_attempts.get(key, 0) + 1
                continue

            if self._updated_at.get(key, 0.0) > asked_at:
                _LOGGER.debug("Ignoring read of %s, changed while in flight", address)
                continue

            self._async_store(key, value)

    async def _async_read_export(self) -> Is3Export:
        """Get the export, re-reading it only once it has gone stale.

        The device list changes only when the installer republishes it from
        IDM3, so it would be wasteful to download it on every value refresh.
        """
        now = self.hass.loop.time()
        if (
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
                return await self.hass.async_add_executor_job(
                    read_export_file, self.export_file
                )
            return await async_fetch_export(
                async_get_clientsession(self.hass),
                self.host,
                self.http_port,
                self.username,
                self.password,
            )
        except Is3ExportAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Is3ExportError as err:
            raise UpdateFailed(str(err)) from err
