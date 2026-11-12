"""Tests de l'enregistrement HTTP et des commandes websocket du panneau Baguette."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import voluptuous as vol

from custom_components.cornelius_wand import panel
from homeassistant.components import frontend
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized
from homeassistant.setup import async_setup_component

from .helpers import IMAGE, see, setup_wand, wand_entry


@pytest.mark.parametrize("real_panel", [True])
async def test_panel_registration_serves_its_javascript(hass: HomeAssistant, hass_client) -> None:
    """Le panneau enregistré pointe vers son vrai module JS, servi par le serveur HTTP de HA."""
    assert await async_setup_component(hass, "http", {})
    await panel.async_setup_panel(hass)

    registered = hass.data[frontend.DATA_PANELS][panel.PANEL_PATH]
    assert registered.component_name == "custom"
    assert registered.require_admin is True
    custom = registered.config["_panel_custom"]
    assert custom["name"] == "cornelius-wand-panel"

    # Utilise l'URL effectivement annoncée au frontend, paramètres de version compris.
    client = await hass_client()
    response = await client.get(custom["module_url"])
    assert response.status == 200
    source = Path(panel.__file__).parent / "www" / "panel.js"
    expected = await hass.async_add_executor_job(source.read_bytes)
    assert await response.read() == expected


def connection():
    conn = MagicMock()
    conn.user.is_admin = True
    return conn


async def call(hass: HomeAssistant, handler, **msg):
    """Appelle une commande comme le ferait le panneau ; renvoie (résultat, erreur)."""
    conn = connection()
    handler(hass, conn, {"id": 1, "type": "x", **msg})
    await hass.async_block_till_done()
    result = conn.send_result.call_args.args[1] if conn.send_result.called else None
    error = conn.send_error.call_args.args[1:] if conn.send_error.called else None
    return result, error


async def test_place_targets(hass: HomeAssistant) -> None:
    wand = await setup_wand(hass)
    result, _ = await call(hass, panel.ws_add_target, name="Lampadaire", level="mur")
    assert result["key"] == "lampadaire"
    _, error = await call(hass, panel.ws_add_target, name="lampadaire")
    assert error == ("invalid", "la cible lampadaire existe déjà")

    result, _ = await call(hass, panel.ws_update_target, key="lampadaire", x=0.9, y=0.4)
    assert result["targets"] == [{"key": "lampadaire", "name": "Lampadaire", "x": 0.9, "y": 0.4, "level": "mur"}]
    await call(hass, panel.ws_update_target, key="lampadaire", level=None)
    assert wand.targets["lampadaire"].level is None
    assert wand.targets["lampadaire"].position == (0.9, 0.4)  # la position ne bouge pas

    result, _ = await call(hass, panel.ws_state)
    assert result["entry_id"] == wand.entry.entry_id
    assert result["tolerance"] == 25
    assert not result["vision_enabled"]

    result, _ = await call(hass, panel.ws_delete_target, key="lampadaire")
    assert result["targets"] == []
    _, error = await call(hass, panel.ws_delete_target, key="lampadaire")  # depuis un autre onglet
    assert error == ("invalid", "cible déjà supprimée : lampadaire")
    _, error = await call(hass, panel.ws_update_target, key="lampadaire", x=0.1, y=0.1)
    assert error[0] == "invalid"


async def test_target_out_of_view(hass: HomeAssistant, aioclient_mock) -> None:
    """Cible hors champ (dans la marge du panneau) : enregistrée telle quelle, la direction reste juste."""
    wand = await setup_wand(hass, vision=True)
    wand.add_target("Radio")
    await call(hass, panel.ws_update_target, key="radio", x=1.1, y=1.1)
    assert wand.targets["radio"].position == (1.1, 1.1)
    # Bornes de la marge du panneau : ±0,11 photo en x, ±0,15 en y (photo 4/3)
    schema = panel.ws_update_target._ws_schema  # validé par la connexion websocket, pas par l'appel direct
    message = {"id": 1, "type": "cornelius_wand/update_target", "key": "radio"}
    schema({**message, "x": -0.11, "y": 1.15})
    for x, y in ((1.13, 0.5), (-0.13, 0.5), (0.5, 1.22), (0.5, -0.22)):
        with pytest.raises(vol.Invalid):
            schema({**message, "x": x, "y": y})

    # Des épaules au centre, la radio est à 36,9° (x compté en proportions réelles, 4/3)
    see(aioclient_mock, hand=(0.62, 0.62))
    result, _ = await call(hass, panel.ws_analyze)
    assert result["target"] == "radio"
    assert result["angle"] == pytest.approx(36.9, abs=0.5)
    assert result["gaps"]["radio"] < 1


async def test_two_wands(hass: HomeAssistant) -> None:
    """Chaque baguette a ses cibles ; une baguette déchargée n'est jamais remplacée par une autre."""
    _, error = await call(hass, panel.ws_state)
    assert error == ("not_found", "aucune baguette configurée")

    first = await setup_wand(hass)
    first.add_target("Lampe")
    other = wand_entry(hass, address="02:00:00:00:00:03")
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()

    result, _ = await call(hass, panel.ws_state, entry_id=other.entry_id)
    assert result["entry_id"] == other.entry_id
    assert result["targets"] == []
    assert [e["entry_id"] for e in result["entries"]] == [first.entry.entry_id, other.entry_id]
    result, _ = await call(hass, panel.ws_state)  # sans entry_id : la première
    assert result["entry_id"] == first.entry.entry_id

    await hass.config_entries.async_unload(other.entry_id)
    for handler, msg in (
        (panel.ws_state, {}),
        (panel.ws_photo, {}),
        (panel.ws_analyze, {}),
        (panel.ws_add_target, {"name": "Lampe"}),
        (panel.ws_update_target, {"key": "lampe", "x": 0.2, "y": 0.2}),
        (panel.ws_delete_target, {"key": "lampe"}),
    ):
        _, error = await call(hass, handler, entry_id=other.entry_id, **msg)
        assert error == ("not_found", "baguette introuvable ou en cours de rechargement")
    assert list(first.targets) == ["lampe"]
    assert first.targets["lampe"].position is None


async def test_admin_only(hass: HomeAssistant) -> None:
    await setup_wand(hass)
    conn = connection()
    conn.user.is_admin = False
    with pytest.raises(Unauthorized):  # la connexion websocket le renvoie au panneau
        panel.ws_state(hass, conn, {"id": 1, "type": "x"})
    conn.send_result.assert_not_called()


async def test_photo_and_analysis(hass: HomeAssistant, aioclient_mock) -> None:
    wand = await setup_wand(hass, vision=True)
    wand.update_target(wand.add_target("Lampadaire"), 0.95, 0.5)
    see(aioclient_mock, hand=(0.62, 0.5), age=40)

    result, _ = await call(hass, panel.ws_photo)
    assert result["image"] and result["age_ms"] == 40  # le panneau date la photo de sa prise

    wand.set_tolerance(30)
    result, _ = await call(hass, panel.ws_analyze)
    assert result["target"] == "lampadaire"
    assert result["angle"] == 0
    assert result["tolerance"] == 30  # pour dessiner le cône sans recharger l'état
    assert result["latency_ms"] >= 0 and 0 < result["hand_confidence"] <= 1  # carte du résultat
    await hass.async_block_till_done(wait_background_tasks=True)  # la photo annotée se dessine après la réponse
    sensor = hass.states.get("sensor.baguette_cornelius_vision")  # capteur Vision : la dernière analyse
    shoulders = result["shoulders"]
    assert sensor.state == f"{(shoulders['left'] + shoulders['right']) / 2:.2f}, {(shoulders['top'] + shoulders['bottom']) / 2:.2f}"
    assert hass.states.get(IMAGE).state != "unknown"
    assert result["shoulders"]["left"] < 0.5 < result["shoulders"]["right"]
    assert result["image"] and result["age_ms"] == 40
    assert aioclient_mock.mock_calls[-2][1].query["ago"] == "0"  # photo de maintenant


async def test_analysis_without_vision(hass: HomeAssistant) -> None:
    await setup_wand(hass)
    _, error = await call(hass, panel.ws_analyze)
    assert error[0] == "unavailable"
