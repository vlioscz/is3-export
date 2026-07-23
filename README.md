<p align="center"><img src="brands/logo.png" alt="IS3 · vlios.cz" width="360"></p>

# IS3 Export

[![hacs][hacs-badge]][hacs] [![Validate](https://github.com/vlioscz/is3-export/actions/workflows/validate.yaml/badge.svg)](https://github.com/vlioscz/is3-export/actions/workflows/validate.yaml)

**English** · [Česky](README.cs.md)

UNOFFICIAL Home Assistant integration for **iNELS central units** (ELKO EP) over their
ASCII interface — primarily the older **CU3-01M** and **CU3-02M**. It talks to the
unit directly and **needs no Connection Server**.

> The "IS3" in the name is **iNELS3** — the `.is3` export format the integration
> is based on.

It downloads the device list from the export the unit serves itself. It tracks
state live: the unit pushes changes on its own, so nothing is polled.

> **Status: experimental.** The protocol is verified against a live unit, the
> parser against exports from seven installations (17 to 1125 items, IDM3
> 03-03-34 through 03-05-03). Blinds on a real drive remain unverified — see
> [Limitations](#limitations).

## ❗ First enable the protocol in IDM3

Without this nothing works — the unit isn't listening on the ASCII port.

In **iNELS IDM3** → *Central unit configuration* → **Third part setting**:

| Item | What to do |
| --- | --- |
| **Port** | Pick a free port. There's no default, so write it down. |
| **Separator** | Must match the integration's setting. `[32]` is a space. |
| **Number base** | Must match the integration's setting. |
| **Mode** | Remote control + IDM |

On the right, tick the **events the unit should send**. Whatever you don't tick,
the integration will never learn about, and the entity stays stuck at its last
value:

- `Digital_OUT_SwitchOn` / `SwitchOff` — relays (without them you won't catch a switch flip)
- `Analog_OUT_ValueChanged`, `Analog_OUT_SwitchOn` / `SwitchOff` — dimmers
- `Analog_IN_ValueChange`, `Sensor_Change` — temperatures, humidities, analog inputs
- `Digital_IN_SwitchOn` / `SwitchOff` — inputs and buttons
- `SysInt_Change`, `Program_ValueSwitchOn` / `Off` — system variables

Finally, **Save to CU**.

How fast state shows up in Home Assistant depends on these checkboxes. The unit
is also driven by switches on the walls, so a change need not come from HA — and
it's only detected from an event. **What has its own event updates within a
second or so; what doesn't, only on the periodic re-read (30 s).** The
variability with changes from the wall (instant vs. 2–3 s) is the unit's own
delay before it sends the change over ASCII, not the integration's.

Commands from HA itself show up immediately, and the integration then **verifies
them by reading back** — if the output didn't take, or a switch on the wall
flipped it in the meantime, the state corrects itself to reality instead of
leaving the icon stuck in the wrong state.

## Installation

[![Add repository to HACS][hacs-badge-btn]][hacs-add]

Then **Download**, restart Home Assistant, and:

[![Add integration][config-badge]][config-add]

Manually: copy `custom_components/is3_export` into `config/custom_components/`.

## Configuration

| Field | Description | Default |
| --- | --- | --- |
| Host | The unit's IP address | — |
| ASCII port | **from IDM3** (default `1111`) | `1111` |
| Export file path | leave empty, it downloads from the unit | empty |
| Separator | **from IDM3**, offers all 27 options | space `[32]` |
| Number base | **from IDM3** | hexadecimal |

The integration's name is taken from the export header.

**No password is entered.** The unit's web server serves the export as a static
file with no login, so **the iNELS project password has no effect on its
availability**. (If some unit blocks the download anyway, enter the path to a
locally downloaded export.)

If entities report an *estimated state*, your **separator** is wrong.

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
| `0x01`**`08`** | analog input | `sensor` | ❌ |
| `0x01`**`03`**, `0x01`**`11`**, `0x01`**`12`** | controller channels | `sensor` | ❌ |
| `0x02`**`06`** | water meters, electricity meters | `sensor` (total) | ❌ |
| `0x05`**`01`**, `0x02`**`04`**, `0x02`**`09`**, `0x0003` | plans, groups, schedules | — | ❌ |

Writes happen **only where a write is documented**. Never to inputs, thermostat
channels, or plans.

The **hardware ID** decides, not the name: anything starting with `Controller_`
is controller internals and is not written to — a window sensor sits in the same
range as a relay. Conversely, an unnamed relay (`_ SA3-04M_RE2_…`) is still a
relay.

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

They combine several addresses into one `cover` entity, from two possible
sources:

1. **System bits of the blind program** — up, down, stop, tilt. The program in
   the unit drives the contacts itself. It takes priority.
2. **A pair of JA3 relays** — only up and down, stop by releasing both. Used
   only when there's no program in the export.

Addresses taken by a blind no longer appear as switches.

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

> ⚠️ **A condition for reliable short/long: no running Connection Server.** Its
> periodic polling freezes the unit for a few seconds and smears the hold
> duration (see the **Connection Server slows the response** section below). That
> originally made this look like a dead end; under clean conditions the timing is
> reliable.

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

A device on an RF module (e.g. `RFKEY` — remotes) appears as its own device with
buttons (`binary_sensor`), and the `Battery_LOW` battery status gets
`device_class battery`.

### What's in the export

The export is **not** a list of everything — in IDM3 you choose what goes into
it. If something's missing in Home Assistant, add it there. The integration
checks once every 30 minutes whether the list has changed and reloads itself.
**Reload** does it immediately.

### Values

Temperatures and humidities come in **multiplied by a hundred** — 2550 means
25.50 °C. Dimmers are already in percent. `SYSTEMINTEGER` is a **raw value** that
isn't converted in any way; what it means is up to the program that uses it.

## ⚠️ Security

**The ASCII port has no authentication** — and the password on the unit won't
change that; it only protects the web server. Anyone who reaches that TCP port
can control the entire installation.

Keep the unit on a separate VLAN, or at least firewall it off from untrusted
devices and from the internet.

## Limitations

- **Blinds are not verified on a real drive.** For the relay variant, it's
  assumed that `1` starts the motor and `0` stops it; the pause when reversing
  direction is a guess.
- **Scenes can't be triggered** — a `GET` on them returns `N`, and writing is unverified.
- **The binary `.otc` / `.cld` formats are not read.** They additionally contain named scenes.
- **Both the HTTP and ASCII ports run without encryption.**

## ⚠️ Connection Server slows the response

If the same central unit is also served by the **iNELS Connection Server**,
expect an occasional delay. The Connection Server reaches into the unit for the
complete state roughly **every 40–60 s**, and during that the unit **freezes the
entire ASCII output for ~2–4 s** — it stops sending events and stops executing
commands. Every so often a press or a toggle falls into this window and then
reacts those 2–4 s later — and that **for all clients at once**, including the
Connection Server itself (so it delays even itself).

- **If you don't need the Connection Server, turn it off** — the integration's
  response is then smooth (the unit answers in ~180 ms).
- **If you do need it**, slow down / lighten the periodic state polling in its
  configuration (how often and how much it reads from the unit).

Verified in isolation: the integration itself causes no freezing — it arises
only when the Connection Server is also connected.

> The number of clients isn't related either: the central unit has a **limited
> number of ASCII connections**. Don't point a lot of clients at the ASCII port at
> once — when the slots run out, the unit will accept the connection but stop
> serving it (the HTTP export keeps running), and only a CU restart helps.

## Diagnostics

When something's off, this script finds out what the unit can do — it's
read-only until you add `--write`:

```bash
python tools/probe_is3.py <ip> <port> 0x0102000A
```

## Development

```bash
pip install -r requirements-test.txt
pytest
```

The unit also speaks other protocols the integration doesn't work with:
**ELKONET** (binary, port 9999) and **XML-RPC** on the Connection Server
(port 7801) — for that route there's
[InelsForHass](https://github.com/JH-Soft-Technology/InelsForHass).

## License

[MIT](LICENSE)

[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg

<!-- My Home Assistant redirects: these resolve against whatever instance the
     reader is signed in to, so no address of anyone's Home Assistant appears
     here. -->
[hacs-add]: https://my.home-assistant.io/redirect/hacs_repository/?owner=vlioscz&repository=is3-export&category=integration
[hacs-badge-btn]: https://my.home-assistant.io/badges/hacs_repository.svg
[config-add]: https://my.home-assistant.io/redirect/config_flow_start/?domain=is3_export
[config-badge]: https://my.home-assistant.io/badges/config_flow_start.svg
