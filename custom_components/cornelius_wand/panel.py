"""Panneau « Baguette » : placer les cibles en cliquant sur la photo de la caméra.

Le panneau (www/panel.js) parle à l'intégration par ces commandes websocket, réservées
aux administrateurs :
- cornelius_wand/state : baguettes configurées, cibles et tolérance ;
- cornelius_wand/photo : photo actuelle de la caméra, et son âge ;
- cornelius_wand/analyze : photo actuelle analysée par Jev, et la cible qu'elle désigne ;
- cornelius_wand/add_target, update_target, delete_target : gérer les cibles.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.loader import async_get_integration

from .aim import LEVELS
from .const import DOMAIN
from .vision import VisionError
from .wand import CorneliusWand

STATIC_URL = "/cornelius_wand_panel"
PANEL_PATH = "cornelius-wand"


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Sert www/panel.js et ajoute « Baguette » dans la barre latérale."""
    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "www"), False)]
    )
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_PATH,
        webcomponent_name="cornelius-wand-panel",
        module_url=f"{STATIC_URL}/panel.js?v={version}",  # change à chaque version : pas de cache périmé
        sidebar_title="Baguette",
        sidebar_icon="mdi:magic-staff",
        require_admin=True,
    )


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    for command in (ws_state, ws_photo, ws_analyze, ws_add_target, ws_update_target, ws_delete_target):
        websocket_api.async_register_command(hass, command)


def _wand(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> CorneliusWand | None:
    """Baguette de la commande (la première sans entry_id) ; sinon répond not_found et renvoie None."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if "entry_id" not in msg:
        if entries:
            return entries[0].runtime_data
        error = "aucune baguette configurée"
    else:
        # Jamais une autre baguette à la place : les cibles iraient à la mauvaise
        entry = next((e for e in entries if e.entry_id == msg["entry_id"]), None)
        if entry is not None:
            return entry.runtime_data
        error = "baguette introuvable ou en cours de rechargement"
    connection.send_error(msg["id"], "not_found", error)
    return None


def _targets(wand: CorneliusWand) -> list[dict[str, Any]]:
    return [
        {"key": key, "name": t.name, "x": t.x, "y": t.y, "level": t.level}
        for key, t in wand.targets.items()
    ]


ENTRY = vol.Optional("entry_id")

# Cible hors champ : cliquée dans la marge hachurée du panneau (padding de 9 % de la largeur,
# photo sur 82 % de la largeur), soit 0,09 / 0,82 ≈ 0,11 photo en x et 0,09 × r / 0,82 en y
# au-delà de chaque bord (r = largeur / hauteur de la photo : 0,15 en 4/3, 0,20 en 16/9 si
# PHOTO_FRAMESIZE passe en HD) ; un centième de plus pour les arrondis du navigateur
MARGIN_X = 0.12
MARGIN_Y = 0.21


@websocket_api.websocket_command({vol.Required("type"): "cornelius_wand/state", ENTRY: str})
@websocket_api.require_admin
@callback
def ws_state(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (wand := _wand(hass, connection, msg)) is None:
        return
    connection.send_result(
        msg["id"],
        {
            "entry_id": wand.entry.entry_id,
            "entries": [
                {"entry_id": e.entry_id, "title": e.title} for e in hass.config_entries.async_loaded_entries(DOMAIN)
            ],
            "targets": _targets(wand),
            "levels": list(LEVELS),
            "tolerance": wand.tolerance,
            "vision_enabled": wand.vision_enabled,
        },
    )


@websocket_api.websocket_command({vol.Required("type"): "cornelius_wand/photo", ENTRY: str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_photo(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (wand := _wand(hass, connection, msg)) is None:
        return
    try:
        photo, age = await wand.async_photo()
    except VisionError as err:
        connection.send_error(msg["id"], "unavailable", str(err))
        return
    # L'âge (ms, None si la caméra ne le donne pas) date la photo dans le panneau : la caméra
    # rend sa dernière photo rangée, parfois ancienne
    connection.send_result(msg["id"], {"image": base64.b64encode(photo).decode(), "age_ms": age})


@websocket_api.websocket_command({vol.Required("type"): "cornelius_wand/analyze", ENTRY: str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_analyze(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Photo de maintenant analysée par Jev : on pointe la cible, sans baguette ni inclinaison."""
    if (wand := _wand(hass, connection, msg)) is None:
        return
    look = await wand.async_look(0)
    if (vision := look.vision) is None:
        connection.send_error(msg["id"], "unavailable", look.error or "vision indisponible")
        return
    result = wand.aim_at(vision, None)
    connection.send_result(
        msg["id"],
        {
            "image": base64.b64encode(look.photo).decode(),
            "age_ms": vision.photo_age_ms,
            "shoulders": vars(vision.shoulders) if vision.shoulders else None,
            "head": vars(vision.head) if vision.head else None,
            "hand": vars(vision.hand) if vision.hand else None,
            "shoulders_confidence": vision.shoulders_confidence,
            "head_confidence": vision.head_confidence,
            "hand_confidence": vision.hand_confidence,
            "latency_ms": vision.latency_ms,
            "target": result.target,
            "angle": result.angle,
            "gaps": result.gaps,
            "refusal": result.refusal,
            "tolerance": wand.tolerance,  # celle de maintenant, pour dessiner le cône de la visée
        },
    )
    await wand.async_record_look(look, result)  # après la réponse : le panneau n'attend pas le dessin


@websocket_api.websocket_command(
    {
        vol.Required("type"): "cornelius_wand/add_target",
        ENTRY: str,
        vol.Required("name"): str,
        vol.Optional("level"): vol.Any(None, vol.In(LEVELS)),
    }
)
@websocket_api.require_admin
@callback
def ws_add_target(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (wand := _wand(hass, connection, msg)) is None:
        return
    try:
        key = wand.add_target(msg["name"], msg.get("level"))
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], {"key": key, "targets": _targets(wand)})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "cornelius_wand/update_target",
        ENTRY: str,
        vol.Required("key"): str,
        # Hors de [0, 1] : objet hors champ, cliqué dans la marge autour de la photo
        vol.Optional("x"): vol.All(vol.Coerce(float), vol.Range(min=-MARGIN_X, max=1 + MARGIN_X)),
        vol.Optional("y"): vol.All(vol.Coerce(float), vol.Range(min=-MARGIN_Y, max=1 + MARGIN_Y)),
        vol.Optional("level"): vol.Any(None, vol.In(LEVELS)),
    }
)
@websocket_api.require_admin
@callback
def ws_update_target(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (wand := _wand(hass, connection, msg)) is None:
        return
    try:
        if msg["key"] not in wand.targets:
            raise ValueError(f"cible inconnue : {msg['key']}")
        wand.update_target(msg["key"], msg.get("x"), msg.get("y"), msg.get("level", "keep"))
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], {"targets": _targets(wand)})


@websocket_api.websocket_command(
    {vol.Required("type"): "cornelius_wand/delete_target", ENTRY: str, vol.Required("key"): str}
)
@websocket_api.require_admin
@callback
def ws_delete_target(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (wand := _wand(hass, connection, msg)) is None:
        return
    if msg["key"] not in wand.targets:  # supprimée entre-temps, depuis un autre onglet
        connection.send_error(msg["id"], "invalid", f"cible déjà supprimée : {msg['key']}")
        return
    wand.delete_target(msg["key"])
    connection.send_result(msg["id"], {"targets": _targets(wand)})
