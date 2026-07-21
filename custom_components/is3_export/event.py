"""Event platform for the IS3 Export integration.

A wall switch button carries a second function on a long press -- held two
seconds or more -- that iNELS acts on but cannot put in the export, since it is
not a physical channel.  The unit does report the input going on and off, though,
so the length of the press can be measured here: a press that outlasts the
threshold is fired as a long press the moment it crosses it, the way iNELS
reacts, and a shorter one as a short press when the button is let go.
"""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .coordinator import Is3ConfigEntry, Is3Coordinator
from .entity import Is3Entity
from .export import Is3Entry, is_wsb_button

SHORT_PRESS = "short_press"
LONG_PRESS = "long_press"

# Held this long or more is a long press, matching the iNELS threshold.
LONG_PRESS_SECONDS = 2.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Is3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a press event for every wall switch button."""
    coordinator = entry.runtime_data
    async_add_entities(
        Is3ButtonEvent(coordinator, item)
        for item in coordinator.data.export.entries
        if is_wsb_button(item)
    )


class Is3ButtonEvent(Is3Entity, EventEntity):
    """The press of a wall switch button, reported as short or long.

    A long press fires the moment the hold crosses the threshold -- as iNELS
    does, while the button is still down -- not on release.  A release before
    then is a short press.
    """

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = [SHORT_PRESS, LONG_PRESS]

    def __init__(self, coordinator: Is3Coordinator, entry: Is3Entry) -> None:
        """Sit alongside the button's binary sensor, on the same switch."""
        super().__init__(coordinator, entry)
        self._address = entry.address
        self._attr_unique_id = f"{self._attr_unique_id}_event"
        self._attr_name = f"{entry.name.replace('_', ' ')} press"
        self._pressed = False
        self._long_fired = False
        self._cancel_timer = None

    async def async_added_to_hass(self) -> None:
        """Watch the input for its own press logic, not the base state rewrite."""
        # Skip Is3Entity's listener -- it only rewrites state -- and use one that
        # measures how long the button is held.
        await super(Is3Entity, self).async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_address_listener(
                self._address, self._handle_change
            )
        )
        self.async_on_remove(self._stop_timer)

    @callback
    def _handle_change(self) -> None:
        """React to the input going on or off."""
        pressed = bool(self.coordinator.values.get(self._address))
        if pressed and not self._pressed:
            # Button went down: start the long-press clock.
            self._pressed = True
            self._long_fired = False
            self._stop_timer()
            self._cancel_timer = async_call_later(
                self.hass, LONG_PRESS_SECONDS, self._on_long_threshold
            )
        elif not pressed and self._pressed:
            # Button released: a short press, unless the long one already fired.
            self._pressed = False
            self._stop_timer()
            if not self._long_fired:
                self._fire(SHORT_PRESS)

    @callback
    def _on_long_threshold(self, _now) -> None:
        """The hold outlasted the threshold while the button was still down."""
        self._cancel_timer = None
        self._long_fired = True
        self._fire(LONG_PRESS)

    @callback
    def _fire(self, event_type: str) -> None:
        """Emit one press event."""
        self._trigger_event(event_type)
        self.async_write_ha_state()

    @callback
    def _stop_timer(self) -> None:
        """Cancel a pending long-press timer, if any."""
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None
