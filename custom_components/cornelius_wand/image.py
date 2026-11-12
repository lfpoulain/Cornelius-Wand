"""Image : la dernière photo analysée par Jev, avec le cadre de la personne trouvée."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CorneliusConfigEntry
from .entity import CorneliusEntity
from .wand import CorneliusWand


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CorneliusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([CorneliusVisionImage(hass, entry.runtime_data)])


class CorneliusVisionImage(CorneliusEntity, ImageEntity):
    """Ce que l'API a vu : utile pour comprendre ses réponses."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, wand: CorneliusWand) -> None:
        CorneliusEntity.__init__(self, wand, "vision_photo")
        ImageEntity.__init__(self, hass)

    def _update_from_wand(self) -> None:
        self._attr_image_last_updated = self.wand.last_vision_time

    async def async_image(self) -> bytes | None:
        return self.wand.last_vision_image
