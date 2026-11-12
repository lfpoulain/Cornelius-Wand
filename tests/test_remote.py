"""Tests des réglages de la baguette en Bluetooth (wand/remote.cpp ↔ remote.py)."""

import asyncio
from datetime import timedelta
from pathlib import Path
import re
import struct
from unittest.mock import AsyncMock, MagicMock, patch

from bleak.exc import BleakCharacteristicNotFoundError, BleakError
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.cornelius_wand.const import DOMAIN
from custom_components.cornelius_wand.remote import (
    COMMAND_CALIBRATE,
    COMMAND_UUID,
    INFO_FORMAT,
    INFO_UUID,
    SERVICE_UUID,
    SETTINGS,
    SETTINGS_BY_KEY,
    RemoteError,
    WandInfo,
    async_exchange,
    char_uuid,
    parse_info,
)
from custom_components.cornelius_wand.wand import COMMAND_RETRY_DELAY, COMMAND_TTL, QUIET_GUARD
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .helpers import STATUS, aim, beacon, receive, setup_wand

FIRMWARE = Path(__file__).parents[1] / "wand"

THRESHOLD = "number.baguette_cornelius_spell_threshold"
SLEEP = "number.baguette_cornelius_sleep_after"
CALIBRATE = "button.baguette_cornelius_calibrate_aiming_axis"
RESET = "button.baguette_cornelius_default_settings"
CALIBRATION = "sensor.baguette_cornelius_calibration"

INFO = WandInfo(firmware=3, calibrated=True, calibration_result=1, voltage=2.9)
WAND_VALUES = {setting.key: setting.default for setting in SETTINGS}


def test_uuids_match_the_firmware():
    remote = (FIRMWARE / "remote.cpp").read_text(encoding="utf-8")
    prefix, suffix = re.search(r'#define REMOTE_UUID\(n\) "(\w+)" n "([-\w]+)"', remote).groups()
    assert char_uuid(0x0001) == f"{prefix}0001{suffix}" == SERVICE_UUID
    # Service, réglages dans l'ordre de Settings, commande, état
    uuids = [f"{prefix}{n}{suffix}" for n in re.findall(r'REMOTE_UUID\("([0-9a-f]{4})"\)', remote)]
    assert uuids == [SERVICE_UUID, *(s.uuid for s in SETTINGS), COMMAND_UUID, INFO_UUID]


def test_settings_match_the_firmware():
    """Bornes de SETTINGS_LIMITS (settings.cpp) et valeurs par défaut [HA] de config.h."""
    settings = (FIRMWARE / "settings.cpp").read_text(encoding="utf-8")
    config = (FIRMWARE / "config.h").read_text(encoding="utf-8")
    limits = re.search(r"SETTINGS_LIMITS\[SETTINGS_COUNT\] = \{(.*?)\n\};", settings, re.S).group(1)
    bounds = [(float(lo), float(hi)) for lo, hi in re.findall(r"\{([-\d.]+)f?, ([-\d.]+)f?\}", limits)]
    assert bounds == [(s.wand_min, s.wand_max) for s in SETTINGS]

    ha = {name: float(value) for name, value in re.findall(r"#define (\w+)\s+([-\d.]+)f?\s*// \[HA\]", config)}
    expressions = re.search(r"Settings DEFAULTS = \{(.*?)\};", settings, re.S).group(1).split(",")
    defaults = []
    for expression in filter(str.strip, expressions):  # « NOM » ou « NOM / 1000.0f »
        name, divisor = re.fullmatch(r"\s*(\w+)(?:\s*/\s*([\d.]+)f?)?\s*", expression).groups()
        defaults.append(ha[name] / float(divisor or 1))
    assert defaults == [pytest.approx(s.default) for s in SETTINGS]


def test_settings_convert_between_display_and_wand_units():
    threshold = SETTINGS_BY_KEY["spell_threshold"]
    assert threshold.to_display(0.6) == 60
    assert threshold.to_wand(75) == 0.75
    assert threshold.to_wand(150) == 0.99  # borné comme sur la baguette


def test_parse_info():
    data = struct.pack(INFO_FORMAT, 3, 0, 2, 2.95)
    assert len(data) == 8
    info = parse_info(data)
    assert (info.firmware, info.calibrated) == (3, False)
    assert info.voltage == pytest.approx(2.95)
    assert info.calibration_text.startswith("ratée : la baguette n'a pas été levée")
    with pytest.raises(RemoteError):
        parse_info(data[:5])


@pytest.mark.parametrize(
    ("error", "cleared"),
    [
        (BleakCharacteristicNotFoundError(char_uuid(0x0019)), True),  # nouveau firmware
        (BleakError("déconnecté"), False),  # sort lancé pendant l'échange : la baguette coupe
    ],
)
async def test_service_cache_is_cleared_only_for_a_missing_characteristic(error, cleared) -> None:
    client = AsyncMock()
    client.read_gatt_char.side_effect = error
    with (
        patch("custom_components.cornelius_wand.remote.establish_connection", return_value=client),
        pytest.raises(RemoteError, match="échange interrompu"),
    ):
        await async_exchange(MagicMock(name="ble_device"), {}, None)
    assert client.clear_cache.await_count == cleared
    client.disconnect.assert_awaited_once()


async def test_command_is_sent_before_reading() -> None:
    """La commande part juste après les réglages : une coupure pendant la relecture ne la perd pas."""
    client = AsyncMock()
    calls: list[str] = []
    client.write_gatt_char.side_effect = lambda uuid, *args, **kwargs: calls.append(f"write {uuid}")
    client.read_gatt_char.side_effect = BleakError("déconnecté")
    with (
        patch("custom_components.cornelius_wand.remote.establish_connection", return_value=client),
        pytest.raises(RemoteError) as caught,
    ):
        await async_exchange(MagicMock(name="ble_device"), {"sleep_after": 60}, COMMAND_CALIBRATE)
    assert calls == [f"write {SETTINGS_BY_KEY['sleep_after'].uuid}", f"write {COMMAND_UUID}"]
    assert caught.value.command_sent


async def test_blocked_exchange_times_out() -> None:
    """Connexion bloquée (proxy) : abandonnée, au lieu de retenir la commande jusqu'au réveil suivant."""

    async def blocked(*args, **kwargs):
        await asyncio.Future()

    with (
        patch("custom_components.cornelius_wand.remote.establish_connection", side_effect=blocked),
        pytest.raises(RemoteError, match="trop long") as caught,
    ):
        await async_exchange(MagicMock(name="ble_device"), {}, COMMAND_CALIBRATE, timeout=0.01)
    assert not caught.value.command_sent


@pytest.fixture
def exchange():
    """Une baguette joignable ; l'échange Bluetooth est simulé."""
    with (
        patch(
            "custom_components.cornelius_wand.wand.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(name="ble_device"),
        ),
        patch("custom_components.cornelius_wand.wand.async_exchange", new_callable=AsyncMock) as mock,
    ):
        mock.side_effect = lambda device, writes, command: ({**WAND_VALUES, **writes}, INFO)
        yield mock


def quiet(hass: HomeAssistant, clock) -> None:
    """Ni réveil ni geste depuis QUIET_GUARD : la connexion peut partir."""
    clock(hass, QUIET_GUARD + 0.1)


def wake_up(hass: HomeAssistant, wand, clock) -> None:
    """La baguette se réveille (sa balise arrive), puis reste au calme."""
    beacon(wand)
    quiet(hass, clock)


async def test_first_wake_reads_the_wand(hass: HomeAssistant, exchange, awake, clock) -> None:
    wand = await setup_wand(hass)
    beacon(wand)
    await hass.async_block_till_done()
    exchange.assert_not_awaited()  # on vient de la prendre, sans doute pour un sort

    quiet(hass, clock)
    await hass.async_block_till_done()
    exchange.assert_awaited_once()
    assert exchange.await_args.args[1:] == ({}, None)
    assert hass.states.get(CALIBRATION).state == "calibrée"
    assert hass.states.get(CALIBRATION).attributes["firmware"] == 3

    beacon(wand, 51)  # déjà lue : plus de connexion
    await hass.async_block_till_done()
    exchange.assert_awaited_once()


async def test_setting_is_sent_at_next_wake(hass: HomeAssistant, exchange, awake, clock) -> None:
    wand = await setup_wand(hass)
    await hass.services.async_call("number", "set_value", {"entity_id": THRESHOLD, "value": 75}, blocking=True)
    assert hass.states.get(THRESHOLD).state == "75.0"
    assert hass.states.get(THRESHOLD).attributes["en_attente"]
    assert "dès que la baguette est réveillée" in hass.states.get(STATUS).state

    wake_up(hass, wand, clock)
    await hass.async_block_till_done()
    assert exchange.await_args.args[1] == {"spell_threshold": 0.75}  # unité de la baguette
    assert not hass.states.get(THRESHOLD).attributes["en_attente"]
    assert hass.states.get(STATUS).state == "1 réglage(s) envoyé(s) à la baguette"


async def test_ignore_unaimed_is_a_switch(hass: HomeAssistant, exchange, awake, clock) -> None:
    """Réglage tout-ou-rien de la baguette : un interrupteur, pas un curseur."""
    wand = await setup_wand(hass)
    entity_id = "switch.baguette_cornelius_ignore_spells_without_aim"
    assert hass.states.get(entity_id).state == "on"  # activé par défaut
    assert hass.states.get("number.baguette_cornelius_ignore_spells_without_aim") is None

    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    assert hass.states.get(entity_id).state == "off"
    assert hass.states.get(entity_id).attributes["en_attente"]
    wake_up(hass, wand, clock)
    await hass.async_block_till_done()
    assert exchange.await_args.args[1] == {"ignore_unaimed": 0}
    assert not hass.states.get(entity_id).attributes["en_attente"]


async def test_spell_announcements_do_not_connect(hass: HomeAssistant, exchange) -> None:
    wand = await setup_wand(hass)
    receive(wand, 1, 1, pitch=10)
    receive(wand, 2, 1, pitch=10, preparing=True)
    await hass.async_block_till_done()
    exchange.assert_not_awaited()


async def test_calibration_command(hass: HomeAssistant, exchange, awake, clock) -> None:
    wand = await setup_wand(hass)
    await hass.services.async_call("button", "press", {"entity_id": CALIBRATE}, blocking=True)
    wake_up(hass, wand, clock)
    await hass.async_block_till_done()
    assert exchange.await_args.args[2] == COMMAND_CALIBRATE
    assert "LED bleue" in hass.states.get(STATUS).state

    beacon(wand, 51)  # la baguette calibre : on attend avant de relire
    await hass.async_block_till_done()
    assert exchange.await_count == 1


async def test_stale_command_is_dropped(hass: HomeAssistant, exchange, clock) -> None:
    """Baguette injoignable pendant COMMAND_TTL : la calibration n'attend pas le réveil suivant."""
    wand = await setup_wand(hass)
    await hass.services.async_call("button", "press", {"entity_id": CALIBRATE}, blocking=True)
    clock(hass, COMMAND_TTL + 1)
    await hass.async_block_till_done()
    assert wand.remote_command is None
    assert hass.states.get(STATUS).state.startswith("Commande abandonnée")

    with patch(
        "homeassistant.components.bluetooth.async_last_service_info",
        side_effect=lambda *args, **kwargs: MagicMock(time=bluetooth.MONOTONIC_TIME()),
    ):
        clock(hass, 6)  # elle se réveille bien plus tard : relue, mais pas calibrée
        quiet(hass, clock)
        await hass.async_block_till_done()
    exchange.assert_awaited_once()
    assert exchange.await_args.args[2] is None


async def test_failed_command_is_retried_quickly(hass: HomeAssistant, exchange, awake, clock) -> None:
    exchange.side_effect = RemoteError("échange interrompu : déconnecté")
    wand = await setup_wand(hass)
    await hass.services.async_call("button", "press", {"entity_id": CALIBRATE}, blocking=True)
    wake_up(hass, wand, clock)
    await hass.async_block_till_done()
    assert exchange.await_count == 1
    assert f"nouvel essai dans {COMMAND_RETRY_DELAY:.0f} s" in hass.states.get(STATUS).state

    exchange.side_effect = lambda device, writes, command: ({**WAND_VALUES, **writes}, INFO)
    clock(hass, COMMAND_RETRY_DELAY + 0.1)
    await hass.async_block_till_done()
    assert exchange.await_count == 2
    assert exchange.await_args.args[2] == COMMAND_CALIBRATE
    assert "LED bleue" in hass.states.get(STATUS).state


async def test_sent_command_is_not_sent_again(hass: HomeAssistant, exchange, awake, clock) -> None:
    """Commande reçue, puis coupure pendant la relecture : pas de seconde calibration plus tard."""
    exchange.side_effect = RemoteError("échange interrompu : déconnecté", command_sent=True)
    wand = await setup_wand(hass)
    await hass.services.async_call("button", "press", {"entity_id": CALIBRATE}, blocking=True)
    wake_up(hass, wand, clock)
    await hass.async_block_till_done()
    assert wand.remote_command is None
    assert "LED bleue" in hass.states.get(STATUS).state

    exchange.side_effect = lambda device, writes, command: ({**WAND_VALUES, **writes}, INFO)
    clock(hass, 31)  # calibration finie : relecture seule
    await hass.async_block_till_done()
    assert exchange.await_count == 2
    assert exchange.await_args.args[1:] == ({}, None)


async def test_default_settings_combine_with_later_changes(hass: HomeAssistant, exchange, awake, clock) -> None:
    """Réglages par défaut : mis en attente, si bien qu'un réglage modifié ensuite passe par-dessus."""
    wand = await setup_wand(hass)
    await hass.services.async_call("number", "set_value", {"entity_id": SLEEP, "value": 60}, blocking=True)
    await hass.services.async_call("button", "press", {"entity_id": RESET}, blocking=True)
    assert hass.states.get(SLEEP).state == "30.0"  # le défaut remplace le réglage d'avant
    assert hass.states.get(SLEEP).attributes["en_attente"]
    await hass.services.async_call("number", "set_value", {"entity_id": THRESHOLD, "value": 75}, blocking=True)

    wake_up(hass, wand, clock)
    await hass.async_block_till_done()
    exchange.assert_awaited_once()
    assert exchange.await_args.args[1:] == ({**WAND_VALUES, "spell_threshold": 0.75}, None)  # aucune commande
    assert hass.states.get(THRESHOLD).state == "75.0"
    assert not hass.states.get(SLEEP).attributes["en_attente"]
    assert wand.status == f"{len(SETTINGS)} réglage(s) envoyé(s) à la baguette"


async def test_failed_exchange_keeps_the_setting(hass: HomeAssistant, exchange, awake, clock) -> None:
    exchange.side_effect = RemoteError("connexion impossible : délai dépassé")
    wand = await setup_wand(hass)
    await hass.services.async_call("number", "set_value", {"entity_id": SLEEP, "value": 60}, blocking=True)
    wake_up(hass, wand, clock)
    await hass.async_block_till_done()
    assert "connexion impossible" in hass.states.get(STATUS).state
    assert hass.states.get(SLEEP).attributes["en_attente"]


async def test_failed_exchange_is_retried(hass: HomeAssistant, exchange, awake, clock) -> None:
    """Pas de nouvelle annonce : la baguette répète une balise identique, que HA ne transmet pas."""
    exchange.side_effect = RemoteError("connexion impossible : délai dépassé")
    wand = await setup_wand(hass)
    await hass.services.async_call("number", "set_value", {"entity_id": SLEEP, "value": 60}, blocking=True)
    quiet(hass, clock)  # baguette vue pour la première fois : réveillée tout juste
    await hass.async_block_till_done()
    assert exchange.await_count == 1
    assert "nouvel essai dans 15 s" in hass.states.get(STATUS).state

    exchange.side_effect = lambda device, writes, command: ({**WAND_VALUES, **writes}, INFO)
    clock(hass, 16)
    await hass.async_block_till_done()
    assert exchange.await_count == 2
    assert not hass.states.get(SLEEP).attributes["en_attente"]
    assert wand.status == "1 réglage(s) envoyé(s) à la baguette"


async def test_setting_sent_while_awake(hass: HomeAssistant, exchange, awake, clock) -> None:
    """Baguette réveillée et au calme : le réglage part tout de suite, sans attendre une autre balise."""
    wand = await setup_wand(hass)
    wake_up(hass, wand, clock)
    await hass.async_block_till_done()
    await hass.services.async_call("number", "set_value", {"entity_id": THRESHOLD, "value": 75}, blocking=True)
    await hass.async_block_till_done()
    assert exchange.await_count == 2
    assert exchange.await_args.args[1] == {"spell_threshold": 0.75}


async def test_wand_reread_after_calibration(hass: HomeAssistant, exchange, awake, clock) -> None:
    wand = await setup_wand(hass)
    wake_up(hass, wand, clock)  # une seule balise : les suivantes, identiques, ne sont pas transmises
    await hass.async_block_till_done()
    await hass.services.async_call("button", "press", {"entity_id": CALIBRATE}, blocking=True)
    assert exchange.await_count == 2
    assert exchange.await_args.args[2] == COMMAND_CALIBRATE

    clock(hass, 10)  # la baguette calibre encore
    await hass.async_block_till_done()
    assert exchange.await_count == 2
    clock(hass, 21)
    await hass.async_block_till_done()
    assert exchange.await_count == 3
    assert exchange.await_args.args[1:] == ({}, None)


async def test_beacon_after_a_spell_connects(hass: HomeAssistant, exchange, awake, clock) -> None:
    """La balise reprend le numéro du sort : la connexion part quand même, après le calme."""
    wand = await setup_wand(hass)
    wake_up(hass, wand, clock)  # réveillée et relue : seule la garde du sort compte ensuite
    await hass.async_block_till_done()
    assert exchange.await_count == 1
    receive(wand, 7, 1, pitch=10)
    await hass.services.async_call("number", "set_value", {"entity_id": SLEEP, "value": 60}, blocking=True)
    beacon(wand)
    await hass.async_block_till_done()
    assert exchange.await_count == 1  # un autre sort s'enchaîne peut-être

    clock(hass, QUIET_GUARD - 0.5)
    await hass.async_block_till_done()
    assert exchange.await_count == 1
    clock(hass, 0.6)
    await hass.async_block_till_done()
    assert exchange.await_count == 2
    assert exchange.await_args.args[1] == {"sleep_after": 60}


async def test_no_connection_during_a_gesture(hass: HomeAssistant, exchange, awake, clock) -> None:
    wand = await setup_wand(hass)
    wake_up(hass, wand, clock)  # réveillée et relue : seule la garde du geste compte ensuite
    await hass.async_block_till_done()
    assert exchange.await_count == 1
    receive(wand, 1, 1, pitch=10, preparing=True)
    await hass.services.async_call("number", "set_value", {"entity_id": SLEEP, "value": 60}, blocking=True)
    beacon(wand)
    await hass.async_block_till_done()
    assert exchange.await_count == 1

    clock(hass, QUIET_GUARD - 0.5)
    await hass.async_block_till_done()
    assert exchange.await_count == 1
    clock(hass, 0.6)  # pas de sort : le geste a été abandonné
    await hass.async_block_till_done()
    assert exchange.await_count == 2
    assert exchange.await_args.args[1] == {"sleep_after": 60}


async def test_no_connection_while_spells_are_chained(hass: HomeAssistant, exchange, awake, clock) -> None:
    """Un sort toutes les 2 à 3 s : chaque visée, préparation ou sort repousse la connexion."""
    wand = await setup_wand(hass)
    wake_up(hass, wand, clock)
    await hass.async_block_till_done()
    assert exchange.await_count == 1  # première relecture

    packet_id = 10
    for _ in range(3):
        aim(wand, packet_id)
        clock(hass, 1)
        receive(wand, packet_id + 1, 1, pitch=0, preparing=True)
        clock(hass, 0.5)
        receive(wand, packet_id + 2, 1, pitch=0)
        await hass.services.async_call("number", "set_value", {"entity_id": SLEEP, "value": 60}, blocking=True)
        beacon(wand)
        clock(hass, 1.5)  # visée suivante 3 s après la précédente
        await hass.async_block_till_done()
        packet_id += 3
    assert exchange.await_count == 1

    quiet(hass, clock)
    await hass.async_block_till_done()
    assert exchange.await_count == 2
    assert exchange.await_args.args[1] == {"sleep_after": 60}


async def test_sleeping_wand_is_watched(hass: HomeAssistant, exchange, clock) -> None:
    """Réglage modifié pendant la veille : il part dès que la baguette se réveille."""
    await setup_wand(hass)
    await hass.services.async_call("number", "set_value", {"entity_id": SLEEP, "value": 60}, blocking=True)
    clock(hass, 6)
    await hass.async_block_till_done()
    exchange.assert_not_awaited()

    with patch(
        "homeassistant.components.bluetooth.async_last_service_info",
        side_effect=lambda *args, **kwargs: MagicMock(time=bluetooth.MONOTONIC_TIME()),
    ):
        clock(hass, 6)
        await hass.async_block_till_done()
        exchange.assert_not_awaited()  # réveil tout juste vu : on attend le calme
        quiet(hass, clock)
        await hass.async_block_till_done()
    exchange.assert_awaited_once()


async def test_replayed_advertisement_does_not_connect(hass: HomeAssistant, exchange, clock) -> None:
    """Home Assistant rejoue sa dernière annonce, ancienne, au démarrage : la baguette dort."""
    wand = await setup_wand(hass)
    beacon(wand, at=bluetooth.MONOTONIC_TIME() - 60)
    await hass.async_block_till_done()
    exchange.assert_not_awaited()
    assert wand.woke_at is None  # pas un réveil
    assert not wand.has_data

    # On guette son réveil pour la relire
    with patch(
        "homeassistant.components.bluetooth.async_last_service_info",
        side_effect=lambda *args, **kwargs: MagicMock(time=bluetooth.MONOTONIC_TIME()),
    ):
        clock(hass, 6)
        quiet(hass, clock)
        await hass.async_block_till_done()
    exchange.assert_awaited_once()
    assert wand.woke_at is not None


async def test_unload_during_an_exchange(hass: HomeAssistant, exchange, awake, clock, hass_storage) -> None:
    """Le déchargement écrit tout de suite, annule l'échange, et l'ancienne baguette n'écrit plus."""

    async def blocked(*args):
        await asyncio.Future()

    exchange.side_effect = blocked
    wand = await setup_wand(hass)
    await hass.services.async_call("number", "set_value", {"entity_id": THRESHOLD, "value": 75}, blocking=True)
    quiet(hass, clock)
    await hass.async_block_till_done()
    assert exchange.await_count == 1
    assert await hass.config_entries.async_unload(wand.entry.entry_id)
    await hass.async_block_till_done()
    key = f"{DOMAIN}.{wand.entry.entry_id}"
    saved = hass_storage[key]
    assert saved["data"]["remote_pending"] == {"spell_threshold": 0.75}

    wand.set_tolerance(40)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()
    assert hass_storage[key] == saved
    assert wand._remote_task.cancelled()


async def test_no_connectable_adapter(hass: HomeAssistant) -> None:
    """Aucun adaptateur ni proxy capable de se connecter n'entend la baguette."""
    with (
        patch("custom_components.cornelius_wand.wand.bluetooth.async_ble_device_from_address", return_value=None),
        patch("custom_components.cornelius_wand.wand.async_exchange", new_callable=AsyncMock) as exchange,
    ):
        wand = await setup_wand(hass)
        beacon(wand)
        await hass.async_block_till_done()
        exchange.assert_not_awaited()
