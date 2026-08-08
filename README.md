<p align="center"><img src="brands/logo.png" alt="IS3 · vlios.cz" width="360"></p>

# IS3 Export

[![hacs][hacs-badge]][hacs] [![Validate](https://github.com/vlioscz/is3-export/actions/workflows/validate.yaml/badge.svg)](https://github.com/vlioscz/is3-export/actions/workflows/validate.yaml)

**English** · [Česky](README.cs.md)

UNOFFICIAL Home Assistant integration for **iNELS central units** (ELKO EP). It
talks to the unit directly over **UDP port 9999** — the same port the unit's own
configuration software connects to — so it **needs no Connection Server**.

> The "IS3" in the name is **iNELS3** — the `.is3` export format the integration
> is based on.

**Nothing has to be enabled on the unit.** On every unit tested it answered on
that port as it came, and pushed changes on its own without any event
configuration — there is no port to open and nothing to switch on in IDM3.

The device list comes from the unit's `.is3` export: downloaded straight from
units that serve it over HTTP, or saved out of IDM3 and dropped into the setup
form for those that don't — **the newer units serve no export over HTTP**
(confirmed on **CU3-07M** and **CU3-08M**), so there you upload it once. State is then tracked live from the unit's own events,
with everything re-read once every 30 s on top.

> **Status: experimental.** Read [What has been tested](#what-has-been-tested)
> before assuming your unit is covered: part of the range is verified on live
> hardware, part of it is only expected to behave the same. Covers report an
> **assumed state** — no position feedback; see [Limitations](#limitations).

How the protocols were established, and what this software is and isn't:
[NOTICE.md](NOTICE.md).

## What has been tested

| Unit | What was actually tested |
| --- | --- |
| **CU3-01M** (oldest generation) | **reading** verified |
| a classic **CU3-0x** — the reference installation, IDM3 **03-04-19** | **everything**: reads, writes, events, heating, dimmers, buttons |
| **CU3-07M**, IDM3 **03-05-03** | **reads and writes** verified |
| **CU3-08M** | only what 0.1.x needed: it serves **no export over HTTP**, and never opened the port 0.1.x used. **Its port 9999 has not been tested.** |
| **CU3-09M**, **CU3-10M** | **never tested at all.** They are believed to behave like the 07M/08M, because it is the same firmware family — but that is an expectation, not a result. |

The export parser is a separate matter, and has a wider base: exports from
several installations, 17 to 1125 items, written by IDM3 **03-03-34** through
**03-05-03**.

If you run a unit that isn't in this table, the useful thing you can do is say
what happened — either way. That is how the table grows.

## Firmware updates can change this

The protocol this integration speaks was recovered by **observation, not from a
specification**. Nothing about it is promised to stay put, and a unit firmware
update can change it with no warning.

- It is **verified against units running IDM3 03-04-19 and 03-05-03**. Other
  versions are untested — which is not the same as known-broken.
- If an update does change the wire format, the integration may stop working.
  The log will say so: the client **warns once, loudly**, when a unit sends
  datagrams whose opening bytes it does not recognise, and names what it got
  instead.
- There is a tool for exactly this question, **new in this release**:

```bash
python tools/compat_check.py 192.168.1.10 --save before.json
# ... update the unit's firmware ...
python tools/compat_check.py 192.168.1.10 --compare before.json
```

`compat_check.py` fingerprints every assumption the integration makes — the
packet header, the checksum, the shape of each reply, the value encodings, the
export format, and the unit's own table of protocol versions — and reports which
of them moved, each with a plain sentence about what that change costs you. It
**only reads**, and it prints nothing that identifies the installation (no device
names, no project name), so the output is safe to paste into an issue.

Run it **before** a firmware update and keep the file. It is worth far more
before than after.

## How state stays in sync

The unit pushes every change it makes — a relay flipped from the wall, a new
temperature reading, a button press — with nothing ticked anywhere to ask for
it.

Commands from Home Assistant show up immediately, and the integration then
**verifies them by reading back**: if the output didn't take, or a switch on the
wall flipped it in the meantime, the state corrects itself to reality instead of
leaving the icon stuck in the wrong state. Measured on the author's unit, the
unit acknowledges a write in **4 ms**, and its own push event for that write
arrives **0.13 s** later.

The **device list** follows the unit too. Each cycle the unit is asked for a
digest of the project loaded in it — one packet — and the export is fetched
again only when that digest changes, which is exactly when the installer
republishes from IDM3. So a republished project shows up within a cycle instead
of within half an hour, and the rest of the time nothing is downloaded at all.

On top of the events, the integration **re-reads every readable address on every
30-second cycle**. It can afford to: reading the whole installation — **313
readable addresses** — takes **0.13 s**. So an address whose events stop
arriving is not stuck; it is back in step within one cycle, without anything
having to know in advance which addresses are at risk.

**Buttons are left out of that re-read.** A button has no state worth restoring —
it is a moment, not a value — and re-reading one would only risk replaying a
press nobody made.

## Installation

It's in the **HACS default store**: open **HACS**, search **IS3 Export**, and
**Download** it — or use this one-click button:

[![Add repository to HACS][hacs-badge-btn]][hacs-add]

Then **restart Home Assistant** and add the integration:

[![Add integration][config-badge]][config-add]

Manually: copy `custom_components/is3_export` into `config/custom_components/`.

## Configuration

| Field | Description | Default |
| --- | --- | --- |
| Host | The unit's IP address | — |
| Port | UDP. Change it only if the unit is reached through a tunnel or a forwarded port. | `9999` |
| Central unit password | the password set on the unit in IDM3; **leave empty if none is set**, which is the usual case | empty |
| Export file path | leave empty, it downloads from the unit | empty |
| Export file upload | for units that serve no HTTP export — drop the `.is3` saved from IDM3 here; it is kept under `config/is3_export/` | — |

The integration's name is taken from the export header.

**The password is for the unit, not for the export.** The unit's web server
serves the export as a static file with no login, so **the iNELS project
password has no effect on its availability**. (If some unit blocks the download
anyway, upload the export or enter the path to a locally downloaded one.)

Any of it can be corrected later without removing the integration: **Settings →
Devices & services → IS3 Export → ⋮ → Reconfigure**.

## Upgrading from 0.1.x

0.2.0 replaces the transport: everything now goes over **UDP port 9999**.
**Existing installations upgrade in place** — entity ids, areas and history are
kept, and nothing has to be set up again.

- The **port** is rewritten to `9999`, whatever was stored before.
- Two connection settings the old transport needed are **gone**, along with the
  repair issue that used to complain about them.
- If the central unit **has a password** set in IDM3, Home Assistant raises its
  usual re-authentication dialog and asks for it. If none is set, nothing is
  asked.
- **One thing worth doing by hand: untick *Third part setting* in IDM3.** That
  setting lives in the unit, not in Home Assistant, so nothing here can turn it
  off for you — and while it is on the unit keeps a door open that takes no
  password and that this integration no longer uses.

Reading the whole installation — **313 readable addresses** — takes **0.13 s**,
and counter addresses (`0x0206` — water and electricity meters) report the
unit's real totals.

**Rolling back to 0.1.x** means deleting the integration and adding it again
(entity ids, areas and history go with it), so take a backup first if you want
that door left open.

## Removing the integration

**Settings → Devices & services → IS3 Export → ⋮ → Delete.** This closes the
connection and removes every entity and device the integration created. Nothing
is left behind on the central unit — the integration only ever talked to it over
the network — and no files are written outside `custom_components/is3_export`.

## Which addresses become entities

The second byte of the address determines the type:

| Address | Meaning | Entity | Write |
| --- | --- | --- | --- |
| `0x01`**`02`** | relay | `switch` | ✅ |
| `0x01`**`04`** | dimmer (with `%` unit) | `light` 0–100 % | ✅ |
| `0x02`**`03`** | SYSTEMBIT | `switch` | ✅ |
| `0x02`**`02`** | SYSTEMINTEGER | `number` | ✅ |
| address pair | blind | `cover` | ✅ |
| controller channels | heating zone | `climate` | ✅ |
| `0x01`**`01`** | inputs, buttons, controller status outputs | `binary_sensor` | ❌ |
| `0x01`**`07`** | module faults | `binary_sensor` (problem) | ❌ |
| `0x01`**`05`** | temperature / humidity | `sensor` | ❌ |
| `0x01`**`08`** | analog input (a `Light-IN` input reads illuminance / lux) | `sensor` | ❌ |
| `0x01`**`03`**, `0x01`**`11`**, `0x01`**`12`** | controller channels | `sensor` | ❌ |
| `0x02`**`06`** | water meters, electricity meters | `sensor` (total) | ❌ |
| `0x05`**`01`**, `0x02`**`04`**, `0x02`**`09`**, `0x0003` | plans, groups, schedules | — | ❌ |

Writes happen **only where a write is documented**. Never to inputs, thermostat
channels, or plans.

The **hardware ID** decides, not the name: anything starting with `Controller_`,
`Heat-Regulator_` or `Cool-Regulator_` is controller/regulator internals and is
not written to — a window sensor sits in the same range as a relay. Conversely,
an unnamed relay (`_ SA3-04M_RE2_…`) is still a relay.

### Names refine the entity type

The address says what the output is; the name says what it's for. A pulse and a
lamp are the same as far as the address is concerned.

| In the name | Entity | Icon |
| --- | --- | --- |
| `imp` | `button` | — |
| `sv` | `light` | light bulb |
| `lamp` | `light` | floor lamp |
| `zrc` | `light` | mirror |
| `LED` | `light` | LED strip |
| `vent` | `switch` | fan |
| `zas` | `switch` | socket |
| `TL` (or `DIN` input) | `event` (`press` + `long_press`) | — |

`sv`, `imp`, `vent`, `zas`, and `TL` must match as a whole token (otherwise
`Svod_vody` would be a light and `Zastineni` a socket); `lamp`, `zrc`, and `LED`
are enough as a prefix.

**`TL_`** (button) makes an `event` button on **any** module. A **`DIN`** input
is a button on **wall controllers** and on the **central unit itself** (In-Out);
on other modules (e.g. the `IM3` input module) `DIN` is a plain `binary_sensor`
(a maintained contact) until you name it `TL_`. A wired button distinguishes
both `press` and `long_press` (see [Wall switches](#wall-switches-wsb3)).

A button (`imp`) sends a **pulse** on press — the bit to `1` and straight back
to `0`. The idle state is always `0`, so every next press is again a clean
rising edge that the iNELS program reacts to. (Holding `1` would work only once;
the unit doesn't zero the bit itself.)
It splits on `_` and `-`, and case doesn't matter. The more specific one wins:
`imp_sv_chodba` is a button.

The light and switch conventions (`sv`, `lamp`, `zrc`, `LED`, `vent`, `imp`)
apply **only to physical relays/dimmers** and never turn anything into a
writable entity — an input named `Sv_okno` stays a `binary_sensor`, and the
system bit `blok_noc_lamp` stays a switch. Conversely, `TL`/`DIN` apply **only
to digital inputs** (they won't make a button out of a relay).

There are deliberately no more rules. If something comes out differently,
override the entity type or icon manually in Home Assistant.

### Blinds

They combine several addresses into one `cover` entity, from three possible
sources:

1. **System bits of the blind program** (`0x0203`) — up, down, stop, tilt. The
   program in the unit drives the contacts and stops the blind itself. Preferred
   where present.
2. **A pair of JA3 relays** — direction is in the hardware ID
   (`JA3-018M_Up1` / `Down1`); stop by releasing both.
3. **A plain relay pair** (an `SA3` module, including the `SA3-02B` box) — two
   relays on one motor, interlocked in hardware. Here the direction is in the
   **name**, not the hardware ID: an `UP`/`DOWN` token anywhere in the name — a
   suffix (`Roleta_loznice_UP`) or in the middle (`…_UP_…`) — pairs the two
   halves that share **the same module**.

A bare relay won't release itself, so a **relay blind (forms 2 and 3 — a JA3
pair works the same way, its interlock is just wired on the module's board)**
gets a **travel-time** `number` entity (default 30 s): after a move the
integration releases the relay once that time elapses, so **set it to the
blind's real run time** (a touch more) and it reaches the end stop first.
Reversing first releases the opposite direction, waits briefly, then drives —
the module interlocks the two directions in hardware.

A name is weak evidence, so one pairing is deliberately refused: **two outputs
that each belong to a different heating zone are left as two switches**, not
joined into a blind. An upstairs and a downstairs zone carry `up` and `down` in
their names for reasons of their own, and a wrong blind would drive real relays —
turning one room's heating on and the other's off.

Neither reports its position, so a cover carries an **assumed state**. Addresses
taken by a blind no longer appear as switches.

### Heating zones

A heating controller is a set of channels with the same serial suffix plus a
named root `<name> Controller_<serial>`. Together they form one `climate` entity:

| | Channel |
| --- | --- |
| current temperature | `Actual-Therm-AOUT` |
| target temperature | `Required-Therm-AOUT` (heating) / `Required-Cool-Therm-AOUT` (cooling) |
| heats / cools | `Required-Heat-DOUT` / `Required-Cool-DOUT` |
| preset | `Control-Manual-IN` — 0 Schedule, 1–4 Preset 1–4, **7 Manual** |
| heating / cooling | `Control-HC-IN` — 0 heating, 1 cooling |
| on / off | `Control-IN` — 0 off, 1 on |

The **Cool** mode is offered for a zone **only when it actually has a cooling
output wired**. The cooling channels (`Control-HC-IN`, `Required-Cool-*`) are
carried by *every* zone, so their presence isn't enough — the capability is only
recognized from the controller's root row: a heating zone has flags `0x05` with
empty cooling schedule slots, a zone with cooling `0x3F` with filled ones
(verified on the unit). Where Cool is available, it's switched via
`Control-HC-IN`, and cooling has its own setpoints: `Required-Cool-Therm-AOUT`
(in effect) and `Manual-Cool-Therm-AIN` (manual).

Setting the temperature switches the zone into Manual and writes
`Manual-Therm-AIN` (heating), or `Manual-Cool-Therm-AIN` (cooling). Preset
values 1–4 and the weekly plan behind Schedule (`HEATCOOL_WEEK`) are set on the
unit.

Watch out for one pitfall (handled): writing the setpoint **immediately** after
switching into Manual corrupts it — the value drops below frost protection
(~0.1 °C), and the heating relay with it, and the zone stops heating. So after
switching, the integration **waits**, then writes the setpoint and **verifies it
by reading back**, repeating the write if needed. Manual is value **7**, not 5 —
a five drops the zone to frost protection.

Each zone also has a `select` **plan** — Normal / Vacation / Holiday
(`Control-Plan-IN` 0 / 64 / 128, all verified on a live unit). Holiday is a
**daily** program (`HEATCOOL_DAY`) and must be configured on the unit; where it
isn't, the switch doesn't take and the read-back squares the plan back in the UI.

### Wall switches (WSB3)

A single switch breaks down into one entity per channel — nothing is
special-cased, it follows from the address type:

| Type | Breakdown into entities |
| --- | --- |
| **WSB3-20** | 8 — 2 buttons (up/down) + 2 LEDs (green/red) + 2 temperatures + 2 digital inputs |
| **WSB3-40** | 12 — 4 buttons + 4 LEDs + 2 temperatures + 2 digital inputs |
| **WSB3-*-Hum** | +2 — humidity (`%`, `device_class humidity`) and dew point (°C) |

The indicator **LEDs** (roles `Green`/`Red`) are switches with a **G**/**R**
icon — it's recognized from the role, so even unnamed ones (`_`) get it.

Buttons (Up/Down/DIN) are an **`event` entity**. Wired switches (WSB) distinguish
a **short `press` and `long_press`**; the buttons of an **RF controller** report
only `press`.

The same recognition applies to the **whole family of wall controllers** —
besides `WSB3`, also the glass/touch `GSB3`, `GSP3`, `MSB3`, `GBP3`, `GRT3`, the
card readers `GMR3`/`GCR3`/`GHR3`/`GCH3`, the info panels `GDB3`, `WMR3`, and the
room controller `IDRT3` (all wired → `press`+`long_press`). The **RFKEY** remote
is all buttons (only `press`). **`IBWL`** (RF input module) is different — each
of its inputs mirrors a paired RF device (a button, but also a door/motion
sensor), which we can't tell from the export, so it's a `binary_sensor` by
default; to make a particular input a `press`, name it `TL_`. A proximity sensor
and a card reader are not treated as buttons.

**How short/long works:** telling them apart needs the hold duration = the gap
between the close (`=1`) and the open (`=0`). On a wired switch this gap is clean
and consistent — taps fall under ~100 ms, deliberate holds over ~1.5 s, with a
wide empty gap in between. So on the close the integration starts a timer: if
the open arrives first, it's a short `press`; when the timer (**1.5 s**, the same
as long-press in iNELS) runs out and the button is **still held**, it's a
`long_press` — it fires **right at that moment, without waiting for release**, so
the long-press action kicks in on time. A lost open won't stick the button — a
safety timer releases it.

Short/long rests on the events arriving when they happen, so anything that
delays them smears the hold duration — see
[Other things talking to the unit](#other-things-talking-to-the-unit).

**RF controllers stay on `press` only** — their open is lost too often, and the
hold duration there isn't reliable. For them `press` fires on **every close
event**; buttons are meanwhile **not deduplicated** (the integration normally
wakes an entity only on a value change) so that a lost open doesn't hide the next
press — otherwise the next press would be "no change" and get discarded (hence
the earlier "I have to press 3×"). A short debounce (~0.5 s) swallows only an
immediate double-send of the same press.

> Sensors, by contrast, are **throttled** (max ~1 notification/s) so that a
> chatty analog input of the CU doesn't flood the loop — the value keeps being
> stored, only the state isn't written constantly. This keeps button handling
> snappy.

The RF controller's battery status is a plain `binary_sensor` (battery), not a
button.

### Split into devices

Every **physical module** (by the serial number in the hardware ID) is its **own
device** in HA, nested under the central unit. So the channels of one switch,
relay board, or dimmer stay together — you can tell which `Green1` belongs to
which switch. System things (bits, integers, buttons) have no module and remain
directly on the central unit.

### Hidden by default

Large installations export hundreds of panel internals — button contacts,
indicator LEDs, fault flags. Entities are created from them, but they are
**disabled by default**. You enable them in the integration settings. Unnamed
ones get their name from the role in the hardware ID (e.g. `Up`, `Green`), not
from the whole ID.

Also disabled are the **`SW` status inputs** of relays and the **fault/alert
flags** (`OUF-Alert`, type `0x0107`) — **even when they're named**, because
hardly anyone watches them. An alert has `device_class problem` and is
diagnostic.

### RF devices

A device on an RF module (e.g. `RFKEY` — remotes) appears as its own device.
Its buttons are `event` entities firing `press` (RF reports no hold, so there is
no `long_press`).

> **An RF button held down fires `press` more than once.** The module re-sends
> "down" about every 1.5 s while the button is held, and each of those is a
> press as far as anything here can tell — a two-second hold measured on a
> live `RFKEY` produced two. A wired button does not do this. If an automation
> on an RF button must not run twice, give it a cooldown (`mode: single` with
> `max_exceeded: silent`, or a condition on the last trigger).

The `Battery_LOW` battery status is a `binary_sensor` with
`device_class battery`. An **`IBWL`** input is the exception: it mirrors whatever
RF device is paired to it — a button or a door contact, indistinguishable in the
export — so it stays a `binary_sensor` unless you name it `TL_`.

### What's in the export

The export is **not** a list of everything — in IDM3 you choose what goes into
it. If something's missing in Home Assistant, add it there and republish: the
integration notices the project changed on the next cycle and reloads itself
(see [How state stays in sync](#how-state-stays-in-sync)). **Reload** does it
immediately.

### Values

Temperatures and humidities come in **multiplied by a hundred** — 2550 means
25.50 °C. Dimmers are already in percent. `SYSTEMINTEGER` is a **raw value** that
isn't converted in any way; what it means is up to the program that uses it.
**Counters** (`0x0206`) report the unit's real totals.

## ⚠️ Security

**The unit's authorization is not a real barrier.** It accepts an **empty
password** by default, which is how most units are left — so anyone who reaches
the unit on the network can control the entire installation. Setting a password
on the unit in IDM3 raises the bar; nothing about it is encrypted either way.

Keep the unit on a separate VLAN, or at least firewall it off from untrusted
devices and from the internet.

## Limitations

- **Covers report no position.** A cover shows an assumed state
  (open / closed / moving), not a percentage; a relay blind's end stop is
  inferred from the configured travel time, not measured.
- **Scenes can't be triggered** — a read on them returns no value, and writing is unverified.
- **The binary `.otc` / `.cld` formats are not read.** They additionally contain named scenes.
- **Nothing is encrypted** — neither the HTTP export nor the port 9999 traffic.

## Other things talking to the unit

**Measured:** the unit's **configuration software connected at the same time
costs almost nothing**. With it connected, a single read still took a median of
**4 ms** and the whole installation **179 ms** (against 130 ms with it gone),
writes were acknowledged in **8 ms**, and the event stream ran throughout. So you
can leave Home Assistant running while you work on the project.

**Not measured:** how this behaves alongside a running **iNELS Connection
Server**. It is expected to be fine — the Connection Server reaches a unit over
this same port for its own traffic, and a configuration client on that port
demonstrably causes no trouble — but nobody has measured it. If you run one,
please open an issue with what you see; that is the measurement nobody has yet.

## Diagnostics

The central unit gets a **Unit status** sensor (diagnostic): whether the unit
reports itself as *running*, *running fast* or **stopped**. A stopped unit still
answers on the network and still holds its last values, so without this
everything looks normal in Home Assistant while nothing in the building
responds.

Its `unit_clock` attribute is the unit's **own** date and time. The unit runs
its heating schedules off that clock, so if it disagrees with real time —
a whole hour is the usual case, when the unit is left on winter time — your
heating switches at the wrong hour and nothing in Home Assistant would otherwise
say why.

**Download diagnostics** from the integration (its **⋮** menu) for a redacted
snapshot — the config, the unit's capabilities, and every entry with its live
value and how it was classified. It's the quickest thing to attach to a bug
report; the host and any credentials are masked.

If the integration won't set up at all, there is nothing to download
diagnostics from. `tools/probe_is3.py` answers the same questions from outside
Home Assistant — whether the unit responds, whether it wants a password,
whether its data plane opens, and whether it pushes events:

```bash
python tools/probe_is3.py 192.168.1.10
```

It only prints addresses and values, never device names, so the output is safe
to paste into an issue. It is read-only unless you pass `--write`.

And before touching a unit's firmware, `tools/compat_check.py` records what the
integration depends on so you can tell afterwards what moved — see
[Firmware updates can change this](#firmware-updates-can-change-this).

## Development

```bash
pip install -r requirements-test.txt
pytest
```

On Windows read [CONTRIBUTING.md](CONTRIBUTING.md) first — it saves an
afternoon (Python 3.13, a short venv path, and an `lru-dict` wheel trick).

The integration talks to the unit on **UDP port 9999**, the port its
configuration software uses. The other way into a unit is **XML-RPC** on the
iNELS Connection Server (port 7801), which this integration doesn't use; for
that route there's
[InelsForHass](https://github.com/JH-Soft-Technology/InelsForHass).

How the protocols were established, and the limits that come with that:
[NOTICE.md](NOTICE.md).

## License

[MIT](LICENSE)

[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Default-41BDF5.svg

<!-- My Home Assistant redirects: these resolve against whatever instance the
     reader is signed in to, so no address of anyone's Home Assistant appears
     here. -->
[hacs-add]: https://my.home-assistant.io/redirect/hacs_repository/?owner=vlioscz&repository=is3-export&category=integration
[hacs-badge-btn]: https://my.home-assistant.io/badges/hacs_repository.svg
[config-add]: https://my.home-assistant.io/redirect/config_flow_start/?domain=is3_export
[config-badge]: https://my.home-assistant.io/badges/config_flow_start.svg
