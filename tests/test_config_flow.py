"""The setup and reconfigure forms share one schema and one identity rule.

The flow orchestration needs a running Home Assistant, which this Windows-
friendly test suite deliberately avoids, so the pure pieces are tested here:
how a unit's unique id and title are derived, and that the reconfigure form
opens pre-filled on the values already configured.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.const import CONF_HOST, CONF_PORT

from custom_components.is3_export.config_flow import build_schema, unit_identity
from custom_components.is3_export.const import (
    BASE_HEX,
    CONF_DELIMITER,
    CONF_EXPORT_FILE,
    CONF_NUMBER_BASE,
    DEFAULT_PORT,
    DELIMITER_SPACE,
)
from custom_components.is3_export.export import Is3Export, Is3Header


def test_identity_comes_from_the_export_header() -> None:
    export = Is3Export(header=Is3Header(unit_id="ABCDEF", name="My House"))
    assert unit_identity(export, "192.168.1.5") == ("ABCDEF", "My House")


def test_identity_falls_back_to_the_host() -> None:
    assert unit_identity(Is3Export(header=None), "192.168.1.5") == (
        "192.168.1.5",
        "IS3 192.168.1.5",
    )
    empty = Is3Export(header=Is3Header(unit_id=None, name=None))
    assert unit_identity(empty, "192.168.1.9") == ("192.168.1.9", "IS3 192.168.1.9")


def _defaults(schema: vol.Schema) -> dict[str, object]:
    """The default value of each field that has one."""
    return {
        str(marker): marker.default()
        for marker in schema.schema
        if marker.default is not vol.UNDEFINED
    }


def test_a_fresh_form_uses_the_documented_defaults() -> None:
    defaults = _defaults(build_schema({}))
    assert defaults[CONF_PORT] == DEFAULT_PORT
    assert defaults[CONF_DELIMITER] == DELIMITER_SPACE
    assert defaults[CONF_NUMBER_BASE] == BASE_HEX
    # the host is not pre-filled on a fresh install
    assert CONF_HOST not in defaults


def test_reconfigure_opens_on_the_current_values() -> None:
    existing = {
        CONF_HOST: "192.168.1.5",
        CONF_PORT: 22272,
        CONF_DELIMITER: ";",
        CONF_NUMBER_BASE: "dec",
        CONF_EXPORT_FILE: "",
    }
    defaults = _defaults(build_schema(existing))
    assert defaults[CONF_HOST] == "192.168.1.5"
    assert defaults[CONF_PORT] == 22272
    assert defaults[CONF_DELIMITER] == ";"
    assert defaults[CONF_NUMBER_BASE] == "dec"


def test_schema_validates_a_complete_input() -> None:
    validated = build_schema({})(
        {
            CONF_HOST: "192.168.1.5",
            CONF_PORT: 1111,
            CONF_EXPORT_FILE: "",
            CONF_DELIMITER: " ",
            CONF_NUMBER_BASE: "hex",
        }
    )
    assert validated[CONF_HOST] == "192.168.1.5"
    assert validated[CONF_PORT] == 1111
