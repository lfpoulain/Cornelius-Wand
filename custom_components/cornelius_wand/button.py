"""Boutons : calibration et réglages par défaut, envoyés à la baguette à son prochain réveil
(Bluetooth)."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CorneliusConfigEntry
from .entity import CorneliusEntity
from .remote import COMMAND_CALIBRATE
from .wand import CorneliusWand


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CorneliusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    wand = entry.runtime_data
    async_add_entities(
        [
            # Commande exécutée par la baguette une fois déconnectée
            CorneliusWandButton(wand, "calibrate", lambda wand: wand.request_command(COMMAND_CALIBRATE)),
            # Valeurs par défaut en attente : un réglage modifié ensuite passe par-dessus
            CorneliusWandButton(wand, "reset_settings", lambda wand: wand.reset_remote_settings()),
        ]
    )


class CorneliusWandButton(CorneliusEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _follow_wand = False

    def __init__(self, wand: CorneliusWand, key: str, press: Callable[[CorneliusWand], None]) -> None:
        super().__init__(wand, key)
        self._press = press

    async def async_press(self) -> None:
        self._press(self.wand)
