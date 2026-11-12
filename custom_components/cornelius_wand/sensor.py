"""Capteurs : inclinaison, cible visée, statut, cibles, vision, appels à Jev, calibration et tension."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import DEGREE, MAX_LENGTH_STATE_STATE, EntityCategory, UnitOfElectricPotential
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CorneliusConfigEntry
from .entity import CorneliusEntity
from .wand import CorneliusWand


@dataclass(frozen=True, kw_only=True)
class CorneliusSensorDescription(SensorEntityDescription):
    value_fn: Callable[[CorneliusWand], Any]
    attributes_fn: Callable[[CorneliusWand], dict[str, Any] | None] = lambda wand: None
    restore: bool = True  # valeur gardée après un redémarrage de Home Assistant
    # La valeur restaurée est remplacée dès que la baguette en donne une nouvelle
    ready_fn: Callable[[CorneliusWand], bool] = lambda wand: wand.has_data


def _aimed(wand: CorneliusWand) -> dict[str, Any]:
    result = wand.last_aim
    return {
        "cible": wand.aimed_target,
        "direction": result.angle if result else None,
        "ecart": result.gap if result else None,
        "refus": result.refusal if result else None,
    }


def _status(wand: CorneliusWand) -> str | None:
    """Statut tronqué à la longueur d'un état : au-delà, Home Assistant le remplacerait par « unknown »."""
    status = wand.status
    if status is None or len(status) <= MAX_LENGTH_STATE_STATE:
        return status
    return status[: MAX_LENGTH_STATE_STATE - 1] + "…"


def _status_message(wand: CorneliusWand) -> dict[str, Any] | None:
    """Le texte complet, quand l'état est tronqué."""
    status = wand.status
    return {"message": status} if status is not None and len(status) > MAX_LENGTH_STATE_STATE else None


def _targets(wand: CorneliusWand) -> dict[str, Any]:
    return {
        "cibles": {
            key: {"nom": t.name, "x": t.x, "y": t.y, "surface": t.level} for key, t in wand.targets.items()
        }
    }


def _vision_position(wand: CorneliusWand) -> str | None:
    vision = wand.last_vision
    if vision is None or vision.origin is None:
        return None
    x, y = vision.origin
    return f"{x:.2f}, {y:.2f}"


def _vision(wand: CorneliusWand) -> dict[str, Any] | None:
    vision = wand.last_vision
    if vision is None:
        return None
    return {
        "presence_probabilite": vision.person_present,
        "epaules_confiance": vision.shoulders_confidence,
        "tete": list(vision.head.center) if vision.head else None,
        "tete_confiance": vision.head_confidence,
        "main": list(vision.hand_point) if vision.hand else None,  # le point retenu pour la direction
        "main_confiance": vision.hand_confidence,
        "latence_ms": vision.latency_ms,
        "age_photo_ms": vision.photo_age_ms,
    }


def _calibration(wand: CorneliusWand) -> str | None:
    info = wand.remote_info
    if info is None:
        return None
    return "calibrée" if info.calibrated else "à calibrer"


def _wand_info(wand: CorneliusWand) -> dict[str, Any]:
    info = wand.remote_info
    attributes: dict[str, Any] = {"reglages_en_attente": len(wand.remote_pending)}
    if info is not None:
        attributes.update({"derniere_calibration": info.calibration_text, "firmware": info.firmware})
    return attributes


SENSORS: tuple[CorneliusSensorDescription, ...] = (
    CorneliusSensorDescription(
        key="pitch",
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda wand: wand.pitch,
        ready_fn=lambda wand: wand.pitch is not None,  # une balise de présence n'a pas de visée
    ),
    CorneliusSensorDescription(
        key="aimed_target",
        value_fn=lambda wand: wand.targets[wand.aimed_target].name if wand.aimed_target in wand.targets else None,
        attributes_fn=_aimed,
        ready_fn=lambda wand: wand.last_aim is not None,  # jusqu'au premier sort
    ),
    CorneliusSensorDescription(
        key="status",
        restore=False,
        value_fn=_status,
        attributes_fn=_status_message,
    ),
    CorneliusSensorDescription(
        key="targets",
        state_class=SensorStateClass.MEASUREMENT,
        restore=False,
        value_fn=lambda wand: len(wand.targets),
        attributes_fn=_targets,
    ),
    CorneliusSensorDescription(
        key="vision",
        restore=False,
        value_fn=_vision_position,
        attributes_fn=_vision,
    ),
    CorneliusSensorDescription(
        key="jev_calls",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        restore=False,  # enregistré avec les cibles
        value_fn=lambda wand: wand.jev_calls,
    ),
    CorneliusSensorDescription(
        key="jev_calls_since_wake",
        entity_category=EntityCategory.DIAGNOSTIC,
        restore=False,
        value_fn=lambda wand: wand.jev_calls_since_wake,
        attributes_fn=lambda wand: {"reveil": wand.woke_at.isoformat() if wand.woke_at else None},
    ),
    CorneliusSensorDescription(
        key="calibration",
        restore=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_calibration,
        attributes_fn=_wand_info,
    ),
    CorneliusSensorDescription(
        key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda wand: wand.voltage,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CorneliusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(CorneliusSensor(entry.runtime_data, description) for description in SENSORS)


class CorneliusSensor(CorneliusEntity, RestoreSensor):
    entity_description: CorneliusSensorDescription

    def __init__(self, wand: CorneliusWand, description: CorneliusSensorDescription) -> None:
        super().__init__(wand, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if not self.entity_description.restore or self._attr_native_value is not None:
            return
        last = await self.async_get_last_sensor_data()
        if last is not None:
            self._attr_native_value = last.native_value

    def _update_from_wand(self) -> None:
        # Tant que la baguette n'a pas donné de nouvelle valeur, on garde la valeur restaurée
        if not self.entity_description.restore or self.entity_description.ready_fn(self.wand):
            self._attr_native_value = self.entity_description.value_fn(self.wand)
            self._attr_extra_state_attributes = self.entity_description.attributes_fn(self.wand)
