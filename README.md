<p align="center"><img src="brands/logo.png" alt="IS3 · vlios.cz" width="360"></p>

# IS3 Export

[![hacs][hacs-badge]][hacs] [![Validate](https://github.com/vlioscz/is3-export/actions/workflows/validate.yaml/badge.svg)](https://github.com/vlioscz/is3-export/actions/workflows/validate.yaml)

**English** · [Česky](README.cs.md)

UNOFFICIAL Home Assistant integration for **iNELS central units** (ELKO EP). It
talks to the unit directly over **UDP port 9999** — the same port the unit's own
configuration software connects to — so it **needs no Connection Server**. The
"IS3" in the name is **iNELS3**, the `.is3` export format it is based on.

**Nothing has to be enabled on the unit.** Every unit tested answered on that
port as it came and pushed changes on its own — no port to open, nothing to
switch on in IDM3.

The device list comes from the unit's `.is3` export: downloaded straight from
units that serve it over HTTP, or saved out of IDM3 and dropped into the setup
form for those that don't — **the newer units serve no export over HTTP**
(confirmed on a **CU3-08M**), so there you upload it once.

> **Status: experimental.** Read [What has been tested](#what-has-been-tested)
> before assuming your unit is covered — part of the range is verified on live
> hardware, part only expected to behave the same. Covers report an **assumed
> state**, no position feedback; see [Limitations](#limitations).

How the protocols were established, and what this software is and isn't:
[NOTICE.md](NOTICE.md).

## What has been tested

This table is about one thing: whether the device list can be **imported
automatically**, or has to be saved from IDM3 and uploaded once. Everything
else — reads, writes, events, heating, dimmers, buttons — is verified on the
reference installation, a classic CU3-0x on IDM3 03-04-19.

| Unit | IDM3 | Automatic import |
| --- | --- | --- |
| **CU3-01M**, **CU3-02M** (identical to each other) | 03-03-34, 03-04-19 | ✅ tested, works |
| **CU3-08M** | 03-05-03 | ❌ tested, does not — upload the export |
| **CU3-07M**, **CU3-09M**, **CU3-10M** | — | expected to behave like the 08M; **not tested** |

The export parser has a wider base: exports from several installations, 17 to
1125 items, written by IDM3 03-03-34 through 03-05-03. If you run a unit that
isn't in this table, saying what happened — either way — is how it grows.

## Firmware updates can change this

What the integration speaks was recovered by **observation, not from a
specification**, so a firmware update can change it with no warning.

- **Verified against units running IDM3 03-04-19 and 03-05-03.** Other versions
  are untested — which is not the same as known-broken.
- If an update breaks the wire format, the client **warns once, loudly** in the
  log and names what it got instead.
- Run `compat_check.py` **before** an update and keep the file — it fingerprints
  every assumption the integration makes and afterwards says which of them moved
  and what that costs you. Read-only, and it prints nothing that identifies the
  installation, so the output is safe to paste into an issue.

```bash
python tools/compat_check.py 192.168.1.10 --save before.json
# ... update the unit's firmware ...
python tools/compat_check.py 192.168.1.10 --compare before.json
```

## How state stays in sync

The unit pushes every change it makes — a relay flipped from the wall, a new
temperature reading, a button press — with nothing ticked anywhere to ask for it.
Commands from Home Assistant are **verified by reading back**: if the output
didn't take, or a wall switch flipped it in the meantime, the state corrects
itself instead of leaving the icon stuck. The unit acknowledges a write in
**4 ms**, and its own push event for that write arrives **0.13 s** later.

On top of the events, every readable address is **re-read on a 30-second
cycle** — reading the whole installation (**313 readable addresses**) takes
**0.13 s** — so an address whose events stop arriving is back in step within one
cycle. **Buttons are left out of that re-read**, or it would replay a press
nobody made.

The **device list** follows too: each cycle the unit is asked for a digest of the
project loaded in it, and the export is re-fetched only when that digest changes
— that is, when the installer republishes from IDM3.

## Installation

It's in the **HACS default store**: open **HACS**, search **IS3 Export**,
**Download** it, **restart Home Assistant**, then add the integration. The two
buttons do the same in one click each:

[![Add repository to HACS][hacs-badge-btn]][hacs-add] [![Add integration][config-badge]][config-add]

Manually: copy `custom_components/is3_export` into `config/custom_components/`.

## Configuration

| Field | Description | Default |
| --- | --- | --- |
| Host | The unit's IP address | — |
| Port | UDP. Change it only if the unit is reached through a tunnel or a forwarded port. | `9999` |
| Central unit password | the password set on the unit in IDM3; **leave empty if none is set**, which is the usual case | empty |
| Export file path | leave empty, it downloads from the unit | empty |
| Export file upload | for units that serve no HTTP export — drop the `.is3` saved from IDM3 here; it is kept under `config/is3_export/` | — |

The integration's name comes from the export header. **The password is for the
unit, not for the export** — the unit serves the export as a static file with no
login; if one blocks the download anyway, upload the export or give the path to
a locally downloaded one.

Correct any of it later under **Settings → Devices & services → IS3 Export → ⋮ →
Reconfigure**. The same menu → **Delete** removes every entity and device it
created; nothing is left behind on the unit, and no files outside
`custom_components/is3_export`.

## Upgrading from 0.1.x

0.2.0 replaces the transport: everything now goes over **UDP port 9999**.
**Existing installations upgrade in place**, keeping entity ids, areas and
history.

- The **port** is rewritten to `9999`, whatever was stored before.
- Two connection settings the old transport needed are **gone**, along with the
  repair issue that complained about them.
- If the unit **has a password** set in IDM3, Home Assistant raises its usual
  re-authentication dialog. If none is set, nothing is asked.
- **Worth doing by hand: untick *Third part setting* in IDM3.** That setting
  lives in the unit, so nothing here can turn it off for you — and while it is
  on, the unit keeps a door open that takes no password and that this
  integration no longer uses.

**Rolling back to 0.1.x** means deleting the integration and adding it again —
entity ids, areas and history go with it, so take a backup first if you want
that door left open.

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

Writes happen **only where a write is documented** — never to inputs, thermostat
channels, or plans. The **hardware ID** decides, not the name: anything starting
with `Controller_`, `Heat-Regulator_` or `Cool-Regulator_` is controller
internals and is not written to, while an unnamed relay (`_ SA3-04M_RE2_…`) is
still a relay.

### Names refine the entity type

The address says what the output is; the name says what it's for.

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

Names split on `_` and `-`, case doesn't matter, and the more specific rule wins
(`imp_sv_chodba` is a button). `sv`, `imp`, `vent`, `zas` and `TL` must match as
a whole token (otherwise `Svod_vody` would be a light and `Zastineni` a socket);
`lamp`, `zrc` and `LED` are enough as a prefix. An `imp` button sends a **pulse**
on press, so the next press is again a fresh edge for the iNELS program.

**`TL_`** makes an `event` button on **any** module. A **`DIN`** input is a
button on **wall controllers** and on the **central unit itself** (In-Out); on
other modules (e.g. the `IM3` input module) it stays a plain `binary_sensor` — a
maintained contact — until you name it `TL_`. Wired buttons distinguish `press`
from `long_press` (see [Wall switches](#wall-switches-wsb3)).

The light and switch tokens apply **only to relays and dimmers** and never make
anything writable (an input named `Sv_okno` stays a `binary_sensor`); `TL`/`DIN`
apply **only to digital inputs**. There are deliberately no more rules — override
the entity type or icon in Home Assistant if something comes out differently.

### Blinds

Several addresses combine into one `cover` entity, from three possible sources:

1. **System bits of the blind program** (`0x0203`) — up, down, stop, tilt. The
   program in the unit drives the contacts and stops the blind itself. Preferred
   where present.
2. **A pair of JA3 relays** — direction is in the hardware ID
   (`JA3-018M_Up1` / `Down1`); stop by releasing both.
3. **A plain relay pair** (an `SA3` module, including the `SA3-02B` box) — two
   relays on one motor, interlocked in hardware. Here the direction is in the
   **name**: an `UP`/`DOWN` token anywhere in it (`Roleta_loznice_UP`, `…_UP_…`)
   pairs the two halves that share **the same module**.

A bare relay won't release itself, so a relay blind (forms 2 and 3) gets a
**travel-time** `number` entity (default 30 s): the relay is released once that
time elapses, so **set it to the blind's real run time**, a touch more, and it
reaches the end stop first. Reversing releases the opposite direction first,
waits briefly, then drives.

Two outputs that each belong to a **different heating zone** are deliberately
left as two switches — a wrong pairing there would drive real relays. No form
reports its position, so a cover carries an **assumed state**, and addresses
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

**Cool** is offered **only for a zone that actually has a cooling output wired**
— every zone carries the cooling channels, so their presence alone means nothing.
Setting the temperature switches the zone into **Manual**; preset values 1–4 and
the weekly plan behind Schedule are set on the unit, not here.

Each zone also has a `select` **plan** — Normal / Vacation / Holiday. Holiday is
a **daily** program and must be configured on the unit; where it isn't, the
switch doesn't take and the read-back squares the plan back in the UI.

### Wall switches (WSB3)

A single switch breaks down into one entity per channel:

| Type | Breakdown into entities |
| --- | --- |
| **WSB3-20** | 8 — 2 buttons (up/down) + 2 LEDs (green/red) + 2 temperatures + 2 digital inputs |
| **WSB3-40** | 12 — 4 buttons + 4 LEDs + 2 temperatures + 2 digital inputs |
| **WSB3-*-Hum** | +2 — humidity (`%`, `device_class humidity`) and dew point (°C) |

The indicator **LEDs** (roles `Green`/`Red`) are switches with a **G**/**R**
icon, taken from the role, so even unnamed ones get it.

Buttons (Up/Down/DIN) are **`event` entities**. Wired ones distinguish a short
**`press`** from a **`long_press`** — a hold counts as long after **1.5 s**, the
same threshold as in iNELS, and the event fires at that moment rather than on
release. That covers the whole wired wall-controller family: `GSB3`, `GSP3`,
`MSB3`, `GBP3`, `GRT3`, the card readers `GMR3`/`GCR3`/`GHR3`/`GCH3`, the info
panels `GDB3`, `WMR3`, the room controller `IDRT3`. Short/long rests on events
arriving when they happen, so anything that delays them smears the hold duration
— see [Other things talking to the unit](#other-things-talking-to-the-unit).

**RF controllers report `press` only**, and **a held RF button fires it more
than once** — see [RF devices](#rf-devices). **`IBWL`** (RF input module) is the
exception: each input mirrors a paired RF device — a button, but also a door or
motion sensor, indistinguishable in the export — so it stays a `binary_sensor`
until you name it `TL_`. Proximity sensors and card readers are not buttons.

### Devices, and what is hidden

Every **physical module** (by the serial number in the hardware ID) is its **own
device** in HA, nested under the central unit, so the channels of one switch or
relay board stay together. System things (bits, integers, buttons) have no
module and sit directly on the central unit.

Large installations export hundreds of panel internals — button contacts,
indicator LEDs, fault flags. Entities are created from them but are **disabled
by default**; enable them in the integration settings. Unnamed ones get their
name from the role in the hardware ID (e.g. `Up`, `Green`). Also disabled, **even
when named**: the **`SW` status inputs** of relays and the **fault/alert flags**
(`OUF-Alert`, `0x0107`, `device_class problem`, diagnostic).

### RF devices

A device on an RF module (e.g. an `RFKEY` remote) is its own device. Its buttons
are `event` entities firing `press` only; `Battery_LOW` is a `binary_sensor`
with `device_class battery`.

> **An RF button held down fires `press` more than once.** The module re-sends
> "down" about every 1.5 s while the button is held, and each of those is a
> press as far as anything here can tell — a two-second hold measured on a
> live `RFKEY` produced two. A wired button does not do this. If an automation
> on an RF button must not run twice, give it a cooldown (`mode: single` with
> `max_exceeded: silent`, or a condition on the last trigger).

### What's in the export, and values

The export is **not** a list of everything — in IDM3 you choose what goes into
it. If something's missing in Home Assistant, add it there and republish: the
integration notices on the next cycle and reloads itself (see
[How state stays in sync](#how-state-stays-in-sync)); **Reload** does it at once.

Temperatures and humidities come in **multiplied by a hundred** — 2550 means
25.50 °C. Dimmers are already in percent. `SYSTEMINTEGER` is a **raw value**;
what it means is up to the program that uses it. **Counters** (`0x0206`) report
the unit's real totals.

## ⚠️ Security

**The unit's authorization is not a real barrier.** It accepts an **empty
password** by default, which is how most units are left — so anyone who reaches
the unit on the network can control the entire installation. Setting a password
in IDM3 raises the bar; nothing is encrypted either way. Keep the unit on a
separate VLAN, or at least firewall it off from untrusted devices and from the
internet.

## Limitations

- **Covers report no position.** A cover shows an assumed state
  (open / closed / moving), not a percentage; a relay blind's end stop is
  inferred from the configured travel time, not measured.
- **Scenes can't be triggered** — a read on them returns no value, and writing is unverified.
- **The binary `.otc` / `.cld` formats are not read.** They additionally contain named scenes.
- **Nothing is encrypted** — neither the HTTP export nor the port 9999 traffic.

## Other things talking to the unit

**Measured:** the unit's **configuration software connected at the same time
costs almost nothing** — a single read still took a median of **4 ms** and the
whole installation **179 ms** (against 130 ms with it gone), writes were
acknowledged in **8 ms**, and the event stream ran throughout. Leave Home
Assistant running while you work on the project.

**Not measured:** how this behaves alongside a running **iNELS Connection
Server**. Expected to be fine, but nobody has checked — if you run one, please
open an issue with what you see.

## Diagnostics

- **Unit status** sensor (diagnostic) on the central unit: *running*, *running
  fast* or **stopped**. A stopped unit still answers and holds its last values,
  so without this everything looks normal in Home Assistant while nothing in the
  building responds. Its `unit_clock` attribute is the unit's **own** clock —
  heating schedules run off it, so an hour's disagreement (a unit left on winter
  time) switches your heating at the wrong hour.
- **Download diagnostics** from the integration's **⋮** menu: a redacted
  snapshot of the config, the unit's capabilities, and every entry with its live
  value and how it was classified. Host and credentials are masked, so it's the
  quickest thing to attach to a bug report.
- **`tools/probe_is3.py <ip>`** answers the same questions from outside Home
  Assistant when the integration won't set up at all — whether the unit
  responds, wants a password, opens its data plane, and pushes events. Prints
  addresses and values, never device names; read-only unless you pass `--write`.
- **`tools/compat_check.py`** before a firmware update — see
  [Firmware updates can change this](#firmware-updates-can-change-this).

## Development

```bash
pip install -r requirements-test.txt
pytest
```

On Windows read [CONTRIBUTING.md](CONTRIBUTING.md) first — it saves an
afternoon (Python 3.13, a short venv path, and an `lru-dict` wheel trick).

The other way into a unit is **XML-RPC** on the iNELS Connection Server (port
7801), which this integration doesn't use; for that route there's
[InelsForHass](https://github.com/JH-Soft-Technology/InelsForHass).

## License

[MIT](LICENSE)

[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Default-41BDF5.svg

<!-- My Home Assistant redirects: resolve against the reader's own instance. -->
[hacs-add]: https://my.home-assistant.io/redirect/hacs_repository/?owner=vlioscz&repository=is3-export&category=integration
[hacs-badge-btn]: https://my.home-assistant.io/badges/hacs_repository.svg
[config-add]: https://my.home-assistant.io/redirect/config_flow_start/?domain=is3_export
[config-badge]: https://my.home-assistant.io/badges/config_flow_start.svg
