"""Constants for the IS3 Export integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "is3_export"

MANUFACTURER: Final = "ELKO EP"
# The device model shown in Home Assistant.  "IS3" is the iNELS3 export the
# integration reads, not a unit model -- the units are the CU3 family, chiefly
# the older CU3-01M and CU3-02M.
MODEL: Final = "iNELS central unit (CU3)"

CONF_EXPORT_FILE: Final = "export_file"
CONF_DELIMITER: Final = "delimiter"

# The unit serves the export over plain HTTP on port 80, which is not
# configurable on the unit, so it is fixed rather than asked for.  Leaving the
# export file path empty fetches it from here instead of reading it off disk.
DEFAULT_HTTP_PORT: Final = 80
EXPORT_URL_PATH: Final = "/immfiles/export.is3"

# These three mirror the "Third part setting" page in iNELS IDM3, where the
# installer configures the ASCII protocol.  They must be set to match, or the
# unit and the integration will not understand each other.

# Every delimiter IDM3 offers, in the order its dropdown lists them.  IDM3 shows
# the choice as an ASCII code, e.g. [32] for a space, so the codes are shown here
# too and the two screens can be compared at a glance.
#
# The set is exactly what the dropdown contains: characters that cannot occur in
# an address or a value.  Digits, `+`, `-`, `.`, `%` and `x` are absent for that
# reason.
DELIMITER_SPACE: Final = " "
DELIMITER_SEMICOLON: Final = ";"

_DELIMITER_CHARS: Final = (
    ' "#$&\'()*,/:;<=?@[\\]^_`{|}~'
)

DELIMITERS: Final = {
    char: (f"Space [32]" if char == " " else f"{char}  [{ord(char)}]")
    for char in _DELIMITER_CHARS
}

# IDM3 calls this "Číselná soustava" / number base.
BASE_HEX: Final = "hex"
BASE_DEC: Final = "dec"
NUMBER_BASES: Final = {BASE_HEX: "Hexadecimal", BASE_DEC: "Decimal"}

CONF_NUMBER_BASE: Final = "number_base"

# IDM3's "Režim" / mode is not configured here. "Remote control + IDM" adds an id
# field to pushed events, and that is detected from the field count instead, so
# both modes work without the user having to say which one is set.

# The ASCII port the central unit listens on.  Working scripts from this
# installation used 1111 in 2022 and 22272 in 2024, so it is configurable and
# the newer value is the default.
DEFAULT_PORT: Final = 22272

DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=30)

# The device list only changes when the installer republishes it from IDM3, so
# it is re-read far less often than values are refreshed.
EXPORT_RELOAD_INTERVAL: Final = timedelta(minutes=30)
CONNECT_TIMEOUT: Final = 10.0

# Second byte of an address encodes what the address does.
TYPE_DOUT: Final = 0x01
TYPE_RELAY: Final = 0x02
TYPE_AOUT: Final = 0x03
TYPE_DIMMER: Final = 0x04
TYPE_HUMIDITY: Final = 0x05
TYPE_ANALOG: Final = 0x08
TYPE_INPUT: Final = 0x11
TYPE_THERM: Final = 0x12
TYPE_SYSTEM_INT: Final = 0x02
TYPE_SYSTEM_BIT: Final = 0x03

# Top byte: which address space the entry belongs to.
SPACE_CONTROLLER: Final = 0x00
SPACE_DEVICE: Final = 0x01
SPACE_SYSTEM: Final = 0x02
SPACE_PLAN: Final = 0x05
