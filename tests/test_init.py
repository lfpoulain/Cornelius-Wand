"""Tests de bout en bout : annonces, vision, cibles et sorts, avec un vrai Home Assistant."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import mock_restore_cache_with_extra_data

from custom_components.cornelius_wand import panel
from custom_components.cornelius_wand.const import DOMAIN, EVENT_SPELL
from custom_components.cornelius_wand.parser import NO_AIM_PITCH
from custom_components.cornelius_wand.vision import IMAGES_HEADER, PHOTO_PART, parse_response
from custom_components.cornelius_wand.wand import QUIET_GUARD, Look
from homeassistant.components import bluetooth
from homeassistant.components.frontend import DATA_PANELS
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.setup import async_setup_component

from .helpers import (
    SPELL_ONLY,
    CEILING,
    FLOOR,
    JEV_CALLS,
    JEV_CALLS_AWAKE,
    aim,
    AIMED,
    CAMERA,
    IMAGE,
    JEV,
    PHOTO,
    PITCH,
    SPELL,
    STATUS,
    TARGETS,
    TOLERANCE,
    VISION,
    beacon,
    jev_answer,
    receive,
    see,
    setup_wand,
    wand_entry,
)


def spells(hass: HomeAssistant) -> list[dict]:
    """Événements Sort reçus, dans l'ordre (écouteur appelé dans la boucle, pas dans un fil)."""
    events: list[dict] = []
    hass.bus.async_listen(EVENT_SPELL, callback(lambda event: events.append(event.data)))
    return events


def captures(aioclient_mock) -> list[int]:
    """Âge demandé à la caméra, pour chaque photo."""
    return [int(call[1].query["ago"]) for call in aioclient_mock.mock_calls if call[0] == "GET"]


def place_targets(wand) -> None:
    """Vues depuis le centre : lampadaire à droite, couloir en bas, plafonnier au plafond."""
    for name, x, y, level in (
        ("Lampadaire", 0.95, 0.5, None),
        ("Couloir", 0.5, 0.98, "mur"),
        ("Plafonnier", 0.52, 0.5, "plafond"),
    ):
        wand.update_target(wand.add_target(name), x, y, level)


async def test_spell_without_vision(hass: HomeAssistant) -> None:
    wand = await setup_wand(hass)
    events = spells(hass)

    receive(wand, 1, 1, pitch=12.3)
    await hass.async_block_till_done()

    spell = hass.states.get(SPELL)
    assert spell.attributes["event_type"] == "alohomora"
    assert spell.attributes["cible"] is None
    assert spell.attributes["inclinaison"] == 12.3
    assert spell.attributes["surface"] == "mur"
    assert "vision non configurée" in spell.attributes["refus"]
    assert hass.states.get(PITCH).state == "12.3"
    assert events[0]["sort"] == "alohomora"


async def test_repeated_announcement_is_one_spell(hass: HomeAssistant) -> None:
    wand = await setup_wand(hass)
    events = spells(hass)
    for _ in range(3):  # la rafale d'une seconde, parfois par plusieurs proxys
        receive(wand, 5, 2, pitch=0)
    await hass.async_block_till_done()
    assert [e["sort"] for e in events] == ["lumos"]


async def test_button_without_spell_name(hass: HomeAssistant) -> None:
    wand = await setup_wand(hass)
    receive(wand, 1, 4, pitch=NO_AIM_PITCH)
    await hass.async_block_till_done()
    assert hass.states.get(SPELL).attributes["event_type"] == "inconnu"
    assert hass.states.get(SPELL).attributes["inclinaison"] is None


async def test_preparation_then_spell(hass: HomeAssistant, aioclient_mock, clock) -> None:
    """La photo de la visée est analysée pendant le geste : le sort n'attend plus Jev."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    see(aioclient_mock, hand=(0.62, 0.5))  # bras vers la droite : le lampadaire

    receive(wand, 1, 1, pitch=5, aim_age_ms=300, preparing=True)
    await hass.async_block_till_done()
    assert hass.states.get(SPELL).state == "unknown"  # pas encore de sort

    clock(hass, 0.6)  # le geste se termine : la même pause de visée, 600 ms plus loin
    receive(wand, 2, 1, pitch=5, aim_age_ms=900)
    await hass.async_block_till_done()

    spell = hass.states.get(SPELL).attributes
    assert spell["cible"] == "lampadaire"
    assert spell["nom_cible"] == "Lampadaire"
    assert spell["direction"] == 0
    assert spell["ecart"] == 0
    assert (spell["position_x"], spell["position_y"]) == (0.5, 0.5)
    assert spell["vision"] == "ok"
    assert spell["attente_ms"] < 50
    assert spell["analyse"] == "préparation"
    assert 550 <= spell["avance_ms"] < 700
    assert hass.states.get(AIMED).state == "Lampadaire"
    assert hass.states.get(VISION).state == "0.50, 0.50"
    assert hass.states.get(STATUS).state.startswith("Alohomora → Lampadaire (écart 0°)")

    # Une seule photo, celle de la visée : 300 ms avant la préparation, plus la marge
    camera, jev = aioclient_mock.mock_calls  # deux appels seulement
    assert 450 <= int(camera[1].query["ago"]) < 600
    assert jev[3]["Authorization"] == "Bearer ts-test"
    assert jev[3][IMAGES_HEADER] == "true"
    parts = {field[0]["name"]: field[2] for field in jev[2]._fields}
    assert parts[PHOTO_PART] == PHOTO
    assert json.loads(parts["request"])["questions"]["main"]["type"] == "box"
    assert hass.states.get(IMAGE).state != "unknown"


async def test_aimed_target_is_current_when_the_spell_fires(hass: HomeAssistant, aioclient_mock) -> None:
    """Une automatisation lancée par l'entité Sort lit le capteur Cible visée dans la foulée :
    il doit déjà donner la cible de CE sort, pas celle du précédent."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    seen: list[str] = []

    @callback
    def on_state(event) -> None:  # appelé pendant l'écriture de l'état, comme un déclencheur
        if event.data["entity_id"] == SPELL:
            seen.append(hass.states.get(AIMED).state)

    hass.bus.async_listen("state_changed", on_state)
    see(aioclient_mock, hand=(0.62, 0.5))  # lampadaire
    receive(wand, 1, 1, pitch=0, aim_age_ms=1200)
    await hass.async_block_till_done()
    see(aioclient_mock, hand=(0.5, 0.62))  # couloir
    receive(wand, 2, 1, pitch=0, aim_age_ms=1200)
    await hass.async_block_till_done()
    assert seen == ["Lampadaire", "Couloir"]


async def test_spell_without_preparation(hass: HomeAssistant, aioclient_mock) -> None:
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    see(aioclient_mock, hand=(0.5, 0.62))  # bras vers le bas : le couloir

    receive(wand, 1, 1, pitch=0, aim_age_ms=1200)
    await hass.async_block_till_done()
    spell = hass.states.get(SPELL).attributes
    assert spell["cible"] == "couloir"
    assert (spell["analyse"], spell["avance_ms"]) == ("sort", 0)
    assert int(aioclient_mock.mock_calls[0][1].query["ago"]) >= 1350


async def test_aim_then_preparation_then_spell(hass: HomeAssistant, aioclient_mock, clock) -> None:
    """Analyse lancée dès la fin de la pause : la préparation et le sort la reprennent."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    see(aioclient_mock, hand=(0.62, 0.5))

    aim(wand, 3, pitch=5, aim_age_ms=40)
    await hass.async_block_till_done()
    assert hass.states.get(SPELL).state == "unknown"  # une visée n'est pas un sort
    assert wand.pitch == 5
    assert 190 <= captures(aioclient_mock)[0] < 250  # la photo de la pause, juste avant sa fin

    clock(hass, 0.7)
    receive(wand, 4, 1, pitch=5, aim_age_ms=740, preparing=True)
    clock(hass, 0.3)
    receive(wand, 5, 1, pitch=5, aim_age_ms=1040)
    await hass.async_block_till_done()

    spell = hass.states.get(SPELL).attributes
    assert spell["cible"] == "lampadaire"
    assert spell["analyse"] == "visée"
    assert 950 <= spell["avance_ms"] < 1100
    assert spell["attente_ms"] < 50
    assert len(captures(aioclient_mock)) == 1  # une seule photo, une seule analyse
    assert "analyse dès la visée" in hass.states.get(STATUS).state


async def test_jev_calls_are_counted(hass: HomeAssistant, aioclient_mock, clock) -> None:
    """Un appel par photo analysée : la visée reprise par le sort ne compte qu'une fois."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    see(aioclient_mock)
    aim(wand, 3, aim_age_ms=40)
    clock(hass, 0.6)
    receive(wand, 4, 1, pitch=0, aim_age_ms=640)
    await hass.async_block_till_done()
    assert (wand.jev_calls, wand.jev_calls_since_wake) == (1, 1)
    assert hass.states.get(JEV_CALLS).state == "1"
    assert hass.states.get(JEV_CALLS_AWAKE).state == "1"
    assert hass.states.get(JEV_CALLS_AWAKE).attributes["reveil"] is not None

    see(aioclient_mock, age=2400)  # photo plus disponible : Jev n'est pas appelé
    receive(wand, 5, 1, pitch=0, aim_age_ms=3150)
    await hass.async_block_till_done()
    assert wand.jev_calls == 1


async def test_wake_up_resets_the_count_since_wake(hass: HomeAssistant, aioclient_mock) -> None:
    wand = await setup_wand(hass, vision=True)
    see(aioclient_mock)
    t = bluetooth.MONOTONIC_TIME()
    receive(wand, 3, 1, pitch=0, at=t)
    await hass.async_block_till_done()
    woke = wand.woke_at

    # Balises identiques : Home Assistant ne rappelle pas l'intégration, le relevé régulier
    # voit quand même la baguette émettre
    for later in (2, 4, 6):
        wand.check_presence(heard_at=t + later)
    beacon(wand, at=t + 8)
    assert (wand.jev_calls, wand.jev_calls_since_wake) == (1, 1)
    assert wand.woke_at == woke

    beacon(wand, at=t + 20)  # 12 s de silence : elle dormait
    await hass.async_block_till_done()
    assert (wand.jev_calls, wand.jev_calls_since_wake) == (1, 0)
    assert wand.woke_at != woke
    assert hass.states.get(JEV_CALLS_AWAKE).state == "0"
    assert hass.states.get(JEV_CALLS).state == "1"


async def test_aim_repeated_during_its_burst_is_analyzed_once(hass: HomeAssistant, aioclient_mock) -> None:
    wand = await setup_wand(hass, vision=True)
    see(aioclient_mock)
    aim(wand, 3, aim_age_ms=40)
    aim(wand, 3, aim_age_ms=100)  # même annonce, âge remis à jour
    beacon(wand)  # puis la présence, avec le numéro de la visée
    await hass.async_block_till_done()
    assert len(captures(aioclient_mock)) == 1


async def test_new_pause_replaces_the_aim_analysis(hass: HomeAssistant, aioclient_mock, clock) -> None:
    """Deux pauses avant le geste : le sort vise la seconde, la première ne sert plus."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    see(aioclient_mock, hand=(0.38, 0.5))
    aim(wand, 3, aim_age_ms=40)
    await hass.async_block_till_done()

    clock(hass, 0.8)
    see(aioclient_mock, hand=(0.5, 0.62))  # le couloir, vu à la seconde pause
    aim(wand, 4, aim_age_ms=40)
    clock(hass, 0.6)
    receive(wand, 5, 1, pitch=0, aim_age_ms=640)
    await hass.async_block_till_done()
    spell = hass.states.get(SPELL).attributes
    assert spell["cible"] == "couloir"
    assert spell["analyse"] == "visée"
    assert len(captures(aioclient_mock)) == 1  # depuis la seconde pause


async def test_running_aim_analysis_is_reused_or_replaced(hass: HomeAssistant, clock) -> None:
    """Analyse de la visée encore en cours : le sort l'attend si c'est la même pause ;
    une préparation d'une autre pause l'annule et en lance une nouvelle."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    release = asyncio.Event()
    started = []

    async def look(ago_ms):
        started.append(ago_ms)
        await release.wait()
        return Look(PHOTO, parse_response(jev_answer(hand=(0.62, 0.5)), 4 / 3, 170, None), None)

    with patch.object(wand, "async_look", look):
        aim(wand, 3, aim_age_ms=40)
        await asyncio.sleep(0)
        first = wand._analysis.task
        clock(hass, 0.5)
        receive(wand, 4, 1, pitch=0, aim_age_ms=100, preparing=True)  # une autre pause, plus récente
        await asyncio.sleep(0)
        assert first.cancelled() or first.cancelling()
        assert wand._analysis.source == "préparation"

        clock(hass, 0.3)
        receive(wand, 5, 1, pitch=0, aim_age_ms=400)  # la pause de la préparation
        await asyncio.sleep(0)
        release.set()
        await hass.async_block_till_done()

    spell = hass.states.get(SPELL).attributes
    assert len(started) == 2  # la visée, puis la préparation ; rien de plus pour le sort
    assert (spell["cible"], spell["analyse"]) == ("lampadaire", "préparation")


async def test_failed_aim_analysis_is_retried_by_the_spell(hass: HomeAssistant, aioclient_mock, clock) -> None:
    """Caméra injoignable à la visée : le sort ne reprend pas l'erreur, il refait la photo de la visée."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    aioclient_mock.get(f"{CAMERA}/capture.jpg", status=503)
    aim(wand, 3, aim_age_ms=40)
    await hass.async_block_till_done()
    assert wand.jev_calls == 0

    see(aioclient_mock, hand=(0.62, 0.5))  # la caméra répond de nouveau
    clock(hass, 0.6)
    receive(wand, 4, 1, pitch=0, aim_age_ms=640)
    await hass.async_block_till_done()
    spell = hass.states.get(SPELL).attributes
    assert (spell["cible"], spell["vision"]) == ("lampadaire", "ok")
    assert (spell["analyse"], spell["avance_ms"]) == ("sort", 0)
    assert 790 <= captures(aioclient_mock)[0] < 850  # le même instant visé
    assert wand.jev_calls == 1


async def test_failed_aim_analysis_is_kept_without_time_to_retry(hass: HomeAssistant, aioclient_mock, clock) -> None:
    """Plus le temps d'une nouvelle analyse avant VISION_DEADLINE : l'erreur de la visée est rendue."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    aioclient_mock.get(f"{CAMERA}/capture.jpg", status=503)
    aim(wand, 3, aim_age_ms=40)
    await hass.async_block_till_done()

    clock(hass, 0.6)
    with patch("custom_components.cornelius_wand.wand.RETRY_MIN_TIME", 2.0):
        receive(wand, 4, 1, pitch=0, aim_age_ms=640)
        await hass.async_block_till_done()
    spell = hass.states.get(SPELL).attributes
    assert spell["cible"] is None
    assert "503" in spell["vision"]
    assert spell["analyse"] == "visée"
    assert len(captures(aioclient_mock)) == 1 and wand.jev_calls == 0


async def test_jev_error_at_the_aim_is_not_retried(hass: HomeAssistant, aioclient_mock, clock) -> None:
    """Jev a répondu par une erreur à la visée : le sort ne le rappelle pas pour la même réponse."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    aioclient_mock.get(f"{CAMERA}/capture.jpg", content=PHOTO)
    aioclient_mock.post(JEV, status=429, text="quota")
    aim(wand, 3, aim_age_ms=40)
    await hass.async_block_till_done()

    clock(hass, 0.6)
    receive(wand, 4, 1, pitch=0, aim_age_ms=640)
    await hass.async_block_till_done()
    spell = hass.states.get(SPELL).attributes
    assert "erreur 429" in spell["vision"]
    assert spell["analyse"] == "visée"
    assert len(captures(aioclient_mock)) == 1 and wand.jev_calls == 1


async def test_retry_skipped_when_the_camera_no_longer_has_the_photo(hass: HomeAssistant, aioclient_mock, clock) -> None:
    """Geste lent : la photo de la visée serait plus vieille que ce que garde la caméra."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    aioclient_mock.get(f"{CAMERA}/capture.jpg", status=503)
    aim(wand, 3, aim_age_ms=40)
    await hass.async_block_till_done()

    clock(hass, 2.8)  # âge demandé au nouvel essai : 2840 + 150 ms, au-delà des 2,6 s gardées (+ 300 ms d'écart)
    receive(wand, 4, 1, pitch=0, aim_age_ms=2840)
    await hass.async_block_till_done()
    assert len(captures(aioclient_mock)) == 1  # pas de seconde demande de photo
    assert "503" in hass.states.get(SPELL).attributes["vision"]


async def test_replayed_advertisements_are_ignored(hass: HomeAssistant, aioclient_mock) -> None:
    """Au démarrage, Home Assistant rejoue sa dernière annonce, ancienne : ni sort, ni
    visée, ni préparation, ni réveil."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    see(aioclient_mock)
    events = spells(hass)
    old = bluetooth.MONOTONIC_TIME() - 60
    receive(wand, 4, 0, pitch=0, aim_age_ms=40, at=old)  # visée
    receive(wand, 5, 1, pitch=0, preparing=True, at=old)
    receive(wand, 6, 1, pitch=0, at=old)
    await hass.async_block_till_done()
    assert not events and not aioclient_mock.mock_calls
    assert (wand._analysis, wand._last_packet_id, wand.woke_at) == (None, None, None)

    receive(wand, 6, 1, pitch=0)  # le même sort, reçu à l'instant : il part
    await hass.async_block_till_done()
    assert len(events) == 1
    assert wand.woke_at is not None


async def test_close_pauses_are_each_analyzed(hass: HomeAssistant, aioclient_mock, clock) -> None:
    """Deux pauses à moins de 0,2 s : chaque visée annonce une nouvelle pause, analysée."""
    wand = await setup_wand(hass, vision=True)
    see(aioclient_mock)
    aim(wand, 3, aim_age_ms=40)
    await hass.async_block_till_done()
    clock(hass, 0.15)
    aim(wand, 4, aim_age_ms=40)
    await hass.async_block_till_done()
    assert len(captures(aioclient_mock)) == 2


async def test_old_aim_analysis_is_not_reused(hass: HomeAssistant, aioclient_mock, clock) -> None:
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    see(aioclient_mock)
    aim(wand, 3, aim_age_ms=40)
    await hass.async_block_till_done()
    clock(hass, 4.5)  # au-delà de 4 s : geste abandonné puis repris sur la même pause ?
    receive(wand, 4, 1, pitch=0, aim_age_ms=4540)
    await hass.async_block_till_done()
    assert hass.states.get(SPELL).attributes["analyse"] == "sort"


async def test_aim_without_vision_guards_the_gesture_without_photo(hass: HomeAssistant, aioclient_mock, clock) -> None:
    """Sans vision, la visée n'est pas une simple présence : ni photo ni Jev, mais le geste
    qui commence peut-être écarte la connexion des réglages."""
    wand = await setup_wand(hass)
    beacon(wand)  # réveil : sa propre garde, finie avant la visée
    clock(hass, QUIET_GUARD + 0.1)
    before = wand._gesture_until
    aim(wand, 3, pitch=20)
    await hass.async_block_till_done()
    assert wand.pitch == 20
    assert wand._last_packet_id == 3
    assert wand._analysis is None
    assert wand._gesture_until > before + QUIET_GUARD  # la visée a repoussé la connexion
    assert not aioclient_mock.mock_calls


async def test_ceiling_target_with_the_arm_raised(hass: HomeAssistant, aioclient_mock) -> None:
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    see(aioclient_mock, hand=(0.51, 0.5))  # bras levé vers la caméra

    receive(wand, 1, 1, pitch=70)
    await hass.async_block_till_done()
    assert hass.states.get(SPELL).attributes["cible"] == "plafonnier"
    assert hass.states.get(SPELL).attributes["surface"] == "plafond"


async def test_no_target_in_that_direction(hass: HomeAssistant, aioclient_mock) -> None:
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    see(aioclient_mock, hand=(0.38, 0.5))  # vers la gauche : rien

    receive(wand, 1, 1, pitch=0)
    await hass.async_block_till_done()
    spell = hass.states.get(SPELL).attributes
    assert spell["cible"] is None
    assert spell["refus"].startswith("la plus proche")


async def test_spell_still_fires_when_vision_fails(hass: HomeAssistant, aioclient_mock) -> None:
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    aioclient_mock.get("http://camera.local/capture.jpg", content=PHOTO)
    aioclient_mock.post("https://api.typesafe.ai/v1/systemone", status=401, text="clé invalide")

    receive(wand, 1, 1, pitch=0)
    await hass.async_block_till_done()
    spell = hass.states.get(SPELL).attributes
    assert spell["event_type"] == "alohomora"
    assert spell["cible"] is None
    assert "erreur 401" in spell["vision"]


async def test_targets_and_tolerance_are_kept(hass: HomeAssistant, hass_storage) -> None:
    wand = await setup_wand(hass)
    place_targets(wand)
    await hass.services.async_call("number", "set_value", {"entity_id": TOLERANCE, "value": 40}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(TARGETS).state == "3"
    assert hass.states.get(TARGETS).attributes["cibles"]["couloir"] == {
        "nom": "Couloir", "x": 0.5, "y": 0.98, "surface": "mur"
    }
    assert wand.tolerance == 40

    wand.delete_target("couloir")
    saved = wand._data_to_save()
    assert list(saved["targets"]) == ["lampadaire", "plafonnier"]
    assert saved["tolerance"] == 40


async def test_spell_only_mode(hass: HomeAssistant, aioclient_mock, clock) -> None:
    """Mode sort uniquement : le geste suffit, ni photo ni Jev, l'événement part tout de suite."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    see(aioclient_mock)
    events = spells(hass)
    assert hass.states.get(SPELL_ONLY).state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": SPELL_ONLY}, blocking=True)
    assert wand.spell_only and wand._data_to_save()["spell_only"] is True

    aim(wand, 3, aim_age_ms=40)
    receive(wand, 4, 1, pitch=5, aim_age_ms=300, preparing=True)
    receive(wand, 5, 1, pitch=5, aim_age_ms=500)
    await hass.async_block_till_done()
    assert not aioclient_mock.mock_calls and wand.jev_calls == 0
    assert events[0]["sort"] == "alohomora"
    assert events[0]["cible"] is None
    assert events[0]["refus"] == "mode sort uniquement"
    assert events[0]["vision"] == "mode sort uniquement"
    assert events[0]["attente_ms"] is None
    assert events[0]["surface"] == "mur"  # l'inclinaison reste donnée

    # Repassé en visée : la cible est de nouveau cherchée
    await hass.services.async_call("switch", "turn_off", {"entity_id": SPELL_ONLY}, blocking=True)
    receive(wand, 6, 1, pitch=5, aim_age_ms=500)
    await hass.async_block_till_done()
    assert events[1]["cible"] == "lampadaire"


async def test_surface_thresholds_are_settings(hass: HomeAssistant) -> None:
    """Seuils déplacés : la même inclinaison vise le mur, et le sort le dit."""
    wand = await setup_wand(hass)
    assert hass.states.get(CEILING).state == "45.0"
    assert hass.states.get(FLOOR).state == "-30.0"
    for entity_id, value in ((CEILING, 60), (FLOOR, -20)):
        await hass.services.async_call("number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True)
    assert (wand.ceiling_pitch, wand.floor_pitch) == (60, -20)
    assert {k: wand._data_to_save()[k] for k in ("ceiling_pitch", "floor_pitch")} == {"ceiling_pitch": 60, "floor_pitch": -20}

    receive(wand, 1, 1, pitch=50)
    await hass.async_block_till_done()
    assert hass.states.get(SPELL).attributes["surface"] == "mur"


async def test_target_name_needs_a_letter_or_digit(hass: HomeAssistant) -> None:
    wand = await setup_wand(hass)
    for name in ("  ", "!!!"):
        with pytest.raises(ValueError, match="vide ou sans lettre ni chiffre"):
            wand.add_target(name)
    assert wand.add_target(" Prise TV ") == "prise_tv"
    assert wand.targets["prise_tv"].name == "Prise TV"
    assert wand.add_target("Unknown!") == "unknown"  # des lettres, même si le nom simplifié vaut « unknown »


async def test_spell_aimed_at_another_moment(hass: HomeAssistant, aioclient_mock, clock) -> None:
    """La baguette a retenu une autre pause au sort : nouvelle photo, celle de cette visée."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    see(aioclient_mock, hand=(0.62, 0.5))

    receive(wand, 1, 1, pitch=5, aim_age_ms=300, preparing=True)
    await hass.async_block_till_done()
    clock(hass, 1)
    receive(wand, 2, 1, pitch=5, aim_age_ms=200)
    await hass.async_block_till_done()

    first, second = captures(aioclient_mock)
    assert 450 <= first < 550
    assert 350 <= second < 450
    assert hass.states.get(SPELL).attributes["cible"] == "lampadaire"


async def test_spell_without_aim_ignores_the_preparation(hass: HomeAssistant, aioclient_mock) -> None:
    """Sans visée au sort, la photo d'un instant quelconque ne désignerait rien."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    see(aioclient_mock)

    receive(wand, 1, 1, pitch=5, aim_age_ms=300, preparing=True)
    await hass.async_block_till_done()
    receive(wand, 2, 1, pitch=NO_AIM_PITCH, aim_age_ms=0)
    await hass.async_block_till_done()

    spell = hass.states.get(SPELL).attributes
    assert spell["cible"] is None
    assert spell["refus"] == "visée inconnue (aucune pause de visée retenue avant le geste)"
    assert spell["vision"] is None
    assert len(aioclient_mock.mock_calls) == 2  # la préparation seule : rien pour le sort


async def test_unreadable_jev_answer_still_fires_the_spell(hass: HomeAssistant, aioclient_mock) -> None:
    wand = await setup_wand(hass, vision=True)
    events = spells(hass)
    aioclient_mock.get(f"{CAMERA}/capture.jpg", content=PHOTO)
    aioclient_mock.post(JEV, text="{tronqué", headers={"Content-Type": "application/json"})

    receive(wand, 1, 1, pitch=0)
    await hass.async_block_till_done()
    assert events[0]["vision"] == "réponse illisible de Jev"


async def test_slow_vision_does_not_hold_the_spell(hass: HomeAssistant, aioclient_mock) -> None:
    wand = await setup_wand(hass, vision=True)
    events = spells(hass)
    aioclient_mock.get(f"{CAMERA}/capture.jpg", content=PHOTO)

    async def never(*args):
        await asyncio.Future()

    with (
        patch("custom_components.cornelius_wand.wand.analyze", never),
        patch("custom_components.cornelius_wand.wand.VISION_DEADLINE", 0.1),
    ):
        receive(wand, 1, 1, pitch=0)
        await hass.async_block_till_done()
    assert events[0]["vision"] == "vision trop lente"
    assert events[0]["attente_ms"] < 500


async def test_photo_no_longer_kept_by_the_camera(hass: HomeAssistant, aioclient_mock) -> None:
    """La caméra rend sa plus vieille photo : trop loin de la visée, Jev n'est pas appelé."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    see(aioclient_mock, age=2400)

    receive(wand, 1, 1, pitch=0, aim_age_ms=3150)
    await hass.async_block_till_done()
    assert "photo de la visée plus disponible" in hass.states.get(SPELL).attributes["vision"]
    assert len(aioclient_mock.mock_calls) == 1


async def test_replaced_preparation_is_not_kept(hass: HomeAssistant, aioclient_mock, clock) -> None:
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    see(aioclient_mock, hand=(0.38, 0.5))
    receive(wand, 1, 1, pitch=0, aim_age_ms=300, preparing=True)
    await hass.async_block_till_done()

    clock(hass, 1)
    see(aioclient_mock, hand=(0.5, 0.62))  # geste repris : la nouvelle visée est celle du sort
    receive(wand, 2, 1, pitch=0, aim_age_ms=300, preparing=True)
    receive(wand, 3, 1, pitch=0, aim_age_ms=300)
    await hass.async_block_till_done()
    assert wand.last_vision.hand.center == (0.5, 0.62)
    assert hass.states.get(SPELL).attributes["cible"] == "couloir"


async def test_late_spell_does_not_overwrite_the_newer_one(hass: HomeAssistant) -> None:
    """Deux sorts rapprochés : l'événement part pour chacun, l'état reste celui du dernier."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    events = spells(hass)
    release = asyncio.Event()
    looks = [
        Look(PHOTO, parse_response(jev_answer(hand=(0.62, 0.5)), 4 / 3, 170, None), None),  # lampadaire
        Look(PHOTO, parse_response(jev_answer(hand=(0.5, 0.62)), 4 / 3, 170, None), None),  # couloir
    ]

    async def look(ago_ms):
        first = len(looks) == 2
        result = looks.pop(0)
        if first:  # la vision du premier sort traîne
            await release.wait()
        return result

    with patch.object(wand, "async_look", look):
        receive(wand, 1, 1, pitch=0)
        receive(wand, 2, 2, pitch=0)
        await asyncio.sleep(0)
        release.set()
        await hass.async_block_till_done()

    assert [e["cible"] for e in events] == ["couloir", "lampadaire"]
    assert wand.aimed_target == "couloir"
    assert hass.states.get(STATUS).state.startswith("Lumos → Couloir")


async def test_preparation_relayed_late_after_its_spell(hass: HomeAssistant, aioclient_mock) -> None:
    """Un proxy en retard relaie la préparation N après le sort N+1, puis le sort lui-même."""
    wand = await setup_wand(hass, vision=True)
    place_targets(wand)
    see(aioclient_mock)
    events = spells(hass)

    receive(wand, 6, 1, pitch=0)
    await hass.async_block_till_done()
    receive(wand, 5, 1, pitch=0, preparing=True)
    aim(wand, 4)  # visée de ce geste, relayée encore plus tard
    receive(wand, 6, 1, pitch=0)
    await hass.async_block_till_done()
    assert len(events) == 1
    assert len(captures(aioclient_mock)) == 1


async def test_beacon_updates_the_voltage(hass: HomeAssistant) -> None:
    """La balise reprend le numéro du dernier sort : sa tension est lue quand même."""
    wand = await setup_wand(hass)
    receive(wand, 7, 1, pitch=0, voltage=2.95)
    beacon(wand, voltage=2.81)
    await hass.async_block_till_done()
    assert wand.voltage == 2.81


async def test_two_wands_share_the_panel(hass: HomeAssistant) -> None:
    """Le panneau est enregistré une fois, avant les baguettes installées en parallèle."""
    wand_entry(hass)
    wand_entry(hass, address="02:00:00:00:00:02")
    hass.http.async_register_static_paths = AsyncMock()
    with patch("custom_components.cornelius_wand.async_setup_panel", panel.async_setup_panel):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
    assert [e.state for e in hass.config_entries.async_entries(DOMAIN)] == [ConfigEntryState.LOADED] * 2
    assert panel.PANEL_PATH in hass.data[DATA_PANELS]


async def test_reload_keeps_unsaved_targets(hass: HomeAssistant) -> None:
    wand = await setup_wand(hass)
    place_targets(wand)
    wand.update_target("couloir", 0.4, 0.9)
    assert await hass.config_entries.async_reload(wand.entry.entry_id)  # avant l'écriture différée
    await hass.async_block_till_done()
    reloaded = wand.entry.runtime_data
    assert reloaded is not wand
    assert list(reloaded.targets) == ["lampadaire", "couloir", "plafonnier"]
    assert reloaded.targets["couloir"].position == (0.4, 0.9)


async def test_saved_data_is_read_back(hass: HomeAssistant, hass_storage) -> None:
    entry = wand_entry(hass)
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {
            "targets": {
                "lampe": {"name": "Lampe", "x": 0.9, "y": 0.5, "level": "mur"},
                "abimee": {"x": 0.1},  # sans nom : ignorée
            },
            "tolerance": 40,
            "remote_values": {"spell_threshold": 0.7},
            "remote_pending": {"sleep_after": 60},
            "jev_calls": 412,
        },
    }
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    wand = entry.runtime_data
    assert wand.jev_calls == 412
    assert hass.states.get(JEV_CALLS).state == "412"
    assert list(wand.targets) == ["lampe"]
    assert (wand.targets["lampe"].position, wand.targets["lampe"].level) == ((0.9, 0.5), "mur")
    assert wand.tolerance == 40
    assert wand.remote_display_value("spell_threshold") == 70
    assert wand.remote_display_value("sleep_after") == 60


async def test_removed_wand_forgets_its_data(hass: HomeAssistant, hass_storage) -> None:
    wand = await setup_wand(hass)
    place_targets(wand)
    entry_id = wand.entry.entry_id
    await hass.config_entries.async_remove(entry_id)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{entry_id}" not in hass_storage


async def test_restored_values_survive_a_beacon(hass: HomeAssistant) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        (
            (State(PITCH, "30"), {"native_value": 30, "native_unit_of_measurement": "°"}),
            (State(AIMED, "Lampe"), {"native_value": "Lampe", "native_unit_of_measurement": None}),
        ),
    )
    wand = await setup_wand(hass)
    beacon(wand)  # sans visée ni sort
    await hass.async_block_till_done()
    assert hass.states.get(PITCH).state == "30"
    assert hass.states.get(AIMED).state == "Lampe"

    receive(wand, 1, 1, pitch=12.3)
    await hass.async_block_till_done()
    assert hass.states.get(PITCH).state == "12.3"
    assert hass.states.get(AIMED).state == "unknown"


async def test_long_status_is_truncated(hass: HomeAssistant) -> None:
    wand = await setup_wand(hass)
    wand.status = "x" * 300
    wand._notify()
    await hass.async_block_till_done()
    state = hass.states.get(STATUS)
    assert len(state.state) == 255 and state.state.endswith("…")
    assert state.attributes["message"] == "x" * 300
