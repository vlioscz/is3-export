"""Parser for IS3 export files.

Two variants exist and both are accepted:

``.is3``
    Starts with a ``VERSION_...`` header line, then one entry per line as
    ``name hw_id address value [extra...] [unit]``.  ``name`` is ``_`` when the
    entry has no user assigned label.

``.imm``
    No header, and no ``hw_id`` column: ``name address [value] [unit]``.

Both are UTF-8 with a BOM and CRLF line endings.  Rather than switching on the
file extension, each line is split on whitespace and the first token that looks
like ``0x`` followed by hex digits is taken as the address; whatever precedes it
is the label column(s).  That handles both layouts, and also the entries that
carry no value at all (scenes such as ``Byt_all_digital 0x02040008``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

_LOGGER = logging.getLogger(__name__)

_HEX_TOKEN = re.compile(r"^0x[0-9A-Fa-f]+$")
_HEADER_PREFIX = "VERSION_"

# The header is one underscore separated string of KEY_VALUE pairs, e.g.
# VERSION_01-03-03_CREATE_2022-05-08-21-38-51_IDM3_03-03-34_ID_813F..._NAME_Dum
_HEADER_KEYS = ("VERSION", "CREATE", "IDM3", "ID", "NAME")

NO_LABEL = "_"


@dataclass(slots=True)
class Is3Header:
    """Metadata from the first line of a ``.is3`` export."""

    version: str | None = None
    created: str | None = None
    idm3: str | None = None
    unit_id: str | None = None
    name: str | None = None


@dataclass(slots=True)
class Is3Entry:
    """One addressable item listed in the export file."""

    name: str
    address: int
    hw_id: str | None = None
    value: int | None = None
    unit: str | None = None
    extra: list[str] = field(default_factory=list)
    labelled: bool = True
    """Whether the installer gave this entry a name.

    Entries written as ``_`` in a ``.is3`` export are internals the installer
    never named -- controller channels, detector inputs -- and they sit in the
    same address ranges as real outputs.  ``.imm`` exports have no such marker,
    so everything there reads as labelled.
    """

    @property
    def address_hex(self) -> str:
        """The address formatted the way the export file writes it."""
        return f"0x{self.address:08X}"

    @property
    def space(self) -> int:
        """Top byte: which address space the entry lives in."""
        return (self.address >> 24) & 0xFF

    @property
    def data_type(self) -> int:
        """Second byte: the data type / role of the address."""
        return (self.address >> 16) & 0xFF

    @property
    def index(self) -> int:
        """Low 16 bits: position within the address space."""
        return self.address & 0xFFFF

    @property
    def unique_id(self) -> str:
        """Stable identifier within one central unit."""
        return self.address_hex.lower()


@dataclass(slots=True)
class Is3Export:
    """A parsed export file."""

    entries: list[Is3Entry] = field(default_factory=list)
    header: Is3Header | None = None

    def by_address(self, address: int) -> Is3Entry | None:
        """Look up a single entry by its numeric address."""
        return next((e for e in self.entries if e.address == address), None)

    @property
    def fingerprint(self) -> tuple[tuple[int, str, str | None], ...]:
        """Identifies the set of entities this export would produce.

        Deliberately ignores the values and the header timestamp, so that
        re-exporting an unchanged installation is not mistaken for a change.
        """
        return tuple(
            sorted((e.address, e.name, e.unit) for e in self.entries)
        )


# How the second byte of an address maps onto entity types.  This table is not
# vendor documented; it is inferred from two independent installations that
# agree, plus the working scripts from this one.
#
# Writable classes are deliberately narrow.  Writing to an address that turns
# out to be an input or a heating plan slot has unknown consequences, so
# anything not proven to be an output is exposed read-only.

# Relays and system bits: on/off outputs the local scripts are known to drive.
SPACE_SYSTEM = 0x02
TYPE_SYSTEM_BIT = 0x03

# Physical relays, as opposed to the virtual bits of a program.
RELAY_OUTPUT = frozenset({(0x01, 0x02)})
SWITCHABLE = frozenset(RELAY_OUTPUT | {(SPACE_SYSTEM, TYPE_SYSTEM_BIT)})

# Dimmers, written with a percentage.  Vendor sample scripts call
# writeValue('DA3-22M_OUT1_010cb2', 70) and abetka scales brightness to 0-100.
DIMMABLE = frozenset({(0x01, 0x04)})

# Digital inputs: wall buttons, motion detectors, and the controller's own
# status outputs (Controller_Status-DOUT).  Not to be confused with system
# bits, which are the writable 0x0203 variables.
BINARY = frozenset({(0x01, 0x01)})

# Fault flags raised by the modules themselves: blind driver overload, dimmer
# over-temperature, relay power supply failure.
ALERT = frozenset({(0x01, 0x07)})

# Analog readings.  The unit of measurement comes from the export file, not from
# the address, because one class covers both temperature and humidity.
#
#   0x0103  controller holiday output
#   0x0105  temperature and humidity sensors
#   0x0108  analog inputs and controller analog outputs
#   0x0111  controller control inputs
#   0x0112  controller thermostat channels
#   0x0206  counters -- water and electricity meters
MEASURED = frozenset(
    {
        (0x01, 0x03),
        (0x01, 0x05),
        (0x01, 0x08),
        (0x01, 0x11),
        (0x01, 0x12),
        (0x02, 0x06),
    }
)

# System integers: named 32-bit variables the installer's programme reads and
# writes -- a dimmer's remembered level, a wind speed, an effect number.  They
# are conditions and scratch space for that programme, so they are writable
# rather than merely observable.
#
# Their values are raw.  What a number means, and whether it is scaled at all,
# is decided by whoever wrote the programme, so there is no general rule to
# apply and none is applied: they are passed through exactly as stored.
NUMBER = frozenset({(0x02, 0x02)})

# The unit stores them as signed 32-bit.
NUMBER_MIN = -(2**31)
NUMBER_MAX = 2**31 - 1

# Counters only ever climb, so they can drive Home Assistant's statistics.
COUNTER = frozenset({(0x02, 0x06)})

# Entries whose hardware id starts with this are internals of a heating
# controller, not devices.  They share address ranges with real outputs -- a
# window detector sits among the relays, a control-type channel among the
# dimmers -- so they are matched by hardware id and never written to.
CONTROLLER_PREFIX = "Controller_"

DIMMER_MIN = 0
DIMMER_MAX = 100

# Dimmers are marked with a percentage unit in the export.  Controller config
# channels share the dimmer address range but carry no unit, and writing a
# brightness into one would reconfigure a thermostat.
DIMMER_UNIT = "%"


def _class_of(entry: Is3Entry) -> tuple[int, int]:
    """The address pair used to look the entry up in the tables above."""
    return (entry.space, entry.data_type)


def is_controller_internal(entry: Is3Entry) -> bool:
    """Whether the entry belongs to a heating controller rather than a device.

    Keyed on the hardware id, not on whether the installer named the entry.
    Plenty of real outputs go unnamed -- ``_ SA3-04M_RE2_0A0001`` is an ordinary
    relay -- so treating unnamed as read-only would strand them.
    """
    identity = entry.hw_id or entry.name
    return identity.startswith(CONTROLLER_PREFIX)


def is_switchable(entry: Is3Entry) -> bool:
    """Whether the entry is an on/off output this integration will write to."""
    return _class_of(entry) in SWITCHABLE and not is_controller_internal(entry)


def is_dimmable(entry: Is3Entry) -> bool:
    """Whether the entry is a dimmer taking a 0-100 percentage.

    Real dimmers carry a percent unit; the controller's control-type channel
    shares the address range but has none.
    """
    return (
        _class_of(entry) in DIMMABLE
        and not is_controller_internal(entry)
        and entry.unit == DIMMER_UNIT
    )


def is_binary(entry: Is3Entry) -> bool:
    """Whether the entry reads as on/off.

    Covers digital inputs and module fault flags, plus controller internals that
    sit in the relay range -- exposed read-only rather than dropped, since a
    window detector is worth seeing even if it must never be written to.
    """
    if _class_of(entry) in BINARY or _class_of(entry) in ALERT:
        return True
    return _class_of(entry) in SWITCHABLE and is_controller_internal(entry)


def is_measured(entry: Is3Entry) -> bool:
    """Whether the entry is an analog reading.

    Includes dimmer-range addresses that failed the dimmer test, so controller
    config channels stay visible but read-only.
    """
    if _class_of(entry) in MEASURED:
        return True
    return _class_of(entry) in DIMMABLE and not is_dimmable(entry)


def is_counter(entry: Is3Entry) -> bool:
    """Whether the entry is a meter reading that only ever increases."""
    return _class_of(entry) in COUNTER


# --- Naming conventions -----------------------------------------------------
#
# The address says what an output *is*; the name the installer gave it says
# what it is *for*.  Two conventions are honoured, because the address cannot
# tell them apart -- an impulse and a lamp are both just relays or bits.
#
#   imp   a momentary impulse: `imp_wled_pc`, `mobil_imp_1`, `imp_WOL_PC`
#   sv    a light: `Sv_loznice`, `SV_prijezd`, `Velke_sv_obyvak`
#   lamp  supplementary lighting: `Lamp_loznice_1`, `Nocni_lamp`
#   zrc   a mirror light: `Sv_zrc_dole`, `sv_zrcadlo_tech`
#
# Tokens are split on `_` and `-` and compared case-insensitively.  `sv` and
# `imp` must match a whole token, or `Svod_vody` would become a light and
# `Impus_rolety` a button.  `lamp` and `zrc` match as a prefix instead, because
# the exports spell the mirror one both ways -- `zrc` twice and `zrcadlo` five
# times -- and no other word starts with either.
IMPULSE_TOKEN = "imp"
LIGHT_TOKEN = "sv"

# Supplementary lighting, named apart so it can be told from the main lights at
# a glance.  Each gets its own icon, which is the reason the names differ.
LAMP_PREFIX = "lamp"
MIRROR_PREFIX = "zrc"
# A fan on a relay -- `Vent_koup`, `VENT_WC_2np`.  Always a whole token; there
# is no `ventil` in the exports, and matching it as a prefix might catch one.
VENT_TOKEN = "vent"

ICON_LAMP = "mdi:floor-lamp"
ICON_MIRROR = "mdi:mirror"
ICON_FAN = "mdi:fan"

PLATFORM_BUTTON = "button"
PLATFORM_LIGHT = "light"
PLATFORM_SWITCH = "switch"
PLATFORM_NUMBER = "number"
PLATFORM_BINARY_SENSOR = "binary_sensor"
PLATFORM_SENSOR = "sensor"
# Covers, climate zones and button events are assembled from entries or ride
# alongside them, rather than being returned by platform_of.
PLATFORM_COVER = "cover"
PLATFORM_CLIMATE = "climate"
PLATFORM_SELECT = "select"
PLATFORM_EVENT = "event"


def name_tokens(entry: Is3Entry) -> list[str]:
    """The installer's name, split into lowercase tokens."""
    return [token for token in _TOKEN_SEPARATORS.split(entry.name.lower()) if token]


def is_impulse(entry: Is3Entry) -> bool:
    """Whether the name marks this output as a momentary impulse.

    An impulse has no state worth showing -- it is fired, not held -- so it
    becomes a button rather than a switch.
    """
    return is_switchable(entry) and IMPULSE_TOKEN in name_tokens(entry)


def is_named_light(entry: Is3Entry) -> bool:
    """Whether the name marks this on/off output as a light.

    Only physical relays qualify.  A system bit carrying a light word is far
    more likely to be a program flag than a lamp -- across seven installations
    the one such bit is `blok_noc_lamp`, which switches off the automatic
    programme rather than a light -- while every real light sits on a relay or
    a dimmer.
    """
    if not is_switchable(entry) or is_impulse(entry):
        return False
    if _class_of(entry) not in RELAY_OUTPUT:
        return False

    tokens = name_tokens(entry)
    return LIGHT_TOKEN in tokens or any(
        token.startswith((LAMP_PREFIX, MIRROR_PREFIX)) for token in tokens
    )


def entity_icon(entry: Is3Entry) -> str | None:
    """An icon suggested by the name, chosen with the platform in mind.

    Icons are tied to the platform on purpose.  The lamp and mirror icons apply
    only to lights, so a program flag such as `blok_noc_lamp` -- a switch that
    happens to contain `lamp` -- does not pick one up.  The fan icon applies
    only to switches.  A mirror light is often also named `Sv_`, so the more
    specific word wins.
    """
    platform = platform_of(entry)
    tokens = name_tokens(entry)

    if platform == PLATFORM_LIGHT:
        if any(token.startswith(MIRROR_PREFIX) for token in tokens):
            return ICON_MIRROR
        if any(token.startswith(LAMP_PREFIX) for token in tokens):
            return ICON_LAMP
    elif platform == PLATFORM_SWITCH:
        if VENT_TOKEN in tokens:
            return ICON_FAN
    return None


def platform_of(entry: Is3Entry) -> str | None:
    """Which platform an entry belongs to, or None if it produces no entity.

    Blinds are decided separately, by :func:`find_covers`, because they are
    assembled from several entries at once.  Whatever a blind claims is removed
    from the platform it would otherwise land on.
    """
    if is_impulse(entry):
        return PLATFORM_BUTTON
    if is_dimmable(entry) or is_named_light(entry):
        return PLATFORM_LIGHT
    if is_switchable(entry):
        return PLATFORM_SWITCH
    if is_number(entry):
        return PLATFORM_NUMBER
    if is_binary(entry):
        return PLATFORM_BINARY_SENSOR
    if is_measured(entry):
        return PLATFORM_SENSOR
    return None


# --- Blinds -----------------------------------------------------------------
#
# Blinds appear in exports two ways, and the richer one is preferred.
#
# As a group of system bits written by the installer's blind program.  Two
# naming conventions are in use, and both carry the same six functions:
#
#   ZALUZIE_pracovna_Bit_Pohyb_Nahoru_0000     ZAL_kp_bit_ZAL_nahoru_0009
#   ZALUZIE_pracovna_Bit_Pohyb_Dolu_0001       ZAL_kp_bit_ZAL_dolu_000D
#   ZALUZIE_pracovna_Bit_Naklon_Nahoru_0002    ZAL_kp_bit_ZAL_cuk_nahoru_000B
#   ZALUZIE_pracovna_Bit_Naklon_Dolu_0003      ZAL_kp_bit_ZAL_cuk_dolu_000A
#   ZALUZIE_pracovna_Bit_Pretrzeni_0004        ZAL_kp_bit_roztrzeni_000C
#   ZALUZIE_pracovna_Bit_Pomocne_Pretrzeni…    ZAL_kp_bit_pom_roztrzeni_000E
#
# Or as a pair of relays on a JA3 blind driver, which gives up and down only.
# Those modules interlock the two directions internally, so driving them
# directly cannot engage both at once.

_BIT_SEPARATORS = ("_bit_", "_Bit_")
_INDEX_SUFFIX = re.compile(r"_[0-9A-Fa-f]{4}$")

# Matched against the lowercased function part of a system bit name.
_TILT_WORDS = ("naklon", "cuk")
_UP_WORDS = ("nahoru",)
_DOWN_WORDS = ("dolu",)
_STOP_WORDS = ("pretrzeni", "roztrzeni")
_AUXILIARY_WORDS = ("pom", "pomocne")

# JA3-018M_Up1_0A0002 / JA3-014M_Down7_0B0004
_RELAY_CHANNEL = re.compile(
    r"^(?P<model>.+?)_(?P<direction>Up|Down)(?P<channel>\d+)_(?P<serial>[0-9A-Fa-f]+)$"
)

OPEN = "open"
CLOSE = "close"
STOP = "stop"
TILT_OPEN = "tilt_open"
TILT_CLOSE = "tilt_close"


@dataclass(slots=True)
class Is3Cover:
    """A blind, assembled from several addresses."""

    name: str
    source: str
    """Either ``systembit`` (pulsed program bits) or ``relay`` (a JA3 pair)."""

    open: Is3Entry
    close: Is3Entry
    stop: Is3Entry | None = None
    tilt_open: Is3Entry | None = None
    tilt_close: Is3Entry | None = None
    # Addresses the blind program owns but this integration does not drive --
    # an auxiliary interrupt bit, say.  Consumed so they do not surface as their
    # own switches, but never written.
    internal: tuple[int, ...] = ()

    @property
    def unique_id(self) -> str:
        """Stable identifier, taken from the address that opens the blind."""
        return f"cover_{self.open.unique_id}"

    @property
    def addresses(self) -> list[int]:
        """Every address this cover consumes, driven or internal."""
        parts = [self.open, self.close, self.stop, self.tilt_open, self.tilt_close]
        driven = [part.address for part in parts if part is not None]
        return driven + list(self.internal)

    @property
    def has_tilt(self) -> bool:
        """Whether the blind's slats can be angled."""
        return self.tilt_open is not None and self.tilt_close is not None


def _bit_function(name: str) -> tuple[str, str] | None:
    """Split a system bit name into (group, function), or None if it is not one."""
    for separator in _BIT_SEPARATORS:
        if separator in name:
            group, _, function = name.partition(separator)
            return group, _INDEX_SUFFIX.sub("", function).lower()
    return None


def _classify_bit(function: str) -> str | None:
    """Map a function name onto the role it plays, in either convention."""
    words = function.split("_")

    if any(word in _STOP_WORDS for word in words):
        # Blind programs expose a second, auxiliary stop; one is enough.
        if any(word in _AUXILIARY_WORDS for word in words):
            return None
        return STOP

    up = any(word in _UP_WORDS for word in words)
    down = any(word in _DOWN_WORDS for word in words)
    if not up and not down:
        return None

    if any(word in _TILT_WORDS for word in words):
        return TILT_OPEN if up else TILT_CLOSE
    return OPEN if up else CLOSE


def _covers_from_system_bits(export: Is3Export) -> list[Is3Cover]:
    """Assemble blinds from the installer's blind program bits."""
    groups: dict[str, dict[str, Is3Entry]] = {}
    members: dict[str, list[Is3Entry]] = {}

    for entry in export.entries:
        if _class_of(entry) != (SPACE_SYSTEM, TYPE_SYSTEM_BIT):
            continue
        parsed = _bit_function(entry.name)
        if parsed is None:
            continue
        group, function = parsed
        # Every bit of the group belongs to its blind program, even one this
        # integration does not drive, so all are recorded to be claimed.
        members.setdefault(group, []).append(entry)
        role = _classify_bit(function)
        if role is None:
            continue
        groups.setdefault(group, {}).setdefault(role, entry)

    covers = []
    for group, roles in groups.items():
        # Without both directions it is not a blind we can drive.
        if OPEN not in roles or CLOSE not in roles:
            continue
        driven = {role_entry.address for role_entry in roles.values()}
        internal = tuple(
            entry.address for entry in members[group] if entry.address not in driven
        )
        covers.append(
            Is3Cover(
                name=group,
                source="systembit",
                open=roles[OPEN],
                close=roles[CLOSE],
                stop=roles.get(STOP),
                tilt_open=roles.get(TILT_OPEN),
                tilt_close=roles.get(TILT_CLOSE),
                internal=internal,
            )
        )
    return covers


_DIRECTION_PREFIX = re.compile(r"^(?:Up|Down)\d*_?(?P<rest>.*)$", re.IGNORECASE)


def _strip_direction(name: str) -> str:
    """Turn `Up1_Pokoj_pravy` into `Pokoj_pravy`, leaving other names alone."""
    match = _DIRECTION_PREFIX.match(name)
    return match["rest"] if match and match["rest"] else name


def _pair_base_name(entry: Is3Entry) -> str:
    """What the two halves of a blind must agree on to be one blind.

    A channel named only `Up6`, or left unnamed, says nothing, so it matches
    anything on the same channel number.  A channel the installer gave a real
    name has to agree with its counterpart -- blind drivers get repurposed, and
    on one site `Up5` is labelled `NIC` while `Down5` switches a corridor light.
    Pairing those would offer a blind that is really a lamp.
    """
    if not entry.labelled:
        return ""
    match = _DIRECTION_PREFIX.match(entry.name)
    if match is None:
        return entry.name.lower()
    return match["rest"].lower()


def _covers_from_relay_pairs(export: Is3Export) -> list[Is3Cover]:
    """Assemble blinds from the up/down relay pairs of a blind driver."""
    channels: dict[tuple[str, str, str], dict[str, Is3Entry]] = {}

    for entry in export.entries:
        if not is_switchable(entry) or not entry.hw_id:
            continue
        match = _RELAY_CHANNEL.match(entry.hw_id)
        if match is None:
            continue
        key = (match["model"], match["serial"], match["channel"])
        channels.setdefault(key, {})[match["direction"].lower()] = entry

    covers = []
    for (model, serial, channel), pair in channels.items():
        if "up" not in pair or "down" not in pair:
            continue
        up, down = pair["up"], pair["down"]
        if _pair_base_name(up) != _pair_base_name(down):
            # Not a blind: the two halves describe different things.
            continue

        name = _strip_direction(up.name) if up.labelled else f"{model} {channel}"
        covers.append(Is3Cover(name=name, source="relay", open=up, close=down))
    return covers


# --- Heating controllers (climate) ------------------------------------------
#
# A controller is a set of channels sharing a six-hex serial suffix, plus a
# named root ``<name> Controller_<serial>``.  The Connection Server paired the
# same channels into a climate zone; the roles below come from those pairings:
#
#   Actual-Therm-AOUT    current temperature (read, x100)
#   Required-Therm-AOUT  the heat setpoint in force, from the active plan (read)
#   Manual-Therm-AIN     the manual heat setpoint (write, x100; active in Manual)
#   Required-Heat-DOUT   1 while calling for heat
#   Control-Manual-IN    preset select: 0 Schedule, 1-4 presets, 7 Manual
#   Control-HC-IN        heat/cool select: 0 heat, 1 cool (write; verified live)
#
# Cooling mirrors heating on its own channels, present on every controller:
#
#   Required-Cool-DOUT       1 while calling for cooling
#   Required-Cool-Therm-AOUT the cool setpoint in force (read)
#   Manual-Cool-Therm-AIN    the manual cool setpoint (write; active in Manual)
#
# The weekly plan behind Schedule (HEATCOOL_WEEK) is configured on the unit and
# is not touched here.
_CONTROLLER_ID = re.compile(
    r"^Controller_(?:(?P<role>.+)_)?(?P<serial>[0-9A-Fa-f]{6})$"
)

# Control-Manual-IN values, confirmed by watching the iNELS app switch presets
# on a live unit.  The values are not contiguous -- Manual is 7, not 5 -- which
# matches the MQTT integration, where the "manual" preset goes on the wire as 7.
PRESET_SCHEDULE = 0
PRESET_MANUAL = 7
CONTROLLER_PRESETS: dict[int, str] = {
    0: "Schedule",
    1: "Preset 1",
    2: "Preset 2",
    3: "Preset 3",
    4: "Preset 4",
    7: "Manual",
}
PRESET_VALUES: dict[str, int] = {name: value for value, name in CONTROLLER_PRESETS.items()}

# Control-Plan-IN values, all verified on a live unit that had the festive plan
# configured: each is writable and holds.  0 normal (the weekly schedule), 64
# (0x40) vacation, 128 (0x80) public holiday.  Selecting public holiday raised
# the setpoint to its daily programme (HEATCOOL_DAY) and lit Public_holiday-AOUT;
# vacation instead lit Holiday-DOUT.  The public-holiday plan must be set up in
# the unit as a daily programme; where it is not, selecting it simply does not
# take, the same as any unconfigured plan.
PLAN_OPTIONS: dict[int, str] = {0: "Normal", 64: "Vacation", 128: "Public holiday"}
PLAN_VALUES: dict[str, int] = {name: value for value, name in PLAN_OPTIONS.items()}

_REQUIRED_ROLES = (
    "Actual-Therm-AOUT",
    "Required-Therm-AOUT",
    "Manual-Therm-AIN",
    "Required-Heat-DOUT",
    "Control-Manual-IN",
)


@dataclass(slots=True)
class Is3Controller:
    """A heating zone, assembled from one controller's channels."""

    name: str
    serial: str
    actual: int
    required: int
    manual: int
    heat_demand: int
    preset_select: int
    control_on: int | None = None
    control_hc: int | None = None
    plan_select: int | None = None
    cool_demand: int | None = None
    cool_required: int | None = None
    cool_manual: int | None = None
    status: int | None = None

    @property
    def unique_id(self) -> str:
        """Stable identifier within one central unit."""
        return f"climate_{self.serial.lower()}"

    @property
    def read_addresses(self) -> list[int]:
        """Every address this zone reads for its state."""
        addresses = [
            self.actual,
            self.required,
            self.heat_demand,
            self.preset_select,
        ]
        for optional in (
            self.control_on,
            self.control_hc,
            self.plan_select,
            self.cool_demand,
            self.cool_required,
            self.status,
        ):
            if optional is not None:
                addresses.append(optional)
        return addresses


def find_controllers(export: Is3Export) -> list[Is3Controller]:
    """Assemble heating zones from the controller channels in the export."""
    roles: dict[str, dict[str, int]] = {}
    names: dict[str, str] = {}

    for entry in export.entries:
        match = _CONTROLLER_ID.match(entry.hw_id or "")
        if match is None:
            continue
        serial = match["serial"]
        if match["role"] is None:
            names[serial] = entry.name if entry.labelled else f"Controller {serial}"
        else:
            roles.setdefault(serial, {})[match["role"]] = entry.address

    controllers = []
    for serial, channels in roles.items():
        if not all(role in channels for role in _REQUIRED_ROLES):
            continue
        controllers.append(
            Is3Controller(
                name=names.get(serial, f"Controller {serial}"),
                serial=serial,
                actual=channels["Actual-Therm-AOUT"],
                required=channels["Required-Therm-AOUT"],
                manual=channels["Manual-Therm-AIN"],
                heat_demand=channels["Required-Heat-DOUT"],
                preset_select=channels["Control-Manual-IN"],
                control_on=channels.get("Control-IN"),
                control_hc=channels.get("Control-HC-IN"),
                plan_select=channels.get("Control-Plan-IN"),
                cool_demand=channels.get("Required-Cool-DOUT"),
                cool_required=channels.get("Required-Cool-Therm-AOUT"),
                cool_manual=channels.get("Manual-Cool-Therm-AIN"),
                status=channels.get("Status-DOUT"),
            )
        )
    return controllers


def expected_entities(export: Is3Export, entry_id: str) -> set[tuple[str, str]]:
    """Every (platform, unique_id) this export should produce.

    Used to prune the entity registry: as the classification was refined an
    output could move platform -- a system integer from sensor to number, a
    relay from switch to light -- and the entity from the old platform lingers
    as unavailable.  The platform is part of the key because both share the
    address-based unique id, so matching on the id alone would keep the stale
    one.
    """
    covers = find_covers(export)
    claimed = {address for cover in covers for address in cover.addresses}

    expected = {
        (PLATFORM_COVER, f"{entry_id}_{cover.unique_id}") for cover in covers
    }
    controllers = find_controllers(export)
    expected |= {
        (PLATFORM_CLIMATE, f"{entry_id}_{controller.unique_id}")
        for controller in controllers
    }
    expected |= {
        (PLATFORM_SELECT, f"{entry_id}_plan_{controller.serial.lower()}")
        for controller in controllers
        if controller.plan_select is not None
    }
    for entry in export.entries:
        if entry.address in claimed:
            continue
        if is_press_button(entry):
            # A button is an event, not the binary sensor its input would be.
            expected.add((PLATFORM_EVENT, f"{entry_id}_{entry.unique_id}_event"))
            continue
        platform = platform_of(entry)
        if platform is not None:
            expected.add((platform, f"{entry_id}_{entry.unique_id}"))
    return expected


def find_covers(export: Is3Export) -> list[Is3Cover]:
    """Find the blinds in an export.

    Program bits win when they exist: they add stop and tilt, and an
    installation that has them drives its blinds through them, so building
    covers from the relays as well would duplicate every blind.
    """
    if covers := _covers_from_system_bits(export):
        return covers
    return _covers_from_relay_pairs(export)


# Temperatures and humidities come back multiplied by 100: a reply of 0x9F6
# (2550) on a thermostat channel is 25.50 degrees.  Dimmers are already a plain
# percentage, and millivolts are raw, so the scale depends on both the unit and
# the address class.
SCALED_UNITS = frozenset({"°C", "%"})
VALUE_SCALE = 100

CELSIUS = "°C"

# Older modules label temperature inputs with a unit; newer ones such as
# IOU3-108M leave the column empty, so `Venkovni_teplota IOU3-108M_TIN1 ...`
# arrives bare.  Without the unit it would neither be scaled nor labelled, and
# 25.50 degrees would read as 2550.
#
# The unit is therefore inferred from the *channel*, never from the module:
# IOU3-108M is a universal input module whose other channels are digital inputs
# and relays, so anything module-wide would be wrong.  Matching whole tokens
# rather than substrings keeps a name that merely contains these letters from
# being read as a thermometer.
_THERMOMETER_CHANNEL = re.compile(r"^(TIN\d*|Therm)$", re.IGNORECASE)
_TOKEN_SEPARATORS = re.compile(r"[_\-]")


def _is_thermometer_channel(identity: str) -> bool:
    """Whether the hardware id names a temperature channel."""
    return any(
        _THERMOMETER_CHANNEL.match(token)
        for token in _TOKEN_SEPARATORS.split(identity)
    )


def effective_unit(entry: Is3Entry) -> str | None:
    """The unit of the reading, inferred from the channel name when absent.

    iNELS scales every temperature by 100, so getting this right is what
    separates 25.50 degrees from a bare 2550.
    """
    if entry.unit is not None:
        return entry.unit
    if _class_of(entry) not in MEASURED:
        # The guess exists to repair unit-less *readings*. A system integer
        # holds whatever the installer's programme puts there, raw, and a fault
        # flag such as `OUF-Alert_TIN1` reports that an input failed rather
        # than what it reads -- neither is a temperature however it is named.
        return None
    if _is_thermometer_channel(entry.hw_id or entry.name):
        return CELSIUS
    return None


def value_scale(entry: Is3Entry) -> int:
    """By how much a raw reading must be divided to get the real value."""
    if is_dimmable(entry):
        return 1
    return VALUE_SCALE if effective_unit(entry) in SCALED_UNITS else 1


def is_number(entry: Is3Entry) -> bool:
    """Whether the entry is a system integer, which can be read and written."""
    return _class_of(entry) in NUMBER


def is_writable(entry: Is3Entry) -> bool:
    """Whether this integration ever sends a value to the entry."""
    return is_switchable(entry) or is_dimmable(entry) or is_number(entry)


def is_readable(entry: Is3Entry) -> bool:
    """Whether the entry becomes an entity, and so is worth polling."""
    return (
        is_switchable(entry)
        or is_dimmable(entry)
        or is_number(entry)
        or is_binary(entry)
        or is_measured(entry)
    )


def parse_export(payload: str) -> Is3Export:
    """Parse the text of a ``.is3`` or ``.imm`` export file."""
    export = Is3Export()

    for lineno, raw_line in enumerate(payload.splitlines(), start=1):
        line = raw_line.lstrip("﻿").strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith(_HEADER_PREFIX):
            if export.header is None:
                export.header = _parse_header(line)
            continue

        entry = _parse_entry(line)
        if entry is None:
            _LOGGER.debug("Skipping unparseable export line %d: %r", lineno, line)
            continue
        export.entries.append(entry)

    return export


def _parse_header(line: str) -> Is3Header:
    """Pull the known keys out of the ``VERSION_...`` header line.

    The values themselves contain ``-`` but not ``_``, so splitting the whole
    line on ``_`` and looking for the key names is unambiguous.
    """
    tokens = line.split("_")
    values: dict[str, str] = {}
    index = 0
    while index < len(tokens) - 1:
        if tokens[index] in _HEADER_KEYS:
            values[tokens[index]] = tokens[index + 1]
            index += 2
            continue
        index += 1

    return Is3Header(
        version=values.get("VERSION"),
        created=values.get("CREATE"),
        idm3=values.get("IDM3"),
        unit_id=values.get("ID"),
        name=values.get("NAME"),
    )


_HW_SERIAL = re.compile(r"_[0-9A-Fa-f]{6}$")


def _role_from_hw_id(hw_id: str) -> str | None:
    """The role in a hardware id: ``WSB3-20-Hum_Up_0B0003`` -> ``Up``.

    Hardware ids read ``<module>_<role>_<serial>``: the serial is a trailing
    six-hex suffix and the module the leading segment, so the role is what sits
    between them.  Returns None when there is nothing between them to name.
    """
    trimmed = _HW_SERIAL.sub("", hw_id)
    _module, separator, role = trimmed.partition("_")
    return role if separator and role else None


_HW_MODULE = re.compile(r"^(?P<model>[^_]+)_.+_(?P<serial>[0-9A-Fa-f]{6})$")


def module_of(entry: Is3Entry) -> tuple[str, str] | None:
    """The physical module an entry lives on: ``(model, serial)``, or None.

    A hardware id reads ``<module>_<role>_<serial>``, so entries sharing a serial
    are channels of one physical module -- a wall switch, a relay board, a
    dimmer -- and belong together under one device.  Two kinds stay on the
    central unit itself: controller channels, which make up a heating zone the
    climate entity already stands for, and the unit's own ``In-Out`` terminals,
    of which there are few and often none in use.  System-level entries (system
    bits and integers) have no module.
    """
    hw_id = entry.hw_id
    if not hw_id:
        return None
    match = _HW_MODULE.match(hw_id)
    if match is None:
        return None
    model = match["model"]
    if model == "Controller" or model.startswith("In-Out"):
        return None
    return model, match["serial"]


def is_press_button(entry: Is3Entry) -> bool:
    """Whether the entry is a button whose press length carries meaning.

    A wall switch input, or an RF remote's, carries a long press -- held two
    seconds or more -- that iNELS acts on but the export cannot describe, since
    it is not a physical channel.  It can still be told apart from an ordinary
    press by how long the input stays on, so these inputs get an event entity
    that reports both.  A remote's low-battery flag is an input too, but not a
    button, so it is left out.
    """
    module = module_of(entry)
    if module is None:
        return False
    model = module[0]
    if not (model.startswith("WSB") or model == "RFKEY"):
        return False
    return _class_of(entry) in BINARY and not is_battery_input(entry)


def is_battery_input(entry: Is3Entry) -> bool:
    """Whether a digital input is a low-battery flag, such as an RF device's."""
    role = _role_from_hw_id(entry.hw_id) if entry.hw_id else None
    return role is not None and "battery" in role.lower()


# Roles always worth showing even when the installer left them unnamed, because
# whether they carry a reading depends on iNELS configuration the export does not
# reveal.  A wall switch's AIN1-AIN2 terminals are wired either as two digital
# inputs or as one temperature input, reported here as AIN1-AIN2-Therm; only the
# unit knows which, so the temperature is surfaced on every switch, named or not.
_ALWAYS_SHOWN_ROLES = frozenset({"ain1-ain2-therm"})

# A relay module's SW inputs mirror the local switch wired to each output; the
# ALERT class is its per-output fault flags.  Both ride along on many modules,
# are rarely wanted, and clutter the list, so they are hidden even when named.
_SW_ROLE = re.compile(r"^SW\d+$", re.IGNORECASE)


def _is_always_hidden(entry: Is3Entry) -> bool:
    """Outputs hidden by default on every module, whatever their name."""
    if _class_of(entry) in ALERT:
        return True
    role = _role_from_hw_id(entry.hw_id) if entry.hw_id else None
    return role is not None and _SW_ROLE.match(role) is not None


def enabled_by_default(entry: Is3Entry) -> bool:
    """Whether the entity should start enabled rather than hidden.

    Named entries are shown and unnamed panel internals hidden, with exceptions
    both ways.  Hidden even when named: a relay's SW state inputs and the fault
    flags, which few installations watch.  Shown even when unnamed: every channel
    of a wall switch, a deliberate control point, and a handful of roles that may
    carry a real reading whatever their name.
    """
    if _is_always_hidden(entry):
        return False
    if entry.labelled:
        return True
    module = module_of(entry)
    if module is not None and module[0].startswith("WSB"):
        return True
    role = _role_from_hw_id(entry.hw_id) if entry.hw_id else None
    return role is not None and role.lower() in _ALWAYS_SHOWN_ROLES


def _parse_entry(line: str) -> Is3Entry | None:
    """Parse one entry line, or return None if it has no usable address."""
    tokens = line.split()

    address_at = next(
        (i for i, token in enumerate(tokens) if _HEX_TOKEN.match(token)), None
    )
    if address_at is None or address_at == 0:
        # No address, or no label in front of it -- not an entry we understand.
        return None

    labels = tokens[:address_at]
    name = labels[0]
    hw_id = labels[1] if len(labels) > 1 else None
    labelled = name != NO_LABEL
    if not labelled and hw_id:
        # Unlabelled entries fall back to the hardware id's role -- a wall
        # switch input reads "Up", not "WSB3-20-Hum_Up_0B0003" -- or to the
        # whole id when it carries no role.
        name = _role_from_hw_id(hw_id) or hw_id

    try:
        address = int(tokens[address_at], 16)
    except ValueError:  # pragma: no cover - guarded by the regex
        return None

    rest = tokens[address_at + 1 :]
    value: int | None = None
    unit: str | None = None
    extra: list[str] = []

    for token in rest:
        if _HEX_TOKEN.match(token):
            if value is None:
                value = int(token, 16)
            else:
                extra.append(token)
        elif "_0x" in token:
            # Controller entries append an underscore joined blob of references.
            extra.append(token)
        else:
            unit = token

    return Is3Entry(
        name=name,
        address=address,
        hw_id=hw_id,
        value=value,
        unit=unit,
        extra=extra,
        labelled=labelled,
    )
