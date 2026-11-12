"""Curseurs : visée (tolérance, seuils des surfaces, dans Home Assistant) et réglages de la
baguette (Bluetooth)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import DEGREE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CorneliusConfigEntry
from .const import (
    MAX_CEILING_PITCH,
    MAX_FLOOR_PITCH,
    MAX_TOLERANCE,
    MIN_CEILING_PITCH,
    MIN_FLOOR_PITCH,
    MIN_TOLERANCE,
)
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
            *(CorneliusAimSetting(wand, description) for description in AIM_SETTINGS),
            *(CorneliusRemoteSetting(wand, setting) for setting in SETTINGS if not setting.switch),
        ]
    )


@dataclass(frozen=True, kw_only=True)
class CorneliusAimDescription(NumberEntityDescription):
    value_fn: Callable[[CorneliusWand], float]
    set_fn: Callable[[CorneliusWand, float], None]


AIM_SETTINGS = (
    # Écart maximal entre la direction du bras et celle de la cible, vue d'en haut
    CorneliusAimDescription(
        key="tolerance",
        native_min_value=MIN_TOLERANCE,
        native_max_value=MAX_TOLERANCE,
        value_fn=lambda wand: wand.tolerance,
        set_fn=lambda wand, value: wand.set_tolerance(value),
    ),
    # Baguette levée au moins de tant : surface « plafond »
    CorneliusAimDescription(
        key="ceiling_pitch",
        native_min_value=MIN_CEILING_PITCH,
        native_max_value=MAX_CEILING_PITCH,
        value_fn=lambda wand: wand.ceiling_pitch,
        set_fn=lambda wand, value: wand.set_ceiling_pitch(value),
    ),
    # Baguette baissée au moins de tant : surface « sol »
    CorneliusAimDescription(
        key="floor_pitch",
        native_min_value=MIN_FLOOR_PITCH,
        native_max_value=MAX_FLOOR_PITCH,
        value_fn=lambda wand: wand.floor_pitch,
        set_fn=lambda wand, value: wand.set_floor_pitch(value),
    ),
)


class CorneliusAimSetting(CorneliusEntity, NumberEntity):
    """Réglage de la visée, gardé par Home Assistant."""

    entity_description: CorneliusAimDescription
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1
    _attr_native_unit_of_measurement = DEGREE

    def __init__(self, wand: CorneliusWand, description: CorneliusAimDescription) -> None:
        super().__init__(wand, description.key)
        self.entity_description = description

    def _update_from_wand(self) -> None:
        self._attr_native_value = self.entity_description.value_fn(self.wand)

    async def async_set_native_value(self, value: float) -> None:
        self.entity_description.set_fn(self.wand, value)


class CorneliusRemoteSetting(CorneliusEntity, NumberEntity):
    """Réglage gardé par la baguette : envoyé en Bluetooth à son prochain réveil."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, wand: CorneliusWand, setting: RemoteSetting) -> None:
        super().__init__(wand, setting.key)
        self.setting = setting
        self._attr_native_min_value = setting.to_display(setting.wand_min)
        self._attr_native_max_value = setting.to_display(setting.wand_max)
        self._attr_native_step = setting.step
        self._attr_native_unit_of_measurement = setting.unit

    def _update_from_wand(self) -> None:
        self._attr_native_value = self.wand.remote_display_value(self.setting.key)
        self._attr_extra_state_attributes = {"en_attente": self.setting.key in self.wand.remote_pending}

    async def async_set_native_value(self, value: float) -> None:
        self.wand.set_remote_setting(self.setting.key, value)
