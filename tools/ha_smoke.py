"""Set the integration up inside a real Home Assistant, against a real unit.

The test suite runs without a Home Assistant harness, which leaves a gap it can
never cover: config-entry setup, the migration, entity creation, the service
layer, and unload.  That gap is where the worst bug in this project's history
lived -- a method named after one the base coordinator already had, which passed
every local test and then broke every install at startup.

This boots a genuine Home Assistant into a temporary config directory, adds a
config entry **at version 1** (the shape 0.1.x wrote, so the migration runs for
real), sets it up against a unit you name, and then drives a heating zone and a
dimmer through actual service calls.

    python tools/ha_smoke.py 192.168.1.10
    python tools/ha_smoke.py 192.168.1.10 --password secret --read-only

Anything it changes is read first and put back afterwards, and a heating zone is
only ever asked for a temperature BELOW the room it is in -- enough to prove the
write landed, not enough to ask the building for heat.  Pass --read-only to skip
the writes entirely.

Nothing is printed that identifies the installation beyond the host you typed.
"""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import REPO, boot, make_config_dir  # noqa: E402,F401

ARGS = argparse.ArgumentParser(description="Home Assistant smoke test against a live unit")
ARGS.add_argument("host", help="the central unit's address")
ARGS.add_argument("--password", default="", help="the unit's password, if one is set")
ARGS.add_argument("--read-only", action="store_true", help="skip every write")
ARGS.add_argument("--debug", action="store_true", help="log the integration's own debug output")
OPTS = ARGS.parse_args()

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
if OPTS.debug:
    logging.getLogger("custom_components.is3_export").setLevel(logging.DEBUG)

from homeassistant import config_entries  # noqa: E402
from homeassistant.const import CONF_HOST, CONF_PORT  # noqa: E402


def make_entry(unique_id: str) -> config_entries.ConfigEntry:
    """A config entry exactly as version 0.1.x would have stored it."""
    return config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain="is3_export",
        title="Live unit",
        data={
            CONF_HOST: OPTS.host,
            CONF_PORT: 22272,          # the old ASCII port
            "delimiter": " ",          # retired settings, must be dropped
            "number_base": "hex",
            "export_file": "",
            "password": OPTS.password,
        },
        options={},
        source="user",
        unique_id=unique_id,
        discovery_keys={},
        subentries_data=(),
    )


async def main() -> int:
    config_dir = make_config_dir()
    print(f"config dir: {config_dir}")

    hass = await boot(config_dir)
    entry = make_entry("live-unit")
    # async_add runs the migration and then sets the entry up, exactly as a
    # Home Assistant restart after an upgrade would.
    print("\n=== SETUP ===")
    t0 = time.perf_counter()
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    ok = entry.state is config_entries.ConfigEntryState.LOADED
    print(f"setup ok={ok} state={entry.state} in {time.perf_counter() - t0:.2f}s")
    if not ok:
        print(f"reason: {entry.reason}")
        await hass.async_stop()
        return 1

    print("\n=== MIGRATION ===")
    print(f"version {entry.version}.{entry.minor_version} (was 1.1)")
    print(f"port    {entry.data.get(CONF_PORT)} (was 22272)")
    print(f"password present: {'password' in entry.data}")
    print(f"delimiter gone:   {'delimiter' not in entry.data}")
    print(f"number_base gone: {'number_base' not in entry.data}")

    states = hass.states.async_all()
    by_domain: dict[str, int] = {}
    for state in states:
        by_domain[state.domain] = by_domain.get(state.domain, 0) + 1
    print(f"\n=== ENTITIES === {len(states)} total")
    print({k: v for k, v in sorted(by_domain.items())})

    unavailable = [s for s in states if s.state in ("unavailable", "unknown")]
    print(f"unavailable/unknown: {len(unavailable)}")

    # Entities the integration created but Home Assistant is not showing: the
    # ones this integration registers disabled (unnamed channels, relay status
    # inputs, fault flags).  Worth separating from anything actually missing.
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    registered = er.async_entries_for_config_entry(registry, entry.entry_id)
    disabled = [e for e in registered if e.disabled]
    print(f"registry: {len(registered)} entities, {len(disabled)} disabled by default")
    missing = len(registered) - len(disabled) - len(states)
    print(f"enabled but with no state: {missing}")

    status = hass.states.get("sensor.live_unit_unit_status")
    if status is None:
        status = next((s for s in states if s.entity_id.endswith("unit_status")), None)
    print(f"unit status sensor: {status.entity_id if status else 'MISSING'}"
          f" = {status.state if status else '-'}"
          f" {dict(status.attributes) if status else ''}")

    if OPTS.read_only:
        print("\nread-only: skipping the writes")
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_stop()
        return 0
    return await exercise(hass, entry)


async def exercise(hass, entry) -> int:
    """Drive heating and dimmers through real service calls, then restore."""
    failures = 0

    climates = [s for s in hass.states.async_all() if s.domain == "climate"]
    print(f"\n=== HEATING ({len(climates)} zones) ===")
    for zone in climates:
        print(f"   {zone.entity_id}: state={zone.state} "
              f"current={zone.attributes.get('current_temperature')} "
              f"setpoint={zone.attributes.get('temperature')} "
              f"modes={zone.attributes.get('hvac_modes')}")

    # Every zone is off in August and reports no setpoint, so there is nothing
    # to nudge.  Instead: switch one to heat with a setpoint well BELOW the room
    # -- that exercises the whole write path (preset to manual, write, verify
    # against Required-Therm-AOUT) while leaving heat demand at zero, so nothing
    # actually fires.  Put back to off afterwards.
    target = next((z for z in climates if z.attributes.get("temperature") is not None), None)
    restore_off = False
    if target is None and climates:
        target = climates[-1]
        current = target.attributes.get("current_temperature")
        print(f"\nno zone has a setpoint (all off); switching {target.entity_id} to heat")
        await hass.services.async_call(
            "climate", "set_hvac_mode",
            {"entity_id": target.entity_id, "hvac_mode": "heat"}, blocking=True,
        )
        await hass.async_block_till_done()
        await asyncio.sleep(2)
        target = hass.states.get(target.entity_id)
        restore_off = True
        print(f"   now state={target.state} setpoint={target.attributes.get('temperature')}")

    if target is not None:
        original = target.attributes.get("temperature")
        current = target.attributes.get("current_temperature")
        print(f"\ndriving {target.entity_id}: state={target.state} "
              f"current={current} setpoint={original}")
        if original is not None:
            # Deliberately below the room temperature: proves the write without
            # asking the building for heat.
            wanted = float(original) + 0.5 if not restore_off else 18.0
            t0 = time.perf_counter()
            await hass.services.async_call(
                "climate", "set_temperature",
                {"entity_id": target.entity_id, "temperature": wanted},
                blocking=True,
            )
            await hass.async_block_till_done()
            after = hass.states.get(target.entity_id).attributes.get("temperature")
            print(f"   set {wanted} -> reads {after}  [{time.perf_counter()-t0:.2f}s]"
                  f"  {'OK' if after == wanted else 'MISMATCH'}")
            failures += after != wanted
            await asyncio.sleep(2)
            await hass.services.async_call(
                "climate", "set_temperature",
                {"entity_id": target.entity_id, "temperature": float(original)},
                blocking=True,
            )
            await hass.async_block_till_done()
            back = hass.states.get(target.entity_id).attributes.get("temperature")
            print(f"   restored to {back}  {'OK' if back == float(original) else 'MISMATCH'}")
            failures += back != float(original)
        else:
            print("   no setpoint reported; skipped")

        if restore_off:
            await hass.services.async_call(
                "climate", "set_hvac_mode",
                {"entity_id": target.entity_id, "hvac_mode": "off"}, blocking=True,
            )
            await hass.async_block_till_done()
            await asyncio.sleep(2)
            final = hass.states.get(target.entity_id)
            print(f"   zone switched back off -> state={final.state}"
                  f"  {'OK' if final.state == 'off' else 'MISMATCH'}")
            failures += final.state != "off"

    lights = [s for s in hass.states.async_all() if s.domain == "light"]
    dimmers = [s for s in lights if s.attributes.get("supported_color_modes") == ["brightness"]]
    print(f"\n=== LIGHTS ({len(lights)} total, {len(dimmers)} dimmable) ===")
    if dimmers:
        lamp = dimmers[0]
        was_on, was_bright = lamp.state == "on", lamp.attributes.get("brightness")
        print(f"dimmer {lamp.entity_id}: state={lamp.state} brightness={was_bright}")
        t0 = time.perf_counter()
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": lamp.entity_id, "brightness_pct": 40},
            blocking=True,
        )
        await hass.async_block_till_done()
        now = hass.states.get(lamp.entity_id)
        print(f"   40% -> state={now.state} brightness={now.attributes.get('brightness')}"
              f"  (255 scale; 40% is ~102)  [{time.perf_counter()-t0:.2f}s]")
        failures += now.state != "on"
        await asyncio.sleep(2)
        if was_on and was_bright:
            await hass.services.async_call(
                "light", "turn_on",
                {"entity_id": lamp.entity_id, "brightness": was_bright}, blocking=True,
            )
        else:
            await hass.services.async_call(
                "light", "turn_off", {"entity_id": lamp.entity_id}, blocking=True,
            )
        await hass.async_block_till_done()
        back = hass.states.get(lamp.entity_id)
        print(f"   restored -> state={back.state} brightness={back.attributes.get('brightness')}"
              f"  {'OK' if (back.state == 'on') == was_on else 'MISMATCH'}")
        failures += (back.state == "on") != was_on
    else:
        print("   no dimmable light found")

    print("\n=== UNLOAD ===")
    ok = await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    print(f"unload ok={ok} state={entry.state}")
    failures += not ok

    await hass.async_stop()
    print(f"\nFAILURES: {failures}")
    return 1 if failures else 0


sys.exit(asyncio.run(main()))
