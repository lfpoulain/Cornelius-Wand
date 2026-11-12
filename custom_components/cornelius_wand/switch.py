"""Interrupteurs : mode sort uniquement (Home Assistant), et réglages tout-ou-rien de la
baguette (Bluetooth), comme ignorer les sorts sans visée."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CorneliusConfigEntry
from .entity import CorneliusEntity
from .remote import SETTINGS, RemoteSetting
from .wand import CorneliusWand


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CorneliusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    wand = entry.runtime_data
    async_add_entities(
        [
            CorneliusSpellOnly(wand),
            *(CorneliusRemoteSwitch(wand, setting) for setting in SETTINGS if setting.switch),
        ]
    )


class CorneliusSpellOnly(CorneliusEntity, SwitchEntity):
    """Allumé : ni photo ni Jev, le sort part dès l'annonce de la baguette, sans cible."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, wand: CorneliusWand) -> None:
        super().__init__(wand, "spell_only")

    def _update_from_wand(self) -> None:
        self._attr_is_on = self.wand.spell_only

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.wand.set_spell_only(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.wand.set_spell_only(False)


class CorneliusRemoteSwitch(CorneliusEntity, SwitchEntity):
    """Réglage tout-ou-rien gardé par la baguette : envoyé en Bluetooth à son prochain réveil."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, wand: CorneliusWand, setting: RemoteSetting) -> None:
        super().__init__(wand, setting.key)
        self.setting = setting

    def _update_from_wand(self) -> None:
        self._attr_is_on = self.wand.remote_display_value(self.setting.key) >= 0.5
        self._attr_extra_state_attributes = {"en_attente": self.setting.key in self.wand.remote_pending}

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.wand.set_remote_setting(self.setting.key, 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.wand.set_remote_setting(self.setting.key, 0)
