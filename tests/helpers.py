"""Outils communs aux tests : annonces BTHome, photo, réponses de Jev, baguette installée."""

import io
import struct
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cornelius_wand.const import (
    BTHOME_SERVICE_UUID,
    CONF_CAMERA_URL,
    CONF_SPELLS,
    CONF_TYPESAFE_API_KEY,
    DOMAIN,
)
from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

ADDRESS = "02:00:00:00:00:01"
CAMERA = "http://camera.local"
JEV = "https://api.typesafe.ai/v1/systemone"

SPELL = "event.baguette_cornelius_spell"
STATUS = "sensor.baguette_cornelius_status"
AIMED = "sensor.baguette_cornelius_aimed_target"
TARGETS = "sensor.baguette_cornelius_targets"
VISION = "sensor.baguette_cornelius_vision"
PITCH = "sensor.baguette_cornelius_pitch"
IMAGE = "image.baguette_cornelius_analyzed_photo"
TOLERANCE = "number.baguette_cornelius_aiming_tolerance"
CEILING = "number.baguette_cornelius_ceiling_threshold"
FLOOR = "number.baguette_cornelius_floor_threshold"
SPELL_ONLY = "switch.baguette_cornelius_spell_only_mode"
JEV_CALLS = "sensor.baguette_cornelius_jev_calls"
JEV_CALLS_AWAKE = "sensor.baguette_cornelius_jev_calls_since_wake_up"


def wand_advert(packet_id, button, pitch, aim_age_ms=300, voltage=2.95, buttons=4, preparing=False):
    """Reproduit advertise() de wand/bthome.cpp."""
    data = bytes([0x44, 0x00, packet_id, 0x0C]) + struct.pack("<H", round(voltage * 1000))
    event = 0x80 if preparing else 0x01
    for b in range(1, buttons + 1):
        data += bytes([0x3A, event if b == button else 0x00])
    data += bytes([0x3F]) + struct.pack("<h", round(pitch * 10))
    data += bytes([0x42]) + aim_age_ms.to_bytes(3, "little")
    return data


def receive(wand, *args, at=None, **kwargs) -> None:
    """Annonce reçue maintenant, ou à l'heure at (horloge de bluetooth.MONOTONIC_TIME)."""
    info = SimpleNamespace(
        service_data={BTHOME_SERVICE_UUID: wand_advert(*args, **kwargs)},
        time=bluetooth.MONOTONIC_TIME() if at is None else at,
    )
    wand.handle_advertisement(info, None)


def aim(wand, packet_id, pitch=0, aim_age_ms=40) -> None:
    """Fin d'une pause de visée : aucun bouton, mais l'inclinaison et l'âge de la pause."""
    receive(wand, packet_id, 0, pitch=pitch, aim_age_ms=aim_age_ms)


def beacon(wand, packet_id=None, voltage=2.95, at=None) -> None:
    """Annonce de présence : aucun bouton, la baguette est réveillée et joignable.

    Comme le firmware, elle reprend le numéro du dernier paquet (visée, préparation ou sort).
    """
    if packet_id is None:
        packet_id = wand._last_packet_id or 0
    receive(wand, packet_id, 0, pitch=-180, aim_age_ms=0, voltage=voltage, at=at)


def jpeg(width=1024, height=768) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), (120, 100, 80)).save(output, format="JPEG")
    return output.getvalue()


PHOTO = jpeg()


def box(x, y, half_w=0.04, half_h=0.05):
    return {"left": x - half_w, "right": x + half_w, "top": y - half_h, "bottom": y + half_h}


def jev_answer(
    shoulders=(0.5, 0.5), hand=(0.62, 0.5), present=0.94, shoulders_confidence=0.86, head="épaules"
) -> dict:
    """Réponse de Jev : épaules, tête et main aux positions données (centres des cadres).

    Par défaut la tête est au centre des épaules ; head=None : Jev ne rend pas de tête.
    """
    answers = {
        "presence": {"type": "noul", "noul": present},
        "epaules": {"type": "box", "box": box(*shoulders), "confidence": shoulders_confidence},
        "main": {"type": "box", "box": box(*hand, 0.02, 0.02), "confidence": 0.7},
    }
    if head is not None:
        center = shoulders if head == "épaules" else head
        answers["tete"] = {"type": "box", "box": box(*center, 0.02, 0.025), "confidence": 0.8}
    return {"model": "jev-1.14.0", "answers": answers, "usage": {"input_tokens": 2442}}


def see(aioclient_mock, shoulders=(0.5, 0.5), hand=(0.62, 0.5), present=0.94, age=None) -> None:
    """La caméra renvoie une photo (de age ms si donné), et Jev y voit ces épaules et cette main."""
    aioclient_mock.clear_requests()
    headers = {"X-Photo-Age-Ms": str(age)} if age is not None else None
    aioclient_mock.get(f"{CAMERA}/capture.jpg", content=PHOTO, headers=headers)
    aioclient_mock.post(JEV, json=jev_answer(shoulders, hand, present))


def wand_entry(hass: HomeAssistant, vision: bool = False, address: str = ADDRESS) -> MockConfigEntry:
    """Entrée de la baguette, ajoutée mais pas encore installée."""
    hass.http = MagicMock()  # l'entité image a besoin du serveur web, absent des tests
    options = {CONF_SPELLS: "Alohomora, Lumos"}
    if vision:
        options |= {CONF_CAMERA_URL: CAMERA, CONF_TYPESAFE_API_KEY: "ts-test"}
    entry = MockConfigEntry(domain=DOMAIN, unique_id=address, data={CONF_ADDRESS: address}, options=options)
    entry.add_to_hass(hass)
    return entry


async def setup_wand(hass: HomeAssistant, vision: bool = False):
    entry = wand_entry(hass, vision)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data
