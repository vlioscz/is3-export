"""Fading a dimmer: the coordinator walks it to the new level in steps.

A DA3 jumps to whatever it is given, and fading on iNELS is a programming
function rather than a module setting -- so Home Assistant's ``transition`` is
honoured by writing the way there.  These drive the fade engine on a real event
loop, against a fake unit with a register per address that records every
packet, with the step shortened so a fade takes milliseconds.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.util.async_ import create_eager_task

import custom_components.is3_export.coordinator as coordinator_module
from custom_components.is3_export.coordinator import Is3Coordinator
from custom_components.is3_export.errors import Is3ConnectionError, Is3Error

LAMP = 0x01040001
LAMP_HEX = "0x01040001"
OTHER = 0x01040002
OTHER_HEX = "0x01040002"


@pytest.fixture(autouse=True)
def _fast(monkeypatch) -> None:
    """Steps a hundred times faster, and read-backs without the wait."""
    monkeypatch.setattr(coordinator_module, "FADE_STEP", 0.001)
    monkeypatch.setattr(coordinator_module, "WRITE_VERIFY_DELAY", 0)


class _Entry:
    """Just the background-task hook the coordinator uses.

    Started eagerly, as Home Assistant's are: the task runs up to its first
    wait before the caller carries on, which is an ordering the code must get
    right and a lazily started task would hide.
    """

    def __init__(self) -> None:
        self.tasks: list[asyncio.Task] = []

    def async_create_background_task(self, hass, coro, name):
        task = create_eager_task(coro, name=name)
        self.tasks.append(task)
        return task


class _Unit:
    """A register per address: writes land in it, reads answer from it."""

    def __init__(
        self,
        register: dict[str, int] | None = None,
        *,
        fail_writes=False,
        fail_reads=False,
        lag=0,
    ):
        self.register: dict[str, int] = dict(register or {})
        self.packets: list[list[tuple[str, int]]] = []
        self.reads: list[list[str]] = []
        self.fail_writes = fail_writes
        self.fail_reads = fail_reads
        # A live unit shows a write in its register a cycle (about 100 ms)
        # after acknowledging it -- a step later.  Counted here in reads, so
        # the test does not hang on the timer resolution of the machine.
        self.lag = lag
        self._landing: list[list] = []
        # Called with each packet once applied, before it is acknowledged: the
        # order a lost reply produces, the echo arriving ahead of the ack.
        self.before_ack = None
        self._held: asyncio.Future | None = None

    def hold_next_read(self) -> asyncio.Future:
        """Keep the next read out, answer captured, until the future is set."""
        self._held = asyncio.get_running_loop().create_future()
        return self._held

    async def async_set_many(self, writes):
        if self.fail_writes:
            await asyncio.sleep(0)
            raise Is3ConnectionError("Write was not acknowledged")
        # Recorded as sent, so nothing in flight can land after a test looks.
        self.packets.append(list(writes))
        if self.lag:
            self._landing.append([self.lag, list(writes)])
        else:
            self.register.update(writes)
        if self.before_ack is not None:
            self.before_ack(list(writes))
        await asyncio.sleep(0)

    async def async_set(self, address, value):
        await self.async_set_many([(address, value)])

    async def async_get_many(self, addresses):
        if self.fail_reads:
            await asyncio.sleep(0)
            raise Is3ConnectionError("Reading was refused or unanswered")
        self.reads.append(list(addresses))
        answer = {address: self.register.get(address) for address in addresses}
        for landing in self._landing:
            landing[0] -= 1
        while self._landing and self._landing[0][0] <= 0:
            self.register.update(self._landing.pop(0)[1])
        if (held := self._held) is not None:
            self._held = None
            await held
        await asyncio.sleep(0)
        return answer

    async def async_get(self, address):
        return (await self.async_get_many([address]))[address]

    def written(self, address_hex: str) -> list[int]:
        return [v for packet in self.packets for a, v in packet if a == address_hex]


def _coordinator(unit: _Unit) -> Is3Coordinator:
    coord = Is3Coordinator.__new__(Is3Coordinator)
    coord.hass = SimpleNamespace(loop=asyncio.get_running_loop())
    coord.config_entry = _Entry()
    coord.client = unit
    coord.reads_supported = True
    coord._values = {}
    coord._pending = {}
    coord._updated_at = {}
    coord._address_listeners = {}
    coord._throttled = frozenset()
    coord._notified_at = {}
    coord._flush_scheduled = set()
    coord._momentary = frozenset()
    coord._fades = {}
    coord._steps = {}
    coord._fade_task = None
    return coord


async def _settle(coord: Is3Coordinator) -> None:
    """Let every fade and read-back finish."""
    for _ in range(2000):
        if not coord._fades and all(t.done() for t in coord.config_entry.tasks):
            return
        await asyncio.sleep(0.001)
    raise AssertionError("the fade never finished")


# --- the fade itself ---------------------------------------------------------


def test_a_fade_walks_the_output_to_its_target() -> None:
    """Every step is on the way, in order, and the last one is the target."""

    async def run() -> list[int]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 0.3)
        await _settle(coord)
        assert coord.values[LAMP] == 100
        return unit.written(LAMP_HEX)

    steps = asyncio.run(run())
    assert len(steps) > 3, f"that was a jump, not a fade: {steps}"
    assert steps == sorted(steps), f"the fade went backwards: {steps}"
    assert steps[-1] == 100


def test_the_target_shows_from_the_start_and_the_steps_do_not() -> None:
    """Home Assistant shows where a light is going; a state per step would be a
    recorder row per step."""

    async def run() -> list[int]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        shown: list[int] = []
        coord.async_add_address_listener(LAMP, lambda: shown.append(coord.values[LAMP]))
        await coord.async_fade(LAMP, 0, 80, 0.05)
        assert coord.values[LAMP] == 80, "the target must show at once"
        await _settle(coord)
        return shown

    assert asyncio.run(run()) == [80]


def test_dimmers_fading_together_share_one_packet() -> None:
    """A scene fades its lights together, not one after another."""

    async def run() -> list[list[tuple[str, int]]]:
        unit = _Unit({LAMP_HEX: 0, OTHER_HEX: 100})
        coord = _coordinator(unit)
        await asyncio.gather(
            coord.async_fade(LAMP, 0, 100, 0.3),
            coord.async_fade(OTHER, 100, 0, 0.3),
        )
        await _settle(coord)
        return unit.packets

    packets = asyncio.run(run())
    together = [p for p in packets if {a for a, _ in p} == {LAMP_HEX, OTHER_HEX}]
    # Not every packet: a dimmer whose rounded level did not move this step is
    # rightly not written.  But stepped apart, none would be together.
    assert len(together) > len(packets) / 2, f"stepped apart: {packets}"


def test_a_slow_fade_still_answers_at_once() -> None:
    """The first step goes out straight away, so a long fade does not hold the
    service call -- and a unit that is not listening is found out at once."""

    async def run() -> float:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        loop = asyncio.get_running_loop()
        began = loop.time()
        await coord.async_fade(LAMP, 0, 1, 30.0)
        took = loop.time() - began
        assert unit.written(LAMP_HEX), "nothing was sent"
        coord._async_stop_fade(LAMP)
        return took

    assert asyncio.run(run()) < 0.5


def test_a_fade_starts_from_the_output_not_from_a_stale_display() -> None:
    """What showed was out of date; the first read says where it really is, and
    the fade goes from there instead of flashing down to the old level."""

    async def run() -> list[int]:
        unit = _Unit({LAMP_HEX: 40})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 0.05)
        await _settle(coord)
        return unit.written(LAMP_HEX)

    steps = asyncio.run(run())
    assert min(steps) > 40, f"the fade flashed down to the stale level: {steps}"
    assert steps[-1] == 100


@pytest.mark.parametrize("lag", [1, 2, 4])
def test_a_register_a_cycle_behind_is_not_taken_for_the_wall(lag, monkeypatch) -> None:
    """Found on a live unit: a write shows in the register about 100 ms after
    it is acknowledged -- a step later -- and the first fade stopped itself
    there, reading its own last step as missing and the old level as someone
    else's.  Some steps behind must still be fine."""
    # The confirmation of the arrived level comes long after any lag, as live:
    # held off here, then run by hand once the last steps have landed.
    monkeypatch.setattr(coordinator_module, "WRITE_VERIFY_DELAY", 60)

    async def run() -> tuple[list[int], int]:
        unit = _Unit({LAMP_HEX: 0}, lag=lag)
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 0.05)
        while coord._fades:
            await asyncio.sleep(0.001)
        for _ in range(lag + 1):  # let the last steps land
            await unit.async_get_many([LAMP_HEX])
        for task in coord.config_entry.tasks:
            task.cancel()
        monkeypatch.setattr(coordinator_module, "WRITE_VERIFY_DELAY", 0)
        await coord._async_confirm_write(LAMP, 100)
        return unit.written(LAMP_HEX), coord.values[LAMP]

    steps, shown = asyncio.run(run())
    assert steps[-1] == 100, f"the fade stopped part-way: {steps[-5:]}"
    assert shown == 100


def test_without_reads_a_fade_simply_steps() -> None:
    """A unit that will not answer reads has nothing to check against."""

    async def run() -> tuple[list[int], int]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        coord.reads_supported = False
        await coord.async_fade(LAMP, 0, 100, 0.05)
        await _settle(coord)
        return unit.written(LAMP_HEX), len(unit.reads)

    steps, reads = asyncio.run(run())
    assert steps[-1] == 100
    # Only the ordinary confirmation of the arrived level -- none per step.
    assert reads <= 1, f"{reads} reads for a unit that does not answer them"


# --- other writers -----------------------------------------------------------


def test_a_command_stops_a_fade() -> None:
    """An instant level beats a fade still running, and the fade sends no more."""

    async def run() -> tuple[list[int], int]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 5.0)
        await asyncio.sleep(0.01)
        await coord.async_command(LAMP, 30)
        sent = len(unit.written(LAMP_HEX))
        await asyncio.sleep(0.05)
        await _settle(coord)
        assert len(unit.written(LAMP_HEX)) == sent, "the fade kept writing"
        return unit.written(LAMP_HEX), coord.values[LAMP]

    steps, shown = asyncio.run(run())
    assert steps[-1] == 30
    assert shown == 30


def test_a_new_fade_starts_where_the_output_actually_is() -> None:
    """Reversing mid-way starts from the level reached, not from the target
    that was showing -- or the lamp would flash up to it first."""

    async def run() -> tuple[int, list[int]]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 2.0)
        await asyncio.sleep(0.2)
        reached = coord.fade_level(LAMP)
        before = len(unit.written(LAMP_HEX))
        await coord.async_fade(LAMP, reached, 0, 0.05)
        await _settle(coord)
        return reached, unit.written(LAMP_HEX)[before:]

    reached, after = asyncio.run(run())
    assert 0 < reached < 100
    assert max(after) <= reached, f"the reversal flashed up first: {after}"
    assert after[-1] == 0


def test_someone_else_moving_it_without_a_word_still_stops_the_fade() -> None:
    """Found on a live unit: a change the next step overwrites within its cycle
    is never reported at all.  The register is checked before every step."""

    async def run() -> tuple[int, list[int]]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 90, 5.0)
        await asyncio.sleep(0.02)
        unit.register[LAMP_HEX] = 10  # someone else, and no event
        sent = len(unit.written(LAMP_HEX))
        await asyncio.sleep(0.05)
        await _settle(coord)
        return coord.values[LAMP], unit.written(LAMP_HEX)[sent:]

    shown, after = asyncio.run(run())
    assert after == [], f"the fade wrote over someone else's change: {after}"
    assert shown == 10


def test_the_fades_own_echoes_are_not_mistaken_for_a_wall_switch() -> None:
    async def run() -> None:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 1.0)
        await asyncio.sleep(0.05)
        coord.handle_event(LAMP, unit.written(LAMP_HEX)[-1])
        assert coord.values[LAMP] == 100, "a step's echo replaced the target"
        assert LAMP in coord._fades, "a step's echo stopped the fade"
        coord._async_stop_fade(LAMP)

    asyncio.run(run())


def test_someone_at_the_wall_wins_over_a_fade() -> None:
    """A level the fade never sent, reported, is someone else's: it stops."""

    async def run() -> tuple[int, list[int]]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 5.0)
        await asyncio.sleep(0.02)
        unit.register[LAMP_HEX] = 77
        coord.handle_event(LAMP, 77)
        sent = len(unit.written(LAMP_HEX))
        await asyncio.sleep(0.05)
        return coord.values[LAMP], unit.written(LAMP_HEX)[sent:]

    shown, more = asyncio.run(run())
    assert shown == 77
    assert set(more) <= {77}, f"the fade carried on over the wall switch: {more}"


def test_a_level_the_fade_already_heard_back_is_someone_else() -> None:
    """Echoes come back in order, so a level already heard back, turning up
    again, is somebody dimming at the wall -- not the fade's own."""

    async def run() -> tuple[int, int, list[int]]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 90, 5.0)
        await asyncio.sleep(0.3)
        steps = unit.written(LAMP_HEX)
        for level in steps:  # every step echoed, in order, duplicates and all
            coord.handle_event(LAMP, level)
            coord.handle_event(LAMP, level)
        assert LAMP in coord._fades, "the fade's own echoes stopped it"
        unit.register[LAMP_HEX] = steps[1]
        coord.handle_event(LAMP, steps[1])  # an early level, back again
        sent = len(unit.written(LAMP_HEX))
        await asyncio.sleep(0.05)
        return steps[1], coord.values[LAMP], unit.written(LAMP_HEX)[sent:]

    theirs, shown, more = asyncio.run(run())
    assert more == [], f"the fade carried on over the wall: {more}"
    assert shown == theirs


def test_a_change_a_step_undid_is_put_back() -> None:
    """The event that tells of someone else's change trails by a step or two,
    and a step sent meanwhile lands on top of it.  Steps still unheard when
    their change is heard are exactly those, so the fade puts it back."""

    async def run() -> tuple[int, list[int]]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 90, 5.0)
        await asyncio.sleep(0.3)  # steps sent, none echoed yet
        coord.handle_event(LAMP, 10)  # theirs -- already overwritten
        await _settle(coord)
        return coord.values[LAMP], unit.written(LAMP_HEX)

    shown, steps = asyncio.run(run())
    assert steps[-1] == 10, f"their level was not put back: {steps[-4:]}"
    assert shown == 10


def test_echoes_the_unit_never_sent_do_not_confuse_the_fade() -> None:
    """The unit may skip echoing a step; the next one is still recognised."""

    async def run() -> bool:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 90, 5.0)
        await asyncio.sleep(0.3)
        coord.handle_event(LAMP, unit.written(LAMP_HEX)[-1])  # earlier ones lost
        alive = LAMP in coord._fades
        coord._async_stop_fade(LAMP)
        return alive

    assert asyncio.run(run())


def test_switching_off_at_the_wall_during_a_fade_up_is_not_overridden() -> None:
    """The classic software-fade failure: the light is switched off by hand and
    the fade turns it back on.  The level a fade started from must not count
    as one of its own echoes."""

    async def run() -> tuple[int, list[int], list[int]]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 5.0)
        await asyncio.sleep(0.01)
        before = unit.written(LAMP_HEX)
        unit.register[LAMP_HEX] = 0  # off, at the wall
        coord.handle_event(LAMP, 0)
        await asyncio.sleep(0.05)
        return coord.values[LAMP], before, unit.written(LAMP_HEX)[len(before):]

    shown, steps, after = asyncio.run(run())
    assert 0 not in steps, f"the fade re-sent the level it started from: {steps}"
    assert shown == 0
    assert set(after) <= {0}, f"the fade turned the light back on: {after}"


# --- reads and confirmations -------------------------------------------------


def test_a_refresh_caught_mid_fade_does_not_show_a_step() -> None:
    async def run() -> int:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 1.0)
        await coord._async_seed([LAMP_HEX])  # the register holds a step
        shown = coord.values[LAMP]
        coord._async_stop_fade(LAMP)
        return shown

    assert asyncio.run(run()) == 100


def test_a_read_back_due_from_before_does_not_disturb_a_fade() -> None:
    """A write's confirmation still pending when a fade takes the address over
    would read a step and 'correct' the target to it."""

    async def run() -> int:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        coord.async_note_write(LAMP, 0)
        await coord.async_fade(LAMP, 0, 100, 1.0)
        await asyncio.sleep(0.01)
        await coord._async_confirm_write(LAMP, 0)
        shown = coord.values[LAMP]
        coord._async_stop_fade(LAMP)
        return shown

    assert asyncio.run(run()) == 100


def test_a_fade_the_unit_will_not_take_is_reported_and_put_back() -> None:
    """Whoever asked hears about it, and the shown target gives way to the real
    level once it is read."""

    async def run() -> int:
        unit = _Unit({LAMP_HEX: 0}, fail_writes=True)
        coord = _coordinator(unit)
        with pytest.raises(Is3Error):
            await coord.async_fade(LAMP, 0, 100, 1.0)
        await _settle(coord)
        return coord.values[LAMP]

    assert asyncio.run(run()) == 0


def test_a_late_echo_after_the_fade_does_not_undo_it(monkeypatch) -> None:
    """Once there, the target is an ordinary written value, settled against
    the echoes of the steps still on their way."""
    monkeypatch.setattr(coordinator_module, "WRITE_VERIFY_DELAY", 1.5)  # as live

    async def run() -> int:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 60, 0.02)
        while coord._fades:
            await asyncio.sleep(0.001)
        coord.handle_event(LAMP, unit.written(LAMP_HEX)[0])
        shown = coord.values[LAMP]
        for task in coord.config_entry.tasks:
            task.cancel()
        return shown

    assert asyncio.run(run()) == 60


def test_stopping_the_engine_releases_whoever_waits() -> None:
    """Unloading cancels the stepping task; nobody may hang on it."""

    async def run() -> None:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 5.0)
        coord._fade_task.cancel()
        await asyncio.sleep(0.01)
        assert not coord._fades

    asyncio.run(run())


# --- found in review ---------------------------------------------------------
#
# Each of these fails if the fix it guards is undone; several were shown to
# pass against a broken version before they were written this way.


def _echo_all(coord: Is3Coordinator, unit: _Unit, upto: int | None = None) -> None:
    """Deliver the echo of every step written so far (or of the first ``upto``)."""
    for level in unit.written(LAMP_HEX)[:upto]:
        coord.handle_event(LAMP, level)


def test_a_reversal_right_after_a_fade_arrives_reaches_its_target() -> None:
    """The fade that has just arrived still has echoes on their way.  Thrown
    away with it, the next fade took the first late one for someone at the
    wall, stopped, and wrote that stale level back: off was asked for, and the
    light stayed at 91 %."""

    async def run() -> tuple[int, list[int]]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 0.05)
        while coord._fades:
            await asyncio.sleep(0)
        steps = unit.written(LAMP_HEX)
        _echo_all(coord, unit, upto=len(steps) - 3)  # the last three still out
        before = len(steps)
        await coord.async_fade(LAMP, 100, 0, 0.1)  # straight back, at once
        coord.handle_event(LAMP, steps[-3])  # the first late echo lands
        assert LAMP in coord._fades, "the old fade's late echo stopped the new one"
        await _settle(coord)
        return coord.values[LAMP], unit.written(LAMP_HEX)[before:]

    shown, after = asyncio.run(run())
    assert after[-1] == 0 and shown == 0, f"left at {shown}: {after[-4:]}"
    assert after == sorted(after, reverse=True), f"a stale level went back: {after}"


def test_a_fade_after_a_command_knows_the_echoes_of_both() -> None:
    """A fade, stopped by a level set at once, then a new fade: the stopped
    fade's late echoes and the command's are all the integration's own."""

    async def run() -> tuple[int, bool]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 5.0)
        await asyncio.sleep(0.05)
        old = unit.written(LAMP_HEX)
        await coord.async_command(LAMP, 30)
        await coord.async_fade(LAMP, 30, 80, 5.0)
        coord.handle_event(LAMP, old[-1])  # the stopped fade's last step
        coord.handle_event(LAMP, 30)  # the command
        alive = LAMP in coord._fades
        coord._async_stop_fade(LAMP)
        return coord.values[LAMP], alive

    shown, alive = asyncio.run(run())
    assert alive, "a late echo of our own stopped the fade"
    assert shown == 80


def test_a_commands_echo_does_not_stop_the_fade_that_follows() -> None:
    async def run() -> bool:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_command(LAMP, 100)
        await coord.async_fade(LAMP, 100, 0, 5.0)
        await asyncio.sleep(0.02)
        coord.handle_event(LAMP, 100)
        alive = LAMP in coord._fades
        coord._async_stop_fade(LAMP)
        return alive

    assert asyncio.run(run()), "the command's own echo stopped the fade after it"


def test_the_replaced_fades_last_step_does_not_stop_the_reversal() -> None:
    async def run() -> bool:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 5.0)
        await asyncio.sleep(0.05)
        last = unit.written(LAMP_HEX)[-1]
        await coord.async_fade(LAMP, coord.fade_level(LAMP), 0, 5.0)
        coord.handle_event(LAMP, last)
        alive = LAMP in coord._fades
        coord._async_stop_fade(LAMP)
        return alive

    assert asyncio.run(run()), "the replaced fade's last echo stopped the reversal"


def test_an_echo_ahead_of_its_acknowledgement_is_still_the_fades_own() -> None:
    """A lost reply is resent; meanwhile the unit has applied the step and
    echoed it.  Recognised only once acknowledged, that echo stopped the fade
    part-way, silently."""

    async def run() -> tuple[int, list[int]]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        unit.before_ack = lambda writes: [coord.handle_event(LAMP, v) for _, v in writes]
        await coord.async_fade(LAMP, 0, 100, 0.2)
        await _settle(coord)
        return coord.values[LAMP], unit.written(LAMP_HEX)

    shown, steps = asyncio.run(run())
    assert steps[-1] == 100 and shown == 100, f"stopped part-way at {steps[-1]}"


def test_a_fade_up_by_one_never_goes_back_off() -> None:
    """0 -> 1 %: the first step is the whole way, and the schedule, still a
    step behind, sent 0 next -- on, off, and on again a second later."""

    async def run() -> list[int]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 1, 0.2)
        await _settle(coord)
        return unit.written(LAMP_HEX)

    steps = asyncio.run(run())
    assert 0 not in steps, f"the light went off again on the way to 1 %: {steps}"


def test_a_command_stops_a_fade_behind_a_lagging_register() -> None:
    """With the register a step behind, the command is not in it yet when the
    fade next looks -- so only stopping the fade keeps it from writing over."""

    async def run() -> tuple[list[int], list[int]]:
        unit = _Unit({LAMP_HEX: 0}, lag=2)
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 0.5)  # a step every few ms
        await asyncio.sleep(0.05)
        await coord.async_command(LAMP, 30)
        sent = len(unit.written(LAMP_HEX))
        await asyncio.sleep(0.1)
        for task in coord.config_entry.tasks:
            task.cancel()
        return unit.written(LAMP_HEX)[:sent], unit.written(LAMP_HEX)[sent:]

    before, after = asyncio.run(run())
    assert len(before) > 3, "the fade had not got going; this proves nothing"
    assert after == [], f"the fade wrote over the command: {after}"


def test_a_fade_stopped_while_its_check_is_out_sends_nothing() -> None:
    async def run() -> list[int]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 5.0)
        release = unit.hold_next_read()  # the fade's next check
        while unit._held is not None:
            await asyncio.sleep(0)
        sent = len(unit.written(LAMP_HEX))
        coord._async_stop_fade(LAMP)  # as a command does
        release.set_result(None)
        await asyncio.sleep(0.02)
        return unit.written(LAMP_HEX)[sent:]

    assert asyncio.run(run()) == [], "a stopped fade still sent its step"


def test_without_reads_a_fade_stopped_meanwhile_is_not_stepped(monkeypatch) -> None:
    """Where nothing is read before a step, a fade stopped while the previous
    packet was out must still be left out of the next one."""
    monkeypatch.setattr(coordinator_module, "FADE_BATCH", 1)  # a packet each

    async def run() -> list[int]:
        unit = _Unit({LAMP_HEX: 0, OTHER_HEX: 0})
        coord = _coordinator(unit)
        coord.reads_supported = False
        # Both stepping every tick, LAMP's packet first.
        await coord.async_fade(LAMP, 0, 100, 0.5)
        await coord.async_fade(OTHER, 0, 100, 0.5)
        await asyncio.sleep(0.05)

        stopped_at: list[int] = []

        def stop_other(writes) -> None:
            if not stopped_at and any(a == LAMP_HEX for a, _ in writes):
                coord._async_stop_fade(OTHER)  # while LAMP's packet is out
                stopped_at.append(len(unit.packets))

        unit.before_ack = stop_other
        await asyncio.sleep(0.1)
        unit.before_ack = None
        for task in coord.config_entry.tasks:
            task.cancel()
        coord._async_stop_fade(LAMP)
        assert stopped_at, "LAMP was never stepped; this proves nothing"
        return [v for p in unit.packets[stopped_at[0]:] for a, v in p if a == OTHER_HEX]

    assert asyncio.run(run()) == [], "a stopped fade was stepped after all"


def test_a_confirmation_out_when_a_fade_begins_does_not_undo_it() -> None:
    async def run() -> int:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        coord.async_note_write(LAMP, 0)
        release = unit.hold_next_read()
        confirm = create_eager_task(coord._async_confirm_write(LAMP, 0))
        while unit._held is not None:
            await asyncio.sleep(0)
        await coord.async_fade(LAMP, 0, 100, 5.0)
        release.set_result(None)
        await confirm
        shown = coord.values[LAMP]
        coord._async_stop_fade(LAMP)
        return shown

    assert asyncio.run(run()) == 100, "a read from before the fade replaced its target"


def test_a_read_caught_mid_fade_and_answered_after_it_does_not_undo_it() -> None:
    """A refresh read out while the fade runs, answered once it has arrived:
    the arrival is the newer word, even though it changed nothing showing."""

    async def run() -> int:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 0.05)
        await asyncio.sleep(0.01)
        release = unit.hold_next_read()
        seed = create_eager_task(coord._async_seed([LAMP_HEX]))  # holds a step
        while coord._fades:
            await asyncio.sleep(0)
        release.set_result(None)
        await seed
        shown = coord.values[LAMP]
        for task in coord.config_entry.tasks:
            task.cancel()
        return shown

    assert asyncio.run(run()) == 100


def test_a_stale_value_over_a_write_is_put_right_by_the_confirmation() -> None:
    async def run() -> int:
        unit = _Unit({LAMP_HEX: 60})
        coord = _coordinator(unit)
        coord.async_note_write(LAMP, 60)
        coord._async_store(LAMP, 12)  # something stale landed on top
        await coord._async_confirm_write(LAMP, 60)
        return coord.values[LAMP]

    assert asyncio.run(run()) == 60


def test_a_wall_level_heard_back_before_is_noticed_from_the_event_alone() -> None:
    """The register shows the fade's own step -- theirs was overwritten -- so
    only the event tells, and only the order of the echoes can read it."""

    async def run() -> bool:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 90, 5.0)
        await asyncio.sleep(0.3)
        steps = unit.written(LAMP_HEX)
        assert len(steps) > 2
        _echo_all(coord, unit)
        coord.handle_event(LAMP, steps[1])  # an early level, back again
        stopped = LAMP not in coord._fades
        coord._async_stop_fade(LAMP)
        for task in coord.config_entry.tasks:
            task.cancel()
        return stopped

    assert asyncio.run(run()), "a level already heard back was taken for an echo"


def test_a_level_of_our_own_is_never_written_back() -> None:
    """An echo of ours that cannot be placed -- a duplicate out of order --
    stops the fade like anyone else's change would.  But writing it back, as is
    done for a change a step undid, would put a stale level of our own on the
    light: it must only be read back."""

    async def run() -> tuple[int, list[int]]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 90, 5.0)
        await asyncio.sleep(0.3)
        steps = unit.written(LAMP_HEX)
        assert len(steps) > 3
        _echo_all(coord, unit, upto=len(steps) - 2)  # the last two still out
        stale = steps[len(steps) - 4]
        sent = len(unit.written(LAMP_HEX))  # before: what follows starts at once
        coord.handle_event(LAMP, stale)  # heard before, now again
        await _settle(coord)
        return stale, unit.written(LAMP_HEX)[sent:]

    stale, after = asyncio.run(run())
    assert stale not in after, f"our own stale level {stale} was written back: {after}"


def test_a_first_step_refused_by_a_unit_gone_quiet_puts_back_what_showed() -> None:
    async def run() -> int:
        unit = _Unit({LAMP_HEX: 0}, fail_writes=True, fail_reads=True)
        coord = _coordinator(unit)
        coord._async_store(LAMP, 25)
        with pytest.raises(Is3Error):
            await coord.async_fade(LAMP, 25, 100, 1.0)
        await _settle(coord)
        return coord.values[LAMP]

    assert asyncio.run(run()) == 25, "the refused fade's target stayed showing"


def test_a_command_refused_after_stopping_a_fade_shows_where_it_stopped() -> None:
    async def run() -> tuple[int, int]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 5.0)
        await asyncio.sleep(0.03)
        reached = coord.fade_level(LAMP)
        unit.fail_writes = True
        with pytest.raises(Is3Error):
            await coord.async_command(LAMP, 30)
        return coord.values[LAMP], reached

    shown, reached = asyncio.run(run())
    assert shown == reached, f"showing {shown}, but the output stopped at {reached}"


# --- found in the second review ----------------------------------------------


@pytest.mark.parametrize("lag", [1, 2, 4])
@pytest.mark.parametrize("after", [0.05, 0.1, 0.17])
def test_a_reversal_behind_a_lagging_register_goes_all_the_way(lag, after, monkeypatch) -> None:
    """The new fade starts from the old one's last acknowledged step while the
    next is still landing.  Seeing its own starting level in the register used
    to clear that landing step too -- which then looked like someone else's,
    and stopped the reversal part-way: off was asked for, 22 % was left."""
    monkeypatch.setattr(coordinator_module, "WRITE_VERIFY_DELAY", 60)

    async def run() -> list[int]:
        unit = _Unit({LAMP_HEX: 0}, lag=lag)
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 0.5)
        await asyncio.sleep(after)
        await coord.async_fade(LAMP, coord.fade_level(LAMP), 0, 0.2)
        while coord._fades:
            await asyncio.sleep(0.001)
        for task in coord.config_entry.tasks:
            task.cancel()
        return unit.written(LAMP_HEX)

    steps = asyncio.run(run())
    assert steps[-1] == 0, f"the reversal stopped part-way at {steps[-1]}: {steps[-6:]}"


def test_an_old_register_reading_does_not_make_a_fade_start_from_it(monkeypatch) -> None:
    """A dimmer faded long ago keeps what the register last said.  A fade
    right after a plain command -- the command not in the register yet --
    took that old reading for the truth and started from it: the light came
    on, dropped to 1 % and crept up, instead of fading down from 100."""
    monkeypatch.setattr(coordinator_module, "WRITE_SETTLE", 0.05)  # "long ago" quickly

    async def run() -> list[int]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 24, 0.02)  # leaves a reading of 24
        await _settle(coord)
        await coord.async_command(LAMP, 0)
        await asyncio.sleep(0.2)  # the reading is old now
        unit.lag = 2  # and the next write is not in the register at once
        before = len(unit.written(LAMP_HEX))
        await coord.async_command(LAMP, 100)
        await coord.async_fade(LAMP, 100, 30, 0.2)
        await _settle(coord)
        return unit.written(LAMP_HEX)[before:]

    steps = asyncio.run(run())
    assert steps[0] == 100
    assert min(steps[1:4]) > 50, f"the fade started from an old reading: {steps[:6]}"


def test_a_step_held_up_behind_someone_elses_lost_reply_is_still_ours(monkeypatch) -> None:
    """The step is noted before its write; the write can then wait seconds on
    the client behind another request's lost reply.  Dated from the note, it
    looked long overdue by the time it went, and the fade stopped itself."""
    monkeypatch.setattr(coordinator_module, "WRITE_SETTLE", 0.05)

    async def run() -> list[int]:
        unit = _Unit({LAMP_HEX: 0}, lag=1)
        coord = _coordinator(unit)
        original = unit.async_set_many
        held = []

        async def slow_once(writes):
            if not held and len(unit.written(LAMP_HEX)) == 5:
                held.append(True)
                await asyncio.sleep(0.1)  # longer than WRITE_SETTLE
            await original(writes)

        unit.async_set_many = slow_once
        await coord.async_fade(LAMP, 0, 100, 0.3)
        await _settle(coord)
        assert held, "the hold-up never happened; this proves nothing"
        return unit.written(LAMP_HEX)

    steps = asyncio.run(run())
    assert steps[-1] == 100, f"the fade stopped itself at {steps[-1]}"


def test_a_refused_reversal_shows_where_the_output_stopped() -> None:
    """Refused at its first step, a fade puts back what showed before it --
    and when it replaced a fade, that is where the old one got to, not the
    target the old one was showing and never reached."""

    async def run() -> tuple[int, int]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_fade(LAMP, 0, 100, 5.0)
        await asyncio.sleep(0.05)
        reached = coord.fade_level(LAMP)
        unit.fail_writes = unit.fail_reads = True
        with pytest.raises(Is3Error):
            await coord.async_fade(LAMP, reached, 0, 2.0)
        await _settle(coord)
        return coord.values[LAMP], reached

    shown, reached = asyncio.run(run())
    assert shown == reached, f"showing {shown}, but the output stopped at {reached}"


def test_a_plain_level_is_remembered_even_on_a_dimmer_never_faded() -> None:
    """The unit now and then echoes a level twice.  Set at once on a dimmer
    with no fade behind it, then faded a moment later, the second echo of
    that level stopped the fade and was written back."""

    async def run() -> tuple[bool, int]:
        unit = _Unit({LAMP_HEX: 0})
        coord = _coordinator(unit)
        await coord.async_command(LAMP, 30)
        coord.handle_event(LAMP, 30)  # its echo
        await coord.async_fade(LAMP, 30, 80, 5.0)
        await asyncio.sleep(0.02)
        coord.handle_event(LAMP, 30)  # and the same echo again
        alive = LAMP in coord._fades
        coord._async_stop_fade(LAMP)
        for task in coord.config_entry.tasks:
            task.cancel()
        return alive, coord.values[LAMP]

    alive, shown = asyncio.run(run())
    assert alive, "a repeated echo of our own level stopped the fade"
    assert shown == 80
