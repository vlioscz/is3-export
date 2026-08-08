"""The setup and reconfigure forms share one schema and one identity rule.

The flow orchestration needs a running Home Assistant, which this Windows-
friendly test suite deliberately avoids, so the pure pieces are tested here:
how a unit's unique id and title are derived, and that the reconfigure form
opens pre-filled on the values already configured.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.helpers.selector import FileSelector, TextSelector

from custom_components.is3_export.config_flow import (
    build_schema,
    saved_export_filename,
    unit_identity,
)
from custom_components.is3_export.const import (
    CONF_EXPORT_FILE,
    CONF_EXPORT_UPLOAD,
    DEFAULT_PORT,
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
    # Most units have no password set, so the field starts empty rather than
    # demanding one.
    assert defaults[CONF_PASSWORD] == ""
    # the host is not pre-filled on a fresh install
    assert CONF_HOST not in defaults


def test_the_password_field_is_masked() -> None:
    """It is a secret, so the form must not show it in the clear."""
    schema = build_schema({})
    marker = next(m for m in schema.schema if str(m) == CONF_PASSWORD)
    assert isinstance(schema.schema[marker], TextSelector)


def test_reconfigure_opens_on_the_current_values() -> None:
    existing = {
        CONF_HOST: "192.168.1.5",
        CONF_PORT: 9999,
        CONF_PASSWORD: "hunter2",
        CONF_EXPORT_FILE: "",
    }
    defaults = _defaults(build_schema(existing))
    assert defaults[CONF_HOST] == "192.168.1.5"
    assert defaults[CONF_PORT] == 9999
    # Pre-filled on purpose: correcting the host must not silently blank the
    # password and strand the entry.
    assert defaults[CONF_PASSWORD] == "hunter2"


def test_the_form_offers_an_export_upload() -> None:
    """The export can be dropped straight into the form.

    Newer CU3 firmware serves no HTTP export, so without this the file would
    have to be copied onto the Home Assistant machine by hand.
    """
    schema = build_schema({})
    marker = next(m for m in schema.schema if str(m) == CONF_EXPORT_UPLOAD)
    assert isinstance(marker, vol.Optional)
    assert isinstance(schema.schema[marker], FileSelector)
    # An upload id is transient, so the field is never pre-filled -- not even
    # on reconfigure.
    assert marker.default is vol.UNDEFINED


def test_uploaded_exports_are_saved_under_a_safe_name() -> None:
    """The saved file is named after the unit, slugified for the filesystem."""
    assert saved_export_filename("ABC123") == "abc123.is3"
    assert saved_export_filename("192.168.1.5") == "192_168_1_5.is3"


def test_schema_validates_a_complete_input() -> None:
    validated = build_schema({})(
        {
            CONF_HOST: "192.168.1.5",
            CONF_PORT: 9999,
            CONF_PASSWORD: "",
            CONF_EXPORT_FILE: "",
        }
    )
    assert validated[CONF_HOST] == "192.168.1.5"
    assert validated[CONF_PORT] == 9999
