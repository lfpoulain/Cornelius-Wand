"""Baguette Cornelius : sorts et visée d'une baguette magique en Bluetooth."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, STORAGE_VERSION
from .panel import async_register_commands, async_setup_panel
from .wand import PRESENCE_POLL, CorneliusWand

PLATFORMS = [
    Platform.BUTTON,
    Platform.EVENT,
    Platform.IMAGE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type CorneliusConfigEntry = ConfigEntry[CorneliusWand]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Commandes et panneau Baguette (une fois pour toutes les baguettes, avant elles)."""
    async_register_commands(hass)
    await async_setup_panel(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CorneliusConfigEntry) -> bool:
    wand = CorneliusWand(hass, entry)
    await wand.async_load()
    entry.runtime_data = wand
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Annonces écoutées en passif ; les réglages passent par une connexion (remote.py)
    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            wand.handle_advertisement,
            bluetooth.BluetoothCallbackMatcher(address=wand.address, connectable=False),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
    )
    # Réveils de la baguette, pour le compteur d'appels à Jev depuis le réveil
    entry.async_on_unload(async_track_time_interval(hass, wand.check_presence, PRESENCE_POLL))
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CorneliusConfigEntry) -> bool:
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_unload()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: CorneliusConfigEntry) -> None:
    """Baguette supprimée : ses cibles et ses réglages enregistrés partent avec elle."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


async def _async_options_updated(hass: HomeAssistant, entry: CorneliusConfigEntry) -> None:
    """Les noms des sorts changent les types d'événement : on recharge."""
    await hass.config_entries.async_reload(entry.entry_id)
