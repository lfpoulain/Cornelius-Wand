"""Tests de l'ajout de la baguette et de ses options."""

from types import SimpleNamespace
from unittest.mock import patch

from custom_components.cornelius_wand.const import CONF_CAMERA_URL, CONF_SPELLS, CONF_TYPESAFE_API_KEY, DOMAIN
from homeassistant.config_entries import SOURCE_BLUETOOTH
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .helpers import ADDRESS, SPELL, setup_wand

DISCOVERY = SimpleNamespace(address=ADDRESS, name="Cornelius", service_data={})


async def test_bluetooth_discovery(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=DISCOVERY)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"

    with patch("custom_components.cornelius_wand.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS}


async def test_user_step_without_wand(hass: HomeAssistant) -> None:
    with patch("custom_components.cornelius_wand.config_flow.async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_options(hass: HomeAssistant) -> None:
    wand = await setup_wand(hass)
    entry = wand.entry

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SPELLS: "a, b, c, d, e"})
    assert result["errors"] == {CONF_SPELLS: "too_many_spells"}

    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SPELLS: "Lumos, lumos"})
    assert result["errors"] == {CONF_SPELLS: "duplicate_spells"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SPELLS: "Lumos, Inconnu"})
    assert result["errors"] == {CONF_SPELLS: "duplicate_spells"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SPELLS: " Alohomora ,Lumos, Nox ,", CONF_CAMERA_URL: " http://cam.local ", CONF_TYPESAFE_API_KEY: "ts-1"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {
        CONF_SPELLS: "Alohomora, Lumos, Nox", CONF_CAMERA_URL: "http://cam.local", CONF_TYPESAFE_API_KEY: "ts-1"
    }
    # Le rechargement met à jour les types d'événement
    assert entry.runtime_data is not wand
    assert hass.states.get(SPELL).attributes["event_types"] == ["alohomora", "lumos", "nox", "inconnu"]


async def test_vision_can_be_turned_off(hass: HomeAssistant) -> None:
    """Champs vidés : la caméra et la clé partent, au lieu de reprendre l'ancienne valeur."""
    wand = await setup_wand(hass, vision=True)
    entry = wand.entry
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    suggested = {str(key): key.description["suggested_value"] for key in schema if key.description}
    assert suggested[CONF_CAMERA_URL] == "http://camera.local"  # pré-rempli, sans valeur par défaut

    # Une erreur sur les sorts ne remet pas l'ancienne caméra dans le champ vidé
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SPELLS: "Lumos, lumos"})
    assert result["errors"] == {CONF_SPELLS: "duplicate_spells"}
    schema = result["data_schema"].schema
    suggested = {str(key): key.description.get("suggested_value") for key in schema if key.description}
    assert not suggested.get(CONF_CAMERA_URL)

    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SPELLS: "Lumos"})
    await hass.async_block_till_done()
    assert entry.options == {CONF_SPELLS: "Lumos", CONF_CAMERA_URL: "", CONF_TYPESAFE_API_KEY: ""}
    assert not entry.runtime_data.vision_enabled
