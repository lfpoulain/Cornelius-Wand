"""Réglages de la baguette en Bluetooth : le service de wand/remote.cpp.

Quand elle est réveillée, la baguette accepte une connexion : Home Assistant y écrit les
réglages modifiés, envoie une commande éventuelle (calibration de l'axe de visée), relit
tous les réglages et son état, puis se déconnecte. La baguette garde les
réglages en flash et exécute la commande une fois déconnectée. « Réglages par défaut »
écrit les valeurs par défaut comme des réglages modifiés (wand.py).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import struct

from bleak.backends.device import BLEDevice
from bleak.exc import BleakCharacteristicNotFoundError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

# Commandes (REMOTE_* dans wand/remote.h)
COMMAND_CALIBRATE = 1
COMMAND_SLEEP = 2  # gérée par le firmware, pas exposée dans Home Assistant
COMMAND_RESET = 3  # gérée par le firmware, pas utilisée : elle écraserait les réglages écrits ensuite

# Calibration de l'axe : bloquante sur la baguette (jusqu'à 30 s), on attend avant de relire
CALIBRATION_DURATION = 30.0
# Échange entier (connexion, écritures, relecture) : au-delà, abandonné et retenté
EXCHANGE_TIMEOUT = 30.0
DISCONNECT_TIMEOUT = 5.0


def char_uuid(number: int) -> str:
    """UUID d'une caractéristique du service : REMOTE_UUID dans wand/remote.cpp."""
    return f"c0e1{number:04x}-1d6b-4e7b-9c3e-436f726e656c"


SERVICE_UUID = char_uuid(0x0001)
COMMAND_UUID = char_uuid(0x0020)
INFO_UUID = char_uuid(0x0021)


@dataclass(frozen=True)
class RemoteSetting:
    """Un réglage de la baguette (champ de Settings dans wand/settings.h).

    La baguette stocke `wand_min`..`wand_max` dans son unité ; Home Assistant affiche
    la valeur multipliée par `scale`, dans `unit`.
    """

    key: str
    index: int  # rang dans Settings : caractéristique 0x0010 + index
    default: float  # valeur par défaut de wand/config.h, unité de la baguette
    wand_min: float
    wand_max: float
    step: float  # pas affiché
    unit: str | None
    scale: float = 1.0
    switch: bool = False  # 0 ou 1 : un interrupteur plutôt qu'un curseur

    @property
    def uuid(self) -> str:
        return char_uuid(0x0010 + self.index)

    def to_display(self, value: float) -> float:
        return round(value * self.scale, 3)

    def to_wand(self, value: float) -> float:
        return min(self.wand_max, max(self.wand_min, value / self.scale))


# Même ordre et mêmes bornes que SETTINGS_LIMITS dans wand/settings.cpp
SETTINGS: tuple[RemoteSetting, ...] = (
    RemoteSetting("spell_threshold", 0, 0.60, 0.3, 0.99, 1, "%", 100),
    RemoteSetting("prepare_threshold", 1, 0.30, 0.05, 0.9, 1, "%", 100),
    RemoteSetting("spell_end_still", 2, 100, 0, 1000, 10, "ms"),
    RemoteSetting("still_gyro", 3, 15, 3, 60, 1, "°/s"),
    RemoteSetting("still_min", 4, 200, 50, 1000, 10, "ms"),
    RemoteSetting("aim_fallback", 5, 40, 5, 120, 1, "°/s"),
    RemoteSetting("aim_max_gesture", 6, 2000, 500, 2200, 100, "ms"),
    RemoteSetting("sleep_after", 7, 30, 5, 600, 5, "s"),
    RemoteSetting("wake_threshold", 8, 60, 20, 500, 5, "mg"),
    RemoteSetting("ignore_unaimed", 9, 1, 0, 1, 1, None, switch=True),
)
SETTINGS_BY_KEY = {setting.key: setting for setting in SETTINGS}

# CalResult dans wand/orientation.h
CALIBRATION_RESULTS = {
    0: "pas lancée depuis le réveil",
    1: "réussie",
    2: "ratée : la baguette n'a pas été levée puis tenue immobile vers le plafond",
    3: "ratée : erreur d'écriture en flash",
}


class RemoteError(Exception):
    """Connexion ou échange Bluetooth impossible.

    command_sent : la commande (et donc les réglages, écrits avant elle) est déjà partie quand
    l'échange a échoué. La baguette l'exécutera à la déconnexion : il ne faut pas la renvoyer.
    """

    def __init__(self, message: str, *, command_sent: bool = False) -> None:
        super().__init__(message)
        self.command_sent = command_sent


@dataclass(frozen=True)
class WandInfo:
    """État de la baguette (RemoteInfo dans wand/remote.cpp)."""

    firmware: int
    calibrated: bool  # axe de visée enregistré
    calibration_result: int
    voltage: float  # mesurée au démarrage et après une calibration : le capteur Tension suit les annonces

    @property
    def calibration_text(self) -> str:
        return CALIBRATION_RESULTS.get(self.calibration_result, f"code {self.calibration_result}")


INFO_FORMAT = "<HBBf"


def parse_info(data: bytes) -> WandInfo:
    if len(data) != struct.calcsize(INFO_FORMAT):
        raise RemoteError(f"état de la baguette illisible ({len(data)} octets)")
    firmware, calibrated, result, voltage = struct.unpack(INFO_FORMAT, data)
    return WandInfo(firmware, bool(calibrated), result, voltage)


async def async_exchange(
    device: BLEDevice, writes: dict[str, float], command: int | None, timeout: float = EXCHANGE_TIMEOUT
) -> tuple[dict[str, float], WandInfo]:
    """Une connexion : écrit les réglages (unité de la baguette), envoie la commande, relit tout.

    La commande part avant la relecture : c'est elle qui compte, et une coupure pendant la
    relecture ne doit pas la faire renvoyer plus tard. L'échange entier est borné par timeout :
    une connexion bloquée (proxy) ne retient pas la commande jusqu'au réveil suivant.
    Renvoie les réglages relus (unité de la baguette, bornés par elle) et son état.
    """
    progress = {"command_sent": False}
    try:
        async with asyncio.timeout(timeout):
            return await _exchange(device, writes, command, progress)
    except TimeoutError as err:
        raise RemoteError(
            f"échange trop long (plus de {timeout:.0f} s)", command_sent=progress["command_sent"]
        ) from err


async def _exchange(
    device: BLEDevice, writes: dict[str, float], command: int | None, progress: dict[str, bool]
) -> tuple[dict[str, float], WandInfo]:
    try:
        client = await establish_connection(
            BleakClientWithServiceCache, device, device.name or "Baguette Cornelius", max_attempts=2
        )
    except Exception as err:  # bleak et ses proxys lèvent des erreurs variées
        raise RemoteError(f"connexion impossible : {err}") from err
    try:
        for key, value in writes.items():
            await client.write_gatt_char(SETTINGS_BY_KEY[key].uuid, struct.pack("<f", value), response=True)
        if command is not None:
            await client.write_gatt_char(COMMAND_UUID, bytes([command]), response=True)
            progress["command_sent"] = True
        values = {}
        for setting in SETTINGS:
            raw = await client.read_gatt_char(setting.uuid)
            values[setting.key] = round(struct.unpack("<f", bytes(raw))[0], 4)
        info = parse_info(bytes(await client.read_gatt_char(INFO_UUID)))
    except RemoteError as err:
        raise RemoteError(str(err), command_sent=progress["command_sent"]) from err
    except Exception as err:
        if isinstance(err, BleakCharacteristicNotFoundError):
            # Services changés (nouveau firmware) : redécouverts à la prochaine connexion. Pas
            # après une coupure (sort lancé pendant l'échange) : le cache reste bon.
            with contextlib.suppress(Exception):
                await client.clear_cache()
        raise RemoteError(f"échange interrompu : {err}", command_sent=progress["command_sent"]) from err
    finally:
        # La baguette a pu couper la première ; une déconnexion bloquée ne retient pas l'échange
        with contextlib.suppress(Exception):
            async with asyncio.timeout(DISCONNECT_TIMEOUT):
                await client.disconnect()
    return values, info
