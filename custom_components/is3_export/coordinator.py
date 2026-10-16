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

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EXPORT_RELOAD_INTERVAL,
    POLL_ONLY_SCAN_INTERVAL,
)
from .client import MAX_BATCH, Is3Client
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

# A dimmer has no fade of its own to hand a transition to -- on iNELS that is a
# programming function, not a module setting -- so a fade is the integration
# walking the output there in steps.  Ten a second is smooth to the eye and
# nothing to the unit: measured live, a step -- a read of where the output is,
# then the write -- is answered in about 10ms, and echoes back within a quarter
# of a second, in order.
FADE_STEP = 0.1

# Most writes fit one packet; a scene fading forty dimmers at once still does.
FADE_BATCH = MAX_BATCH


def _retrieve(future: asyncio.Future[None]) -> None:
    """Mark a fade's outcome as seen.

    A fade whose first step failed carries the error for whoever asked; if they
    have gone, asyncio would otherwise log it as an exception nobody retrieved.
    """
    if not future.cancelled():
        future.exception()


def _fresh(at: float, now: float) -> bool:
    """Whether something sent at ``at`` can still be on its way back."""
    return now - at < WRITE_SETTLE


@dataclass(slots=True)
class _Steps:
    """What has been sent to one dimmer lately, and what has come back.

    Kept per address rather than per fade.  A fade that has arrived, or been
    stopped, still has steps on their way back -- an event trails a write by
    about 140 ms, the register by about 100 -- and a fade started inside that
    window has to recognise them.  Forgetting them along with the fade that
    sent them is how a reversal started right after a fade took the old fade's
    late echo for someone at the wall, and wrote the stale level back.
    """

    # Levels sent and not yet heard back as events, oldest first, with when
    # each went.  The unit echoes every step, in order, so an event is one of
    # ours only if it matches one still on its way.  A level already heard
    # back, turning up again, is someone at the wall -- dimming past it, say.
    unechoed: list[tuple[int, float]] = field(default_factory=list)
    # The last one heard back, and when: the unit sometimes says it twice.
    heard: tuple[int, float] | None = None
    # The register lags the same way, showing a write about 100 ms after it
    # is acknowledged: levels not yet seen there, and the level last seen and
    # when -- a reading from an hour ago says nothing about now.
    unread: list[tuple[int, float]] = field(default_factory=list)
    seen: tuple[int, float] | None = None
    # Every level sent lately, heard back since or not.
    sent: dict[int, float] = field(default_factory=dict)

    def note_sent(self, level: int, at: float) -> None:
        """Remember a level on its way out, forgetting any long overdue."""
        self.unechoed = [(s, t) for s, t in self.unechoed if _fresh(t, at)]
        self.unechoed.append((level, at))
        self.unread = [(s, t) for s, t in self.unread if _fresh(t, at)]
        self.unread.append((level, at))
        self.sent = {s: t for s, t in self.sent.items() if _fresh(t, at)}
        self.sent[level] = at

    def note_acked(self, level: int, noted: float, at: float) -> None:
        """Date a level noted on its way out from when it was acknowledged.

        It is noted before the write, so that an echo beating a lost reply is
        still known -- but the write can then wait seconds for the client
        behind someone else's lost reply, and the unit only acts on it once it
        is through.  Freshness runs from then.
        """
        self.unechoed = [(s, at if (s, t) == (level, noted) else t) for s, t in self.unechoed]
        self.unread = [(s, at if (s, t) == (level, noted) else t) for s, t in self.unread]
        if self.sent.get(level) == noted:
            self.sent[level] = at

    def note_seen(self, value: int, at: float) -> None:
        """The register was read at this level."""
        self.seen = (value, at)

    def sent_lately(self, value: int | None, now: float) -> bool:
        """Whether this integration itself sent ``value`` a moment ago."""
        at = self.sent.get(value) if value is not None else None
        return at is not None and _fresh(at, now)

    def is_echo(self, value: int | None, now: float) -> bool:
        """Whether an event is one of ours coming back; consumes it if so."""
        if self.heard is not None and self.heard[0] == value and _fresh(self.heard[1], now):
            return True
        for i, (sent, at) in enumerate(self.unechoed):
            if sent == value and _fresh(at, now):
                # Everything sent before it has been passed: echoes come in order.
                del self.unechoed[: i + 1]
                self.heard = (sent, now)
                return True
        return False

    def in_flight(self, now: float) -> bool:
        """Whether something sent has not been heard back yet.

        Echoes come in order, so when someone else's change is heard while one
        of ours is still unheard, ours reached the unit *after* theirs -- and
        undid it.
        """
        return any(_fresh(at, now) for _, at in self.unechoed)

    def in_register(self, value: int, level: int, now: float) -> bool:
        """Whether the register shows something of ours.

        That is a level still on its way into the register, or ``level``, the
        last step acknowledged -- or, with ours not landed yet, the level it
        was last seen at.  What is still landing is looked at first: a step of
        a fade just replaced can be on its way in after the very level the new
        fade starts from, and clearing it on seeing that level made the step
        look like someone else's when it landed, and the new fade stop.
        """
        landing = [(s, at) for s, at in self.unread if _fresh(at, now)]
        for i, (sent, _) in enumerate(landing):
            if sent == value:
                self.unread = landing[i + 1 :]
                self.note_seen(value, now)
                return True
        if value == level:
            self.unread.clear()
            self.note_seen(value, now)
            return True
        seen = self.seen if self.seen is not None and _fresh(self.seen[1], now) else None
        if landing and (seen is None or value == seen[0]):
            # Nothing of ours has landed in it yet.
            self.note_seen(value, now)
            return True
        return False


@dataclass(slots=True)
class _Fade:
    """One dimmer on its way from one level to another."""

    start: int
    target: int
    began: float
    duration: float
    # Where the output actually is: the last level the unit acknowledged.
    level: int
    steps: _Steps
    # Resolved once the first step has landed, or failed with the reason.
    started: asyncio.Future[None]
    # What showed before the target did, to put back if the very first step
    # is refused -- the unit may well not be there to read it from.
    shown_before: int | None = None

    def level_at(self, now: float) -> int:
        """Where the fade should be at ``now``, on the unit's own scale."""
        if self.duration <= 0 or now >= self.began + self.duration:
            return self.target
        progress = max(0.0, now - self.began) / self.duration
        return round(self.start + (self.target - self.start) * progress)

    def next_level(self, now: float) -> int | None:
        """The step to send at ``now``, or None if there is nothing to send."""
        level = self.level_at(now)
        if not self.started.done():
            # The first step goes out at once, however slow the fade: it is
            # what proves the unit is listening.  But never as the level the
            # output is already at -- its echo would look exactly like someone
            # at the wall putting it back there, which is the one thing a fade
            # has to give way to.
            if level == self.level != self.target:
                level += 1 if self.target > level else -1
            return level
        if self.level == self.target:
            return None  # there, early; it waits out its time
        # Only ever onward, in the direction it set out: after that first
        # nudge the schedule can be a step behind, and going back would resend
        # the level it started from.
        if self.target > self.start:
            level = max(level, self.level)
        else:
            level = min(level, self.level)
        return None if level == self.level else level

    def ended(self, now: float) -> bool:
        """Whether the output has arrived and the fade's time is up."""
        return self.level == self.target and now >= self.began + self.duration


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
        # Dimmers being faded, by address, and the one task stepping them all.
        self._fades: dict[int, _Fade] = {}
        self._fade_task: asyncio.Task[None] | None = None
        # What has gone out to each faded dimmer lately; outlives the fade.
        self._steps: dict[int, _Steps] = {}

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

        A dimmer being faded echoes every step, and those are recognised as its
        own.  Anything else is someone else moving it -- a wall button, a
        function in the unit -- and theirs wins: the fade stops where it is.
        """
        now = self.hass.loop.time()
        steps = self._steps.get(address)
        fade = self._fades.get(address)
        if fade is not None:
            if not fade.steps.is_echo(value, now):
                # Something of ours still unheard landed after their change.
                self._async_yield_fade(
                    address, fade, value, overridden=fade.steps.in_flight(now)
                )
            return
        if steps is not None:
            # No fade now, but its last echoes may still be arriving: take them
            # off the list, so a fade started next does not wait for them.
            steps.is_echo(value, now)
        self._async_apply_event(address, value)

    @callback
    def _async_yield_fade(
        self, address: int, fade: _Fade, value: int | None, *, overridden: bool
    ) -> None:
        """Someone else moved a dimmer mid-fade: stop, and let theirs stand.

        Their change is stored before anything follows it up -- those tasks
        start at once, and rely on it being there.
        """
        _LOGGER.debug(
            "%s moved to %s during a fade; stopping the fade", hex(address), value
        )
        self._async_stop_fade(address)
        self._async_apply_event(address, value)
        now = self.hass.loop.time()
        if (
            overridden
            and value is not None
            # Never write back a level this integration sent itself: that is
            # one of ours, come back too late to be recognised, not theirs.
            and not fade.steps.sent_lately(value, now)
        ):
            # The event is a step behind, there is no helping that, so a step
            # sent meanwhile undid their change.  Put it back: the wall wins,
            # all the way.
            follow_up = self._async_reassert(address, value)
        else:
            # The register is prompt where events are not: one read settles
            # what the output is, should this have been an echo misjudged.
            follow_up = self._async_read_back(address)
        self.config_entry.async_create_background_task(
            self.hass, follow_up, f"is3-after-fade-{address:08x}"
        )

    @callback
    def _async_apply_event(self, address: int, value: int | None) -> None:
        """Store a pushed value, unless it contradicts a write still settling."""
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

        The write counts as a change even when the value was already showing --
        a fade shows its target from the start -- so that a read already in
        flight, carrying the level from before, cannot land on top of it.
        """
        now = self.hass.loop.time()
        self._pending[address] = (now, value)
        self._async_store(address, value)
        self._updated_at[address] = now

    async def async_command(self, address: int, value: int) -> None:
        """Write a value, show it at once, and confirm the output followed.

        The value is reflected immediately so the UI feels instant.  A moment
        later it is read back: if the output did not follow -- the write was
        rejected, or a wall switch moved it meanwhile -- the real value replaces
        the optimistic one, instead of the entity staying stuck on a state the
        device is not in until the next poll.

        A fade in progress on the address is stopped first, or its next step
        would undo this.
        """
        fade = self._fades.get(address)
        self._async_stop_fade(address)
        # Kept for every write, not only a dimmer that has faded: a fade
        # started a moment after this has to know this level's echo -- and the
        # unit's occasional second one -- for one of ours.
        steps = self._steps.setdefault(address, _Steps())
        noted = self.hass.loop.time()
        steps.note_sent(value & 0xFFFFFFFF, noted)
        try:
            await self.client.async_set(f"0x{address:08X}", value)
        except Is3Error:
            if fade is not None and self._fades.get(address) is None:
                # The fade's target was showing; the output is where it stopped.
                self._async_store(address, fade.level)
            raise
        steps.note_acked(value & 0xFFFFFFFF, noted, self.hass.loop.time())
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
        if address in self._fades or self._pending.get(address) != pending:
            # Something newer took the address over while the read was out.
            return

        self._pending.pop(address, None)
        if actual != value:
            _LOGGER.debug(
                "Write to %#010x did not take (wanted %s, got %s); correcting",
                address,
                value,
                actual,
            )
        # Stored even when it matches: what is showing may not be the value
        # written, if something stale landed on top of it meanwhile.
        self._async_store(address, actual)

    # ---- fades -------------------------------------------------------------

    def fade_level(self, address: int) -> int | None:
        """Where a dimmer being faded actually is; None if it is not fading.

        Not what is showing -- a fade shows its target from the start -- but the
        last step the unit took, which is where a new request has to start.
        """
        fade = self._fades.get(address)
        return None if fade is None else fade.level

    async def async_fade(
        self, address: int, start: int, target: int, duration: float
    ) -> None:
        """Walk a dimmer from ``start`` to ``target`` over ``duration`` seconds.

        The target shows at once, the way a lamp that fades itself reports it:
        Home Assistant shows where a light is going, not each step on the way,
        and a state per step would be a recorder row per step.  Returns once the
        first step has landed -- so a unit that is not answering is reported to
        whoever asked -- and the rest carries on in the background, where a new
        request for the same dimmer replaces it.

        Every dimmer fading at the same moment is stepped in the same packet,
        so a scene fades its lights together rather than one after another.
        """
        now = self.hass.loop.time()
        # Whatever was sent here lately may still be on its way back -- the
        # steps of a fade this one replaces or follows, a level set since.  A
        # write made before any fade, not yet confirmed, is one of those too.
        # Nothing else: in particular not the level it starts from, which an
        # event may only carry because someone at the wall set it back there.
        steps = self._steps.setdefault(address, _Steps())
        pending = self._pending.get(address)
        if pending is not None and steps.sent.get(pending[1]) != pending[0]:
            steps.note_sent(pending[1], pending[0])
        # What to put back should this one's first step be refused: where a
        # fade it replaces has got to -- not the target that one was showing,
        # which the output never reached -- or else what shows now.
        previous = self._fades.get(address)
        shown_before = previous.level if previous is not None else self._values.get(address)
        self._async_stop_fade(address)

        fade = _Fade(
            start=start,
            target=target,
            began=now,
            duration=duration,
            level=start,
            steps=steps,
            started=self.hass.loop.create_future(),
            shown_before=shown_before,
        )
        # Nobody may be left awaiting a fade that failed; see _async_fail_fade.
        fade.started.add_done_callback(_retrieve)
        self._fades[address] = fade
        # The fade owns the address now: a read-back still due for an earlier
        # write would compare against a level the fade has long since left.
        self._pending.pop(address, None)
        self._async_store(address, target)
        self._updated_at[address] = now

        if self._fade_task is None or self._fade_task.done():
            self._fade_task = self.config_entry.async_create_background_task(
                self.hass, self._async_run_fades(), "is3-fade"
            )
        await asyncio.shield(fade.started)

    @callback
    def _async_stop_fade(self, address: int) -> None:
        """Stop a fade where it is, if there is one; whoever asked is released."""
        fade = self._fades.pop(address, None)
        if fade is not None and not fade.started.done():
            fade.started.set_result(None)

    async def _async_run_fades(self) -> None:
        """Step every fade in progress; see _async_step_fades."""
        try:
            await self._async_step_fades()
        finally:
            # However this ends -- cancelled at unload, or something unforeseen
            # -- nobody is left waiting on a fade that will never be stepped.
            for address in list(self._fades):
                self._async_stop_fade(address)

    async def _async_step_fades(self) -> None:
        """Step every fade in progress, all of them in one packet per step."""
        loop = self.hass.loop
        while self._fades:
            tick = loop.time()
            due = [
                (address, fade, level)
                for address, fade in self._fades.items()
                if (level := fade.next_level(tick)) is not None
            ]
            for i in range(0, len(due), FADE_BATCH):
                chunk = await self._async_check_fades(due[i : i + FADE_BATCH], tick)
                if not chunk:
                    continue
                # Noted before the write, not after: the unit can apply a step
                # and echo it while its reply is lost and resent, and an echo
                # arriving first must still be recognised as the fade's own.
                sent_at = loop.time()
                for _, fade, level in chunk:
                    fade.steps.note_sent(level, sent_at)
                try:
                    await self.client.async_set_many(
                        [(f"0x{address:08X}", level) for address, _, level in chunk]
                    )
                except Is3Error as err:
                    for address, fade, _ in chunk:
                        self._async_fail_fade(address, fade, err)
                    continue
                acked = loop.time()
                for _, fade, level in chunk:
                    fade.level = level
                    fade.steps.note_acked(level, sent_at, acked)
                    if not fade.started.done():
                        fade.started.set_result(None)

            now = loop.time()
            for address, fade in list(self._fades.items()):
                if fade.ended(now):
                    del self._fades[address]
                    # Arrived: from here on it is an ordinary written value,
                    # settled against late echoes and confirmed by a read.
                    self.async_note_write(address, fade.target)
                    self.config_entry.async_create_background_task(
                        self.hass,
                        self._async_confirm_write(address, fade.target),
                        f"is3-confirm-{address:08x}",
                    )
            if self._fades:
                await asyncio.sleep(max(0.0, FADE_STEP - (loop.time() - tick)))

    async def _async_check_fades(
        self, chunk: list[tuple[int, _Fade, int]], tick: float
    ) -> list[tuple[int, _Fade, int]]:
        """Read where each output is before stepping it; leave any someone moved.

        Events cannot be relied on to say so.  The unit reports a value only if
        it is still there when it next looks, and the next step lands within a
        tenth of a second -- so a change at the wall that a step overwrites may
        never be reported at all.  Seen on a live unit: another client's write,
        followed 20 ms later by a step, left no event behind, and the fade went
        on over it.  The register holds the change until that step, though, and
        reading it costs a few milliseconds.

        Where reads are not answered there is nothing to check against, and
        the fade simply steps.
        """
        if not self.reads_supported:
            # Still only fades that are still running: one stopped while the
            # previous packet was out must not have its step sent after all.
            return [c for c in chunk if self._fades.get(c[0]) is c[1]]
        try:
            actual = await self.client.async_get_many(
                [f"0x{address:08X}" for address, _, _ in chunk]
            )
        except Is3Error as err:
            for address, fade, _ in chunk:
                self._async_fail_fade(address, fade, err)
            return []

        now = self.hass.loop.time()
        checked: list[tuple[int, _Fade, int]] = []
        for address, fade, level in chunk:
            if self._fades.get(address) is not fade:
                continue  # replaced or stopped while the read was out
            value = actual.get(f"0x{address:08X}")
            if value is None or fade.steps.in_register(value, fade.level, now):
                checked.append((address, fade, level))
            elif not fade.started.done():
                # Not where it was thought to be -- what showed was stale.
                # Start from where it really is.
                fade.start = fade.level = value
                fade.steps.note_seen(value, now)
                if (level := fade.next_level(tick)) is not None:
                    checked.append((address, fade, level))
            else:
                # Moved since the last step, and not by the fade: theirs stands.
                self._async_yield_fade(address, fade, value, overridden=False)
        return checked

    @callback
    def _async_fail_fade(self, address: int, fade: _Fade, err: Is3Error) -> None:
        """A step was not acknowledged: give up, and show where the output is."""
        if self._fades.get(address) is not fade:
            return  # already replaced or stopped; the new owner carries on
        del self._fades[address]
        if not fade.started.done():
            fade.started.set_exception(err)
            # Not one step taken, so the output is where it was -- and what
            # showed before is the best word on that while the unit is not
            # answering, as it may well not be.
            if (
                fade.shown_before is not None
                and self._updated_at.get(address) == fade.began
            ):
                self._async_store(address, fade.shown_before)
        else:
            _LOGGER.warning("Fade of %#010x stopped part-way: %s", address, err)
        # The target was shown up front; put back what the output really is.
        self.config_entry.async_create_background_task(
            self.hass, self._async_read_back(address), f"is3-readback-{address:08x}"
        )

    async def _async_reassert(self, address: int, value: int) -> None:
        """Write back a change a fade step undid, unless something newer came."""
        if address in self._fades or self._values.get(address) != value:
            return
        try:
            await self.async_command(address, value)
        except Is3Error as err:
            _LOGGER.debug("Could not restore %#010x to %s: %s", address, value, err)

    async def _async_read_back(self, address: int) -> None:
        """Read one address and show it, unless something newer owns it."""
        asked_at = self.hass.loop.time()
        try:
            actual = await self.client.async_get(f"0x{address:08X}")
        except Is3Error:
            return
        if (
            actual is None
            or address in self._fades
            or self._updated_at.get(address, 0.0) > asked_at
        ):
            return
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
        self._async_follow_event_stream()

        return Is3Data(export=export, values=dict(self._values))

    @callback
    def _async_follow_event_stream(self) -> None:
        """Match the refresh interval to whether the unit pushes its changes.

        Checked every cycle rather than once at setup: the stream is asked for
        again on each reconnect, so a unit can start pushing -- or stop -- long
        after the entry was loaded.  The base coordinator reads this attribute
        when it schedules the next run, which is after this returns, so setting
        it here needs nothing rescheduled.
        """
        wanted = (
            DEFAULT_SCAN_INTERVAL
            if getattr(self.client, "events_started", True)
            else POLL_ONLY_SCAN_INTERVAL
        )
        if self.update_interval != wanted:
            _LOGGER.debug("Refreshing every %s", wanted)
            self.update_interval = wanted

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
            if key in self._fades:
                continue  # caught mid-fade; the fade knows better
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
