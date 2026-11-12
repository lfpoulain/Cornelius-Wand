"""Entité Sort : un événement par sort lancé, avec la cible visée."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CorneliusConfigEntry
from .entity import CorneliusEntity
from .wand import CorneliusWand, SpellResult


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CorneliusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([CorneliusSpellEvent(entry.runtime_data)])


class CorneliusSpellEvent(CorneliusEntity, EventEntity):
    """Se déclenche à chaque sort ; le type d'événement est le nom du sort."""

    _follow_wand = False

    def __init__(self, wand: CorneliusWand) -> None:
        super().__init__(wand, "spell")
        self._attr_event_types = wand.spell_types

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.wand.async_add_spell_listener(self._handle_spell))

    @callback
    def _handle_spell(self, result: SpellResult) -> None:
        self._trigger_event(result.spell, result.attributes)
        self.async_write_ha_state()
