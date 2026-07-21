"""Base entity shared by all IS3 Export platforms."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import Is3Coordinator
from .export import Is3Entry, entity_icon, module_of


class Is3Entity(CoordinatorEntity[Is3Coordinator]):
    """Common identity, device info and value lookup."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: Is3Coordinator, entry: Is3Entry) -> None:
        """Bind the entity to one address from the export file."""
        super().__init__(coordinator)
        self.entry = entry
        config_entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{config_entry_id}_{entry.unique_id}"
        self._attr_name = entry.name.replace("_", " ")

        # A large site exports hundreds of panel internals the installer never
        # named -- button contacts, indicator LEDs, per-channel fault flags.
        # They are created so nothing is unreachable, but start disabled so the
        # entity list stays close to what the installer actually labelled.
        self._attr_entity_registry_enabled_default = entry.labelled

        # An icon suggested by the name, so a fan or a mirror light reads as
        # itself. Platforms that set their own icon can still override this.
        if (icon := entity_icon(entry)) is not None:
            self._attr_icon = icon

        # Channels of one physical module -- a wall switch's buttons and LEDs, a
        # relay board's outputs -- are grouped under that module's own device, so
        # a "Green1" is clearly one switch's and not adrift among every module's.
        # The module sits under the central unit; entries with no module (system
        # bits and integers) stay on the unit itself.
        if (module := module_of(entry)) is not None:
            model, serial = module
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{config_entry_id}_{serial}")},
                manufacturer=MANUFACTURER,
                model=model,
                name=f"{model} {serial}",
                via_device=(DOMAIN, config_entry_id),
            )
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, config_entry_id)},
                manufacturer=MANUFACTURER,
                model=MODEL,
                name=coordinator.config_entry.title,
            )

    async def async_added_to_hass(self) -> None:
        """Subscribe to pushed changes of this entity's own address."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_address_listener(
                self.entry.address, self.async_write_ha_state
            )
        )

    @property
    def _value(self) -> int | None:
        """The most recent value for this address, if there is one."""
        return self.coordinator.values.get(self.entry.address)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose the identifiers needed to match the entity to the export."""
        attributes = {"address": self.entry.address_hex}
        if self.entry.hw_id:
            # The same string the vendor's XML-RPC examples use as a device name.
            attributes["hardware_id"] = self.entry.hw_id
        return attributes
