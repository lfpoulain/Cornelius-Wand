"""Base commune des entités de la baguette."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .wand import CorneliusWand


class CorneliusEntity(Entity):
    """Entité rattachée à l'appareil de la baguette, mise à jour par elle."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _follow_wand = True  # mise à jour à chaque changement d'état de la baguette

    def __init__(self, wand: CorneliusWand, key: str) -> None:
        self.wand = wand
        self._attr_translation_key = key
        self._attr_unique_id = f"{wand.address}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, wand.address)},
            connections={(CONNECTION_BLUETOOTH, wand.address)},
            name="Baguette Cornelius",
            manufacturer="Cornelius",
            model="Baguette magique (Nano 33 BLE)",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._follow_wand:
            self._update_from_wand()
            self.async_on_remove(self.wand.async_add_listener(self._handle_wand_update))

    def _handle_wand_update(self) -> None:
        self._update_from_wand()
        self.async_write_ha_state()

    def _update_from_wand(self) -> None:
        """À surcharger : recopie l'état de la baguette dans l'entité."""
