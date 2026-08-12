"""Replace a configured unit's export, inside a real Home Assistant.

Uploading a new export is how an installation on newer firmware gets at new
devices at all -- that firmware serves no export over HTTP, so the file is
picked by hand in the reconfigure dialog.  It is also the one path the unit
tests cannot reach: it runs through Home Assistant's file-upload store, the
config-flow machinery and an entry reload, none of which exist outside a real
instance.  Everything that went wrong with it in the field went wrong here.

    python tools/ha_reconfigure.py 192.168.1.10
    python tools/ha_reconfigure.py 192.168.1.10 --export saved.is3 --password secret

The unit is only ever read from.  The export it is asked for is fetched over
HTTP unless ``--export`` names a file, which is what a unit on newer firmware
needs.  Everything happens in a throwaway config folder; the installation's own
Home Assistant is not touched.

Nothing that identifies the installation is printed.
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import REPO, boot, make_config_dir, stage_upload  # noqa: E402

ARGS = argparse.ArgumentParser(description="Exercise the export-replacement path")
ARGS.add_argument("host", help="the central unit's address")
ARGS.add_argument("--port", type=int, default=9999, help="the unit's control port")
ARGS.add_argument("--password", default="", help="the unit's password, if one is set")
ARGS.add_argument(
    "--export",
    default="",
    help="an export file to start from; without it the unit is asked over HTTP",
)
ARGS.add_argument(
    "--new-export",
    default="",
    help="the export to upload; without it one is made up from the current one",
)
OPTS = ARGS.parse_args()

from homeassistant import config_entries  # noqa: E402
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from homeassistant.helpers.aiohttp_client import async_get_clientsession  # noqa: E402

DOMAIN = "is3_export"
CONF_EXPORT_FILE = "export_file"
CONF_EXPORT_UPLOAD = "export_upload"

PASS = "OK"
FAIL = "FAILED"
failures = 0


def check(label: str, ok: bool, detail: str = "", on_fail: str = "") -> None:
    """Record one assertion without stopping the run: later steps still inform."""
    global failures
    failures += not ok
    note = detail or (on_fail if not ok else "")
    print(f"   [{PASS if ok else FAIL}] {label}{f' -- {note}' if note else ''}")


async def load_export_text(hass) -> str:
    """The unit's current export, as text: from a file, or from the unit itself."""
    if OPTS.export:
        return Path(OPTS.export).read_text(encoding="utf-8-sig")
    session = async_get_clientsession(hass)
    response = await session.get(f"http://{OPTS.host}/immfiles/export.is3", timeout=15)
    response.raise_for_status()
    return (await response.read()).decode("utf-8-sig", errors="replace")


def republish(text: str, drop: str) -> tuple[str, str]:
    """Make the export look like a project that was republished from IDM3.

    Three things change, because all three are things a real republish does and
    each one has its own way of going wrong: the installation id in the header
    (which is where the entry's identity comes from), an entry that has gone
    away, and an entry that is new.  Returns the text and the new id.
    """
    new_id = "REPUBLISHED1"
    # Header values contain "-" but never "_", so this cannot run past the field.
    text = re.sub(r"(_ID_)[^_]+", r"\g<1>" + new_id, text, count=1)

    lines = text.splitlines()
    relays = [
        i
        for i, line in enumerate(lines)
        if re.search(r"\b0x0102[0-9A-Fa-f]{4}\b", line)
    ]
    if not relays:
        raise SystemExit("This export has no relays to rearrange; nothing to test")

    # New: a copy of a real relay line, at an address no project uses, under a
    # name of our own.  Cloning a line rather than writing one keeps the file's
    # exact shape, whatever that shape happens to be.
    template = lines[relays[0]]
    added = re.sub(r"\b0x0102[0-9A-Fa-f]{4}\b", "0x0102FF01", template, count=1)
    added = "Sv_reconfigure_probe" + added[len(added.split()[0]) :]

    # Gone: an entry Home Assistant is currently showing, so its disappearance
    # is something that can actually be looked for afterwards.  Picking "the
    # last relay in the file" instead once chose a channel that was registered
    # disabled, and proved nothing.
    kept = [line for line in lines if not re.search(rf"\b{drop}\b", line, re.I)]
    if len(kept) == len(lines):
        raise SystemExit(f"Could not find {drop} in the export to remove it")
    kept.append(added)
    return "\n".join(kept) + "\n", new_id


def addresses_in(text: str) -> set[str]:
    """Every address an export names, upper-cased for comparing."""
    return {a.upper() for a in re.findall(r"\b0x[0-9A-Fa-f]{8}\b", text)}


def header_id(text: str) -> str:
    """The installation id out of an export's header line."""
    found = re.search(r"_ID_([^_]+)", text.splitlines()[0] if text else "")
    return found.group(1) if found else ""


def a_droppable_address(hass, before: set[str]) -> str:
    """The address behind an entity that is on screen right now."""
    for entity_id in sorted(before):
        state = hass.states.get(entity_id)
        address = (state.attributes.get("address") or "") if state else ""
        # Relays only: a light or switch is one export line, where a heating
        # zone or a blind is several and removing one line rearranges the rest.
        if address.lower().startswith("0x0102"):
            return address
    raise SystemExit("No relay-backed entity to remove; nothing to test")


def entity_ids(hass, entry) -> set[str]:
    """Every entity Home Assistant currently shows for this entry."""
    registry = er.async_get(hass)
    registered = {
        e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    return {s.entity_id for s in hass.states.async_all()} & registered


async def reconfigure(hass, entry, user_input: dict) -> dict:
    """Drive the reconfigure dialog exactly as the front end would."""
    flow = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert flow["type"] is FlowResultType.FORM, flow
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], user_input)
    await hass.async_block_till_done()
    return result


def base_input() -> dict:
    """What the form opens on for this unit."""
    return {
        CONF_HOST: OPTS.host,
        CONF_PORT: OPTS.port,
        CONF_PASSWORD: OPTS.password,
        CONF_EXPORT_FILE: OPTS.export,
    }


async def main() -> int:
    config_dir = make_config_dir()
    print(f"config dir: {config_dir}")
    hass = await boot(config_dir)

    print("\n=== SETUP ===")
    entry = config_entries.ConfigEntry(
        version=2,
        minor_version=1,
        domain=DOMAIN,
        title="Live unit",
        data=base_input(),
        options={},
        source="user",
        unique_id="ORIGINAL-ID",
        discovery_keys={},
        subentries_data=(),
    )
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    check("entry set up", entry.state is config_entries.ConfigEntryState.LOADED,
          str(entry.reason or entry.state))
    if entry.state is not config_entries.ConfigEntryState.LOADED:
        await hass.async_stop()
        return 1

    before = entity_ids(hass, entry)
    print(f"   {len(before)} entities, unique_id={entry.unique_id}")

    original_text = await load_export_text(hass)
    if OPTS.new_export:
        # A real pair of exports from one installation, which is worth more than
        # anything that can be made up: it carries whatever the configuration
        # software actually wrote, including whatever it was that would not go in.
        new_text = Path(OPTS.new_export).read_text(encoding="utf-8-sig")
        new_id = header_id(new_text) or entry.unique_id
        print("   uploading the export given on the command line")
    else:
        dropped = a_droppable_address(hass, before)
        new_text, new_id = republish(original_text, dropped)
        print(f"   removing {dropped} from the export, adding 0x0102FF01")

    print("\n=== REPUBLISHED PROJECT, UPLOADED ===")
    result = await reconfigure(
        hass,
        entry,
        base_input() | {CONF_EXPORT_UPLOAD: await stage_upload(hass, new_text)},
    )
    check(
        "the form accepted it",
        result["type"] is FlowResultType.ABORT
        and result["reason"] == "reconfigure_successful",
        f"{result['type']}: {result.get('reason') or result.get('errors')}",
    )
    check("the new installation id was adopted", entry.unique_id == new_id,
          f"unique_id={entry.unique_id}")

    saved = Path(entry.data.get(CONF_EXPORT_FILE, ""))
    check("the upload was saved as a file the entry points at",
          bool(entry.data.get(CONF_EXPORT_FILE)) and saved.is_file(), str(saved))
    check("the entry came back up",
          entry.state is config_entries.ConfigEntryState.LOADED, str(entry.reason))

    after = entity_ids(hass, entry)
    added, gone = after - before, before - after
    print(f"   {len(before)} entities -> {len(after)}  (+{len(added)} -{len(gone)})")
    check("the export brought entities with it", bool(added or gone),
          f"{len(added)} appeared, {len(gone)} disappeared")
    if not OPTS.new_export:
        check("the entity that is new in the export appeared",
              any("reconfigure_probe" in e for e in added))
        check("the entity that left the export went away", len(gone) == 1)

    print("\n=== A REJECTED SUBMIT MUST NOT LOSE THE UPLOAD ===")
    # Home Assistant deletes an uploaded file the moment it is read, so a form
    # that comes back with an error has nothing left to submit -- and the file
    # path field is still pre-filled with the *previous* export.  Anyone who
    # corrects the password and presses submit again would then quietly
    # reconfigure onto the old export and wonder where the new devices went.
    flow = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    # A different address, not just a different name: an entity is identified by
    # its address, so renaming one only relabels the entity that is already
    # there and would prove nothing about which export was read.  With a real
    # pair of exports, going back to the first one is that same difference.
    second_text = (
        original_text if OPTS.new_export else new_text.replace("0x0102FF01", "0x0102FF02")
    )
    # Addresses the export just uploaded has and the one being submitted now
    # does not: if any of them still has an entity afterwards, the form read the
    # wrong file.
    only_in_first = addresses_in(new_text) - addresses_in(second_text)
    rejected = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        base_input()
        | {
            CONF_HOST: "192.0.2.1",  # reserved for documentation: nothing answers
            CONF_EXPORT_FILE: str(saved),
            CONF_EXPORT_UPLOAD: await stage_upload(hass, second_text),
        },
    )
    check("an unreachable unit re-shows the form",
          rejected["type"] is FlowResultType.FORM, str(rejected.get("reason")))
    print(f"   errors: {rejected.get('errors')}")

    retried = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        base_input() | {CONF_EXPORT_FILE: str(saved)},
    )
    await hass.async_block_till_done()
    check("the corrected submit went through",
          retried["type"] is FlowResultType.ABORT
          and retried.get("reason") == "reconfigure_successful",
          f"{retried['type']}: {retried.get('reason') or retried.get('errors')}")
    live = {
        (hass.states.get(e).attributes.get("address") or "").upper()
        for e in entity_ids(hass, entry)
    }
    leftover = only_in_first & live
    check("it used the export that was uploaded, not the one on disk",
          not leftover,
          on_fail=f"{len(leftover)} address(es) only the previous export had are "
                  "still here: the upload was silently discarded")

    print("\n=== UNLOAD ===")
    ok = await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    check("unloaded", ok, str(entry.state))

    await hass.async_stop()
    print(f"\nFAILURES: {failures}")
    return 1 if failures else 0


sys.exit(asyncio.run(main()))
