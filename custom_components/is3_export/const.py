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
CONF_EXPORT_UPLOAD: Final = "export_upload"

# An export dropped into the setup form is kept here, under the Home Assistant
# config folder, so a config entry always holds a plain file path whichever way
# the export arrived.  Overwriting the saved file (or uploading again) is how a
# republished project reaches units whose firmware serves no HTTP export.
SAVED_EXPORT_DIR: Final = "is3_export"

# The unit serves the export over plain HTTP on port 80, which is not
# configurable on the unit, so it is fixed rather than asked for.  Leaving the
# export file path empty fetches it from here instead of reading it off disk.
DEFAULT_HTTP_PORT: Final = 80
EXPORT_URL_PATH: Final = "/immfiles/export.is3"

# --- Retired with the ASCII transport (0.2.0) --------------------------------
#
# These two named the settings that configured the old line-based protocol: how
# its fields were separated, and which base its numbers were written in.  The
# binary protocol has neither.  Only the stored keys survive, and only so the
# config-entry migration can find them and take them out of entries written by
# 0.1.x.  Nothing else may use them, and they go once no 0.1.x entry is left.
CONF_DELIMITER: Final = "delimiter"
CONF_NUMBER_BASE: Final = "number_base"

# --- Connection ---------------------------------------------------------------

# The UDP port the unit answers on.  It is the one the configuration software
# connects to, so it is open on every generation without anything being enabled
# first -- unlike the old ASCII port, which had to be switched on by hand and
# which the newest units never open at all.  It stays editable because remote
# installs reach the unit through a tunnel or a forwarded port.
DEFAULT_PORT: Final = 9999

DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=30)

# What the refresh drops to on a unit that will not turn its event stream on.
# There, the sweep is not a safety net behind the pushed values -- it is the
# only way anything is ever heard, and a light switched at the wall would sit
# unreported for half a minute.  A whole installation is a handful of datagrams
# and well under a second, so the shorter interval is affordable; it is not the
# default only because it buys nothing on a unit that is already telling us.
POLL_ONLY_SCAN_INTERVAL: Final = timedelta(seconds=10)

# The device list only changes when the installer republishes it from IDM3, so
# it is re-read far less often than values are refreshed.
EXPORT_RELOAD_INTERVAL: Final = timedelta(minutes=30)
CONNECT_TIMEOUT: Final = 10.0

# Relay-driven blinds report no position, so it is estimated from how long a
# direction runs against a per-blind travel time (seconds), exposed as a Number
# the installer tunes.  A run lasting the whole time is taken to have reached the
# end, which recalibrates the estimate -- and the unit's own blind timing drops
# the relay there even when the wall drove it, so that end is actually observed.
DEFAULT_COVER_TRAVEL_TIME: Final = 30
COVER_TRAVEL_TIME_MIN: Final = 1
COVER_TRAVEL_TIME_MAX: Final = 300

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
