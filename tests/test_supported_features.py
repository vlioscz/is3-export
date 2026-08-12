"""Every entity must declare the actions it implements.

Home Assistant decides whether to allow an action from ``supported_features``
alone -- it does not look at whether the method exists.  So an entity can be
created, show the right state, and refuse the one thing it was made for.

That is what happened to the fan: naming an output ``Vent_`` moved it from the
switch platform, where turning it on needs no declaration, to the fan platform,
where it does.  The result was worse than leaving it a switch, and nothing
caught it -- the live smoke test drove lights and heating, and a unit test that
builds the entity never asks Home Assistant's opinion.

Read through ``supported_features`` on an instance, never ``_attr_supported_``
``features`` on the class: Home Assistant turns those class attributes into
properties behind the scenes, so reading one off the class gets the property
object rather than the value, and comparisons against it quietly do nothing.
"""

from __future__ import annotations

from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.components.fan import FanEntityFeature

from custom_components.is3_export.climate import Is3Climate
from custom_components.is3_export.fan import Is3Fan


def features_of(cls):
    """What Home Assistant would allow, without running the constructor."""
    return cls.__new__(cls).supported_features


def test_a_fan_can_be_turned_on_and_off() -> None:
    """Both are implemented; both have to be declared to be reachable."""
    features = features_of(Is3Fan)

    assert features & FanEntityFeature.TURN_ON
    assert features & FanEntityFeature.TURN_OFF


def test_a_fan_claims_nothing_it_cannot_do() -> None:
    """It is a relay: one speed, no oscillation, no direction, no presets."""
    features = features_of(Is3Fan)

    assert not features & FanEntityFeature.SET_SPEED
    assert not features & FanEntityFeature.OSCILLATE
    assert not features & FanEntityFeature.DIRECTION
    assert not features & FanEntityFeature.PRESET_MODE


def test_a_heating_zone_declares_its_setpoint_and_presets() -> None:
    """Turning a zone on and off is declared per zone, in the constructor, so
    only what every zone has is checked here."""
    features = features_of(Is3Climate)

    assert features & ClimateEntityFeature.TARGET_TEMPERATURE
    assert features & ClimateEntityFeature.PRESET_MODE


def test_every_action_a_platform_implements_is_declared() -> None:
    """A method with no feature bit behind it is unreachable, whatever it does.

    Each entry pairs the method Home Assistant would call with the bit that
    lets it: add one later without the other and this fails.
    """
    required = (
        (Is3Fan, FanEntityFeature, (("async_turn_on", "TURN_ON"),
                                    ("async_turn_off", "TURN_OFF"))),
        (Is3Climate, ClimateEntityFeature,
         (("async_set_temperature", "TARGET_TEMPERATURE"),
          ("async_set_preset_mode", "PRESET_MODE"))),
    )

    for cls, feature_enum, pairs in required:
        features = features_of(cls)
        for method, bit in pairs:
            if method not in vars(cls):
                continue
            assert features & getattr(feature_enum, bit), (
                f"{cls.__name__}.{method} is implemented but {bit} is not declared"
            )
