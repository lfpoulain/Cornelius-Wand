"""État de la baguette : annonces, vision, cibles, réglages et sorts.

Un sort se déroule ainsi :
1. on vise une cible, on marque une courte pause, puis on fait le geste ;
2. dès que la pause finit, la baguette envoie une annonce « visée » avec l'inclinaison de
   la visée et depuis combien de temps la pause est finie (puis une « préparation » quand
   le score du sort monte, qui fait de même si la visée n'a pas été reçue) ;
3. Home Assistant récupère la photo de la caméra au plafond prise à ce moment-là, et Jev
   y trouve les épaules et la main qui tient la baguette (vision.py), pendant tout le
   geste ;
4. à l'annonce du sort, la direction du bras sur la photo et l'inclinaison désignent la
   cible (aim.py) : l'événement Sort part aussitôt. La baguette recalcule sa visée au sort :
   si elle ne vise plus le même instant, ou si la photo de la visée n'a pas pu être prise
   (caméra injoignable) alors qu'il reste le temps et que la caméra l'a encore, une
   nouvelle photo est analysée ; sans visée, le sort part sans cible.

Les réglages de la baguette, modifiés dans Home Assistant, partent en une connexion
Bluetooth (remote.py) dès que sa balise de présence montre qu'elle est réveillée, après
quelques secondes sans geste.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothChange, BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util, slugify

from .aim import LEVELS, AimResult, Target, aim, surface_from_pitch
from .const import (
    BTHOME_SERVICE_UUID,
    CONF_CAMERA_URL,
    CONF_SPELLS,
    CONF_TYPESAFE_API_KEY,
    DEFAULT_SPELLS,
    DEFAULT_CEILING_PITCH,
    DEFAULT_FLOOR_PITCH,
    DEFAULT_TOLERANCE,
    DOMAIN,
    EVENT_SPELL,
    MAX_CEILING_PITCH,
    MAX_FLOOR_PITCH,
    MAX_TOLERANCE,
    MIN_CEILING_PITCH,
    MIN_FLOOR_PITCH,
    MIN_TOLERANCE,
    STORAGE_VERSION,
    UNKNOWN_SPELL,
)
from .overlay import draw
from .parser import BTHomeParseError, WandPacket, parse_bthome
from .remote import (
    CALIBRATION_DURATION,
    COMMAND_CALIBRATE,
    SETTINGS,
    SETTINGS_BY_KEY,
    RemoteError,
    WandInfo,
    async_exchange,
)
from .vision import VisionError, VisionResult, analyze, fetch_photo

_LOGGER = logging.getLogger(__name__)

# Photo un peu avant la fin de la pause de visée : le bras pointait encore la cible
PHOTO_MARGIN_MS = 150
# La caméra rend la photo la plus proche de l'instant demandé : au-delà, elle ne l'a plus
PHOTO_MAX_GAP_MS = 300  # quelques photos d'écart (la caméra range chaque image, 10 à 15 par seconde)
# Durée de photos toujours gardée par la caméra (PHOTO_KEEP_MS de camera/config.h)
PHOTO_KEEP_MS = 2600
# Une analyse lancée plus tôt ne sert plus au sort (geste abandonné puis repris)
ANALYSIS_MAX_AGE = 4.0
# L'analyse de la visée ou de la préparation sert au sort s'ils visent le même instant :
# la même pause vue plusieurs fois
SAME_AIM_TOLERANCE = 0.2  # s (gigue de réception, 10 à 15 photos par seconde)
# Le sort part au plus tard ce délai après son annonce, sans cible si la vision traîne
VISION_DEADLINE = 1.5  # s ; Jev répond d'habitude en 0,2 s
# Photo de la visée manquée (caméra injoignable) : le sort en relance une s'il lui reste ce
# temps. Une erreur de Jev ne se relance pas : elle coûterait un second appel pour la même réponse
RETRY_MIN_TIME = 0.4  # s : photo (≈ 150 ms) puis Jev (≈ 200 ms)
# Annonces déjà traitées : une rafale peut être relayée en retard par un autre proxy
SEEN_PACKETS_WINDOW = 5.0  # s
# Annonce plus ancienne : rejouée par Home Assistant depuis son historique (démarrage,
# rechargement), pas reçue à l'instant ; la rafale d'un sort ne dure qu'une seconde
STALE_ADVERT = 3.0  # s

# Réglages Bluetooth : délai avant un nouvel essai après un échec
REMOTE_RETRY_DELAY = 15.0
# Une commande (calibration) attend : on réessaie plus vite, la personne attend la LED bleue
COMMAND_RETRY_DELAY = 5.0
# Commande pas partie dans ce délai : abandonnée. Elle ne doit pas se déclencher au réveil
# suivant, des minutes plus tard, quand plus personne ne l'attend.
COMMAND_TTL = 60.0  # s
# Réveillée, la baguette répète sa balise toutes les 500 ms
AWAKE_WINDOW = 2.0  # s
# Plus long silence : la baguette dormait, l'annonce suivante est un réveil
SLEEP_GAP = 5.0  # s
PRESENCE_POLL = timedelta(seconds=2)  # relevé de l'heure de la dernière annonce
# Réglages en attente et baguette endormie : on regarde régulièrement si elle s'est réveillée
AWAKE_POLL = 5.0  # s
# Pas de connexion en plein geste : une visée, une préparation ou un sort la coupe (l'échange
# échoue et attend REMOTE_RETRY_DELAY). On attend ce calme après le réveil et après chaque
# visée, préparation ou sort (enchaînés toutes les 2 à 3 s).
QUIET_GUARD = 3.0  # s

VISION_DISABLED = "vision non configurée (clé TypeSafe, adresse de la caméra)"
SPELL_ONLY = "mode sort uniquement"
NO_AIM = "visée inconnue (aucune pause de visée retenue avant le geste)"

COMMAND_STATUS = {
    COMMAND_CALIBRATE: (
        "Calibration lancée : LED bleue, lève la baguette vers le PLAFOND puis ne bouge plus. "
        "Vert : réussie, rouge : à refaire"
    ),
}


def parse_spell_names(value: str) -> list[str]:
    """Noms des sorts dans l'ordre des boutons : « Alohomora, Lumos »."""
    return [name.strip() for name in value.split(",") if name.strip()]


def _aimed_at(packet: WandPacket, received: float) -> float | None:
    """Instant visé, sur l'horloge de Home Assistant (None sans visée)."""
    if not packet.has_aim or packet.aim_age_ms is None:
        return None
    return received - packet.aim_age_ms / 1000


@dataclass
class Look:
    """Une photo analysée : ce que Jev a vu, ou pourquoi il n'a rien vu."""

    photo: bytes | None
    vision: VisionResult | None
    error: str | None


@dataclass
class Analysis:
    """Analyse lancée avant le sort, à la fin de la pause de visée ou à la préparation."""

    started: float  # time.monotonic() de l'annonce qui l'a lancée
    aimed: float  # instant visé, même horloge
    source: str  # « visée » ou « préparation »
    task: asyncio.Task[Look]


@dataclass
class SpellResult:
    """Un sort lancé, avec la cible reconnue."""

    button: int
    spell: str  # identifiant, ex. « alohomora »
    spell_name: str  # nom affiché, ex. « Alohomora »
    target: str | None
    target_name: str | None
    pitch: float | None
    surface: str | None  # d'après l'inclinaison et les seuils du moment
    aim: AimResult
    vision: VisionResult | None
    vision_error: str | None
    wait_ms: int | None  # attente de la vision entre l'annonce du sort et l'événement
    analysis_from: str | None  # annonce qui a lancé l'analyse : visée, préparation ou sort
    head_start_ms: int | None  # avance de l'analyse sur l'annonce du sort

    @property
    def attributes(self) -> dict[str, Any]:
        """Attributs exposés sur l'entité Sort et dans l'événement du bus."""
        person = self.vision.origin if self.vision else None  # la tête, sinon les épaules
        return {
            "sort": self.spell,
            "nom": self.spell_name,
            "bouton": self.button,
            "cible": self.target,
            "nom_cible": self.target_name,
            "inclinaison": self.pitch,
            "surface": self.surface,
            "direction": self.aim.angle,
            "ecart": self.aim.gap,
            "refus": self.aim.refusal,
            "position_x": person[0] if person else None,
            "position_y": person[1] if person else None,
            "vision": "ok" if self.vision else self.vision_error,
            "attente_ms": self.wait_ms,
            "analyse": self.analysis_from,
            "avance_ms": self.head_start_ms,
        }


class CorneliusWand:
    """Une baguette, liée à une entrée de configuration."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.address: str = entry.data[CONF_ADDRESS]
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")

        # Enregistré : cibles placées sur la photo, tolérance, réglages de la baguette
        self.targets: dict[str, Target] = {}
        self.tolerance = DEFAULT_TOLERANCE
        self.ceiling_pitch = DEFAULT_CEILING_PITCH
        self.floor_pitch = DEFAULT_FLOOR_PITCH
        self.spell_only = False  # le geste suffit : ni photo ni Jev, le sort part sans cible
        self.remote_values: dict[str, float] = {}  # relus sur la baguette, dans son unité
        self.remote_pending: dict[str, float] = {}  # modifiés, en attente de son réveil

        # État courant
        self.voltage: float | None = None
        self.pitch: float | None = None
        self.status: str | None = None
        self.aimed_target: str | None = None
        self.last_aim: AimResult | None = None
        self.last_vision: VisionResult | None = None
        self.last_vision_image: bytes | None = None  # photo annotée : épaules, main, bras, cibles
        self.last_vision_time: datetime | None = None
        self.remote_command: int | None = None
        self._command_until = 0.0  # time.monotonic() au-delà duquel la commande est abandonnée
        self.remote_info: WandInfo | None = None
        self.has_data = False  # une annonce a été reçue depuis le démarrage
        self._seen_packets: dict[int, float] = {}  # numéro de paquet -> dernière réception
        self._last_packet_id: int | None = None  # dernière visée, préparation ou dernier sort traité
        self._cast_seq = 0  # numéro du dernier sort : seul le plus récent met à jour l'état
        self._analysis: Analysis | None = None  # photo de la visée analysée pendant le geste
        self.jev_calls = 0  # appels à Jev depuis l'installation (enregistré)
        self.jev_calls_since_wake = 0
        self.woke_at: datetime | None = None  # dernier réveil vu par Home Assistant
        self._heard_at: float | None = None  # heure (bluetooth.MONOTONIC_TIME) de la dernière annonce
        self._gesture_until = 0.0  # un geste est en cours : pas de connexion avant
        self._remote_task: asyncio.Task[None] | None = None
        self._remote_timer: CALLBACK_TYPE | None = None
        self._remote_next_try = 0.0
        self._remote_reread = True  # relire la baguette au premier réveil, et après une commande
        self._unloaded = False

        self._listeners: list[CALLBACK_TYPE] = []
        self._spell_listeners: list[Callable[[SpellResult], None]] = []

    # ------------------------------------------------------------------
    # Options et stockage
    # ------------------------------------------------------------------
    @property
    def spell_names(self) -> list[str]:
        return parse_spell_names(self.entry.options.get(CONF_SPELLS, DEFAULT_SPELLS))

    @property
    def spell_types(self) -> list[str]:
        """Types d'événement de l'entité Sort."""
        return [slugify(name) for name in self.spell_names] + [UNKNOWN_SPELL]

    @property
    def vision_enabled(self) -> bool:
        """Caméra et clé TypeSafe configurées (le panneau peut vérifier la visée)."""
        options = self.entry.options
        return bool(options.get(CONF_TYPESAFE_API_KEY) and options.get(CONF_CAMERA_URL))

    @property
    def aiming(self) -> bool:
        """Les sorts cherchent leur cible sur la photo : vision configurée, hors mode sort uniquement."""
        return self.vision_enabled and not self.spell_only

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        for key, value in data.get("targets", {}).items():
            if isinstance(value, dict) and "name" in value:
                self.targets[key] = Target(value["name"], value.get("x"), value.get("y"), value.get("level"))
        self.tolerance = data.get("tolerance", DEFAULT_TOLERANCE)
        self.ceiling_pitch = data.get("ceiling_pitch", DEFAULT_CEILING_PITCH)
        self.floor_pitch = data.get("floor_pitch", DEFAULT_FLOOR_PITCH)
        self.spell_only = data.get("spell_only", False)
        self.remote_values = data.get("remote_values", {})
        self.remote_pending = data.get("remote_pending", {})
        self.jev_calls = data.get("jev_calls", 0)

    @callback
    def _save(self) -> None:
        if self._unloaded:  # l'instance rechargée a repris le fichier
            return
        self._store.async_delay_save(self._data_to_save, 1)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            "targets": {
                key: {"name": t.name, "x": t.x, "y": t.y, "level": t.level} for key, t in self.targets.items()
            },
            "tolerance": self.tolerance,
            "ceiling_pitch": self.ceiling_pitch,
            "floor_pitch": self.floor_pitch,
            "spell_only": self.spell_only,
            "remote_values": self.remote_values,
            "remote_pending": self.remote_pending,
            "jev_calls": self.jev_calls,
        }

    async def async_unload(self) -> None:
        """Déchargement : arrête la minuterie des réglages et écrit tout de suite ce qui attend.

        Home Assistant annule ensuite les échanges et les analyses en cours, et attend les sorts.
        """
        self._unloaded = True
        if self._remote_timer:
            self._remote_timer()
            self._remote_timer = None
        await self._store.async_save(self._data_to_save())  # remplace l'écriture différée

    # ------------------------------------------------------------------
    # Abonnements des entités
    # ------------------------------------------------------------------
    @callback
    def async_add_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Prévient l'entité à chaque changement d'état."""
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def async_add_spell_listener(self, handler: Callable[[SpellResult], None]) -> CALLBACK_TYPE:
        """Prévient l'entité Sort à chaque sort lancé."""
        self._spell_listeners.append(handler)
        return lambda: self._spell_listeners.remove(handler)

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    # ------------------------------------------------------------------
    # Cibles, placées depuis le panneau Baguette
    # ------------------------------------------------------------------
    @callback
    def add_target(self, name: str, level: str | None = None) -> str:
        """Nouvelle cible, pas encore placée ; renvoie sa clé (le nom simplifié)."""
        name = name.strip()
        key = slugify(name)
        # slugify ne rend jamais "" : « unknown » pour un nom sans lettre ni chiffre
        if not any(c.isalnum() for c in name):
            raise ValueError("nom de cible vide ou sans lettre ni chiffre")
        if key in self.targets:
            raise ValueError(f"la cible {key} existe déjà")
        self.targets[key] = Target(name, level=self._checked_level(level))
        self._targets_changed()
        return key

    @callback
    def update_target(
        self, key: str, x: float | None = None, y: float | None = None, level: str | None = "keep"
    ) -> None:
        """Place la cible (x, y de 0 à 1, hors de la photo pour un objet hors champ) et/ou change sa surface."""
        target = self.targets[key]
        if x is not None and y is not None:
            target.x, target.y = round(float(x), 4), round(float(y), 4)
        if level != "keep":
            target.level = self._checked_level(level)
        self._targets_changed()

    @callback
    def delete_target(self, key: str) -> None:
        self.targets.pop(key)
        if self.aimed_target == key:
            self.aimed_target = None
        self._targets_changed()

    @staticmethod
    def _checked_level(level: str | None) -> str | None:
        if level is not None and level not in LEVELS:
            raise ValueError(f"surface inconnue : {level}")
        return level

    @callback
    def _targets_changed(self) -> None:
        self._save()
        self._notify()

    @callback
    def set_tolerance(self, tolerance: float) -> None:
        self.tolerance = max(MIN_TOLERANCE, min(MAX_TOLERANCE, tolerance))
        self._save()
        self._notify()

    @callback
    def set_ceiling_pitch(self, pitch: float) -> None:
        self.ceiling_pitch = max(MIN_CEILING_PITCH, min(MAX_CEILING_PITCH, pitch))
        self._save()
        self._notify()

    @callback
    def set_floor_pitch(self, pitch: float) -> None:
        self.floor_pitch = max(MIN_FLOOR_PITCH, min(MAX_FLOOR_PITCH, pitch))
        self._save()
        self._notify()

    @callback
    def set_spell_only(self, enabled: bool) -> None:
        """Mode sort uniquement : le geste déclenche le sort, sans photo ni appel à Jev."""
        self.spell_only = enabled
        if enabled and self._analysis:  # analyse en cours : elle ne servira plus
            self._analysis.task.cancel()
            self._analysis = None
        self._save()
        self._notify()

    def surface(self, pitch: float | None) -> str | None:
        """Sol, mur ou plafond, d'après l'inclinaison et les seuils réglés."""
        return surface_from_pitch(pitch, self.ceiling_pitch, self.floor_pitch)

    # ------------------------------------------------------------------
    # Vision
    # ------------------------------------------------------------------
    async def async_photo(self, ago_ms: int = 0) -> tuple[bytes, int | None]:
        """Photo de la caméra au plafond (lève VisionError)."""
        camera = self.entry.options.get(CONF_CAMERA_URL)
        if not camera:
            raise VisionError("adresse de la caméra non configurée")
        return await fetch_photo(async_get_clientsession(self.hass), camera, ago_ms)

    async def async_look(self, ago_ms: int) -> Look:
        """Photo d'il y a ago_ms, analysée par Jev ; l'erreur est rendue, pas levée."""
        if not self.vision_enabled:
            return Look(None, None, VISION_DISABLED)
        photo = None
        try:
            photo, age = await self.async_photo(ago_ms)
            if age is not None and abs(age - ago_ms) > PHOTO_MAX_GAP_MS:
                raise VisionError(
                    f"photo de la visée plus disponible (demandée il y a {ago_ms} ms, reçue de {age} ms)"
                )
            key = self.entry.options[CONF_TYPESAFE_API_KEY]
            self._count_jev_call()
            vision = await analyze(async_get_clientsession(self.hass), key, photo, age)
        except VisionError as err:
            _LOGGER.warning("Vision indisponible : %s", err)
            return Look(photo, None, str(err))
        except Exception as err:  # le sort doit partir quoi qu'il arrive
            _LOGGER.exception("Erreur inattendue de la vision")
            return Look(photo, None, f"vision : {type(err).__name__}")
        return Look(photo, vision, None)

    def aim_at(self, vision: VisionResult, pitch: float | None) -> AimResult:
        return aim(
            self.targets,
            vision.origin,
            vision.hand_point,
            pitch,
            self.tolerance,
            vision.ratio,
            self.ceiling_pitch,
            self.floor_pitch,
        )

    async def async_record_look(self, look: Look, result: AimResult) -> None:
        """Analyse faite depuis le panneau : le capteur Vision et la « Photo analysée » la montrent."""
        self.last_vision = look.vision
        self._notify()
        await self._async_store_image(look, result)

    async def _async_store_image(self, look: Look, result: AimResult) -> None:
        """Photo annotée pour l'entité « Photo analysée », sans retarder le sort."""
        if look.photo is None or look.vision is None:
            return
        try:
            self.last_vision_image = await self.hass.async_add_executor_job(
                draw, look.photo, look.vision, result, dict(self.targets)
            )
        except (OSError, ValueError):  # Pillow : photo ou cadre illisible, on garde la photo brute
            self.last_vision_image = look.photo
        self.last_vision_time = dt_util.utcnow()
        self._notify()

    # ------------------------------------------------------------------
    # Réglages de la baguette, en Bluetooth
    # ------------------------------------------------------------------
    def remote_display_value(self, key: str) -> float:
        """Valeur affichée : en attente, sinon relue sur la baguette, sinon par défaut."""
        setting = SETTINGS_BY_KEY[key]
        return setting.to_display(self.remote_pending.get(key, self.remote_values.get(key, setting.default)))

    @callback
    def set_remote_setting(self, key: str, display_value: float) -> None:
        self.remote_pending[key] = SETTINGS_BY_KEY[key].to_wand(display_value)
        self._remote_next_try = 0.0
        self.status = "Réglage en attente : il part dès que la baguette est réveillée (bouge-la si elle dort)"
        self._save()
        self._notify()
        self._exchange_when_awake()

    @callback
    def reset_remote_settings(self) -> None:
        """Réglages par défaut : mis en attente comme des réglages modifiés, si bien que ceux
        modifiés ensuite, avant le réveil, passent par-dessus."""
        self.remote_pending = {setting.key: setting.default for setting in SETTINGS}
        self._remote_next_try = 0.0
        self.status = (
            "Réglages par défaut en attente : ils partent dès que la baguette est réveillée (bouge-la si elle dort)"
        )
        self._save()
        self._notify()
        self._exchange_when_awake()

    @callback
    def request_command(self, command: int) -> None:
        self.remote_command = command
        self._command_until = time.monotonic() + COMMAND_TTL
        self._remote_next_try = 0.0
        self.status = (
            "Commande en attente : elle part dès que la baguette est réveillée (bouge-la si elle dort), "
            f"abandonnée au bout de {COMMAND_TTL:.0f} s"
        )
        self._notify()
        self._exchange_when_awake()

    @callback
    def _drop_stale_command(self) -> None:
        """Commande restée trop longtemps sans partir : abandonnée plutôt qu'exécutée au hasard d'un réveil."""
        if self.remote_command is None or time.monotonic() < self._command_until:
            return
        _LOGGER.warning("Commande %s abandonnée : baguette injoignable pendant %.0f s", self.remote_command, COMMAND_TTL)
        self.remote_command = None
        self.status = (
            f"Commande abandonnée : baguette injoignable pendant {COMMAND_TTL:.0f} s. "
            "Réveille-la (bouge-la), puis relance"
        )
        self._notify()

    @property
    def _remote_work(self) -> bool:
        """Des réglages ou une commande attendent, ou il faut relire la baguette."""
        return bool(self.remote_pending or self.remote_command is not None or self._remote_reread)

    @callback
    def check_presence(self, _now: datetime | None = None, heard_at: float | None = None) -> None:
        """Repère les réveils : une annonce après plus de SLEEP_GAP de silence.

        Home Assistant ne rappelle pas l'intégration pour une balise identique à la précédente,
        mais il note son heure de réception : on la relève à chaque annonce et toutes les
        PRESENCE_POLL, si bien qu'une baguette réveillée n'a jamais de silence aussi long.
        Une annonce ancienne (historique rejoué au démarrage, baguette endormie) ne compte pas.
        """
        if heard_at is None:
            info = bluetooth.async_last_service_info(self.hass, self.address, connectable=False)
            if info is None:
                return
            heard_at = info.time
        if bluetooth.MONOTONIC_TIME() - heard_at > STALE_ADVERT:
            return
        previous, self._heard_at = self._heard_at, max(heard_at, self._heard_at or heard_at)
        if previous is None or heard_at - previous > SLEEP_GAP:
            self.jev_calls_since_wake = 0
            self.woke_at = dt_util.utcnow()
            # On vient de la prendre, sans doute pour un sort : pas de connexion tout de suite
            self._gesture_until = max(self._gesture_until, time.monotonic() + QUIET_GUARD)
            self._notify()

    @callback
    def _count_jev_call(self) -> None:
        self.jev_calls += 1
        self.jev_calls_since_wake += 1
        self._save()
        self._notify()

    def _wand_awake(self) -> bool:
        """La baguette émet en ce moment, vue par un adaptateur capable de s'y connecter.

        Home Assistant ne rappelle pas l'intégration pour une balise identique à la précédente,
        mais il note quand même l'heure de réception.
        """
        info = bluetooth.async_last_service_info(self.hass, self.address, connectable=True)
        return info is not None and bluetooth.MONOTONIC_TIME() - info.time < AWAKE_WINDOW

    @callback
    def _schedule_exchange(self, delay: float) -> None:
        if self._unloaded:
            return
        if self._remote_timer:
            self._remote_timer()
        self._remote_timer = async_call_later(self.hass, max(delay, 0.5), self._on_remote_timer)

    @callback
    def _on_remote_timer(self, _now: datetime) -> None:
        self._remote_timer = None
        self._exchange_when_awake()

    @callback
    def _exchange_when_awake(self) -> None:
        """Échange si la baguette est réveillée ; sinon on revient voir tant que du travail attend."""
        self._drop_stale_command()
        self.check_presence()  # réveil pas encore relevé : le calme qui le suit compte aussi
        if self._wand_awake():
            self._maybe_exchange()
        elif self._remote_work:
            self._schedule_exchange(AWAKE_POLL)

    @callback
    def _maybe_exchange(self) -> None:
        """Baguette réveillée : connexion si des réglages attendent, ou pour les relire."""
        if self._remote_task is not None and not self._remote_task.done():
            return
        self._drop_stale_command()
        if not self._remote_work:
            return
        # Réveil ou geste récent, ou trop tôt après un échec ou une calibration : on revient plus tard
        wait = max(self._gesture_until, self._remote_next_try) - time.monotonic()
        if wait > 0:
            self._schedule_exchange(wait)
            return
        # Seul un adaptateur ou un proxy capable de se connecter convient
        device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if device is None:
            self._schedule_exchange(1)
            return
        self._remote_next_try = time.monotonic() + REMOTE_RETRY_DELAY
        self._remote_task = self.entry.async_create_background_task(
            self.hass, self._async_exchange(device), f"{DOMAIN} réglages"
        )

    async def _async_exchange(self, device: Any) -> None:
        writes = dict(self.remote_pending)
        command = self.remote_command
        try:
            values, info = await async_exchange(device, writes, command)
        except RemoteError as err:
            if command is not None and err.command_sent:
                # La baguette a reçu la commande (et les réglages, écrits avant) : elle l'exécute
                # à la déconnexion. La renvoyer déclencherait une seconde calibration plus tard.
                _LOGGER.info("Commande envoyée, relecture de la baguette interrompue : %s", err)
                self._delivered(writes, command)
                return
            delay = REMOTE_RETRY_DELAY if command is None else COMMAND_RETRY_DELAY
            if writes or command is not None:
                _LOGGER.warning("Réglages de la baguette : %s", err)
                self.status = f"Réglages : {err} ; nouvel essai dans {delay:.0f} s"
                self._notify()
            else:
                _LOGGER.debug("Relecture de la baguette : %s", err)
            self._remote_next_try = time.monotonic() + delay
            self._schedule_exchange(delay)
            return
        self.remote_values = values
        self.remote_info = info
        self._delivered(writes, command)

    @callback
    def _delivered(self, writes: dict[str, float], command: int | None) -> None:
        """Réglages et commande reçus par la baguette : plus en attente."""
        for key, value in writes.items():
            if self.remote_pending.get(key) == value:  # pas modifié entre-temps
                del self.remote_pending[key]
        self._remote_reread = command is not None  # relire l'effet de la commande
        if command is not None and self.remote_command == command:
            self.remote_command = None
        if command == COMMAND_CALIBRATE:  # la baguette calibre : on relit une fois qu'elle a fini
            self._remote_next_try = time.monotonic() + CALIBRATION_DURATION
            self._schedule_exchange(CALIBRATION_DURATION)
        else:
            self._remote_next_try = 0.0
            if self._remote_work:  # modifié pendant l'échange, ou effet de la commande à relire
                self._schedule_exchange(0)
        if command is not None:
            self.status = COMMAND_STATUS.get(command, "Commande envoyée à la baguette")
        elif writes:
            self.status = f"{len(writes)} réglage(s) envoyé(s) à la baguette"
        self._save()
        self._notify()

    # ------------------------------------------------------------------
    # Annonces de la baguette
    # ------------------------------------------------------------------
    @callback
    def handle_advertisement(self, service_info: BluetoothServiceInfoBleak, change: BluetoothChange) -> None:
        data = service_info.service_data.get(BTHOME_SERVICE_UUID)
        if data is None:
            return
        try:
            packet = parse_bthome(data)
        except BTHomeParseError as err:
            _LOGGER.debug("Annonce ignorée de %s : %s", self.address, err)
            return
        self.check_presence(heard_at=service_info.time)
        if bluetooth.MONOTONIC_TIME() - service_info.time > STALE_ADVERT:
            # Rejouée à l'enregistrement du rappel (démarrage, rechargement) : la baguette dort
            # peut-être, ce n'est ni un geste ni une présence. On guette son réveil s'il le faut.
            self._exchange_when_awake()
            return
        now = time.monotonic()
        packet_id = packet.packet_id
        self._seen_packets = {
            seen: at for seen, at in self._seen_packets.items() if now - at < SEEN_PACKETS_WINDOW
        }

        # Annonce de présence (ni sort, ni préparation, ni visée) : la baguette est réveillée et
        # joignable. Elle reprend le numéro du dernier paquet, avec une tension à jour. Une
        # visée déjà reçue, répétée pendant sa rafale, compte aussi comme présence.
        aiming = packet.is_aim and packet_id is not None and not self._already_seen(packet, packet_id)
        if packet.pressed_button is None and packet.preparing_button is None and not aiming:
            if packet_id in self._seen_packets:
                self._seen_packets[packet_id] = now
            self.has_data = True
            if packet.voltage is not None:
                self.voltage = packet.voltage
            self._notify()
            self._maybe_exchange()
            return

        if packet_id is not None:
            if self._already_seen(packet, packet_id):
                return
            self._seen_packets[packet_id] = now
            self._last_packet_id = packet_id
        self.has_data = True
        if packet.voltage is not None:
            self.voltage = packet.voltage
        if packet.has_aim:
            self.pitch = packet.pitch

        self._gesture_until = now + QUIET_GUARD  # un geste en cours, ou un autre sort enchaîné
        if aiming:  # fin de la pause de visée : le geste commence peut-être
            self._start_analysis(packet, now, "visée")
        elif packet.preparing_button is not None:
            self._start_analysis(packet, now, "préparation")
        elif (button := packet.pressed_button) is not None:
            self._cast_seq += 1
            self.entry.async_create_task(
                self.hass, self._async_cast(button, packet, now, self._cast_seq), f"{DOMAIN} sort"
            )
        self._notify()

    def _already_seen(self, packet: WandPacket, packet_id: int) -> bool:
        """Annonce répétée pendant sa rafale, parfois par plusieurs proxys, ou visée ou
        préparation relayée en retard par un autre proxy après un paquet plus récent."""
        if packet_id in self._seen_packets:
            return True
        last = self._last_packet_id
        return (
            packet.pressed_button is None
            and last in self._seen_packets
            and 0 < (last - packet_id) % 256 < 128
        )

    @staticmethod
    def _photo_delay(packet: WandPacket, since: float) -> int:
        """Âge de la photo à demander : le moment où l'on visait, juste avant la fin de la pause."""
        elapsed = round((time.monotonic() - since) * 1000)
        return (packet.aim_age_ms or 0) + PHOTO_MARGIN_MS + elapsed

    @staticmethod
    def _reusable(analysis: Analysis | None, received: float, aimed: float) -> bool:
        """L'analyse lancée plus tôt vise le même instant, et n'est pas trop ancienne."""
        return (
            analysis is not None
            and received - analysis.started < ANALYSIS_MAX_AGE
            and abs(analysis.aimed - aimed) <= SAME_AIM_TOLERANCE
        )

    @callback
    def _start_analysis(self, packet: WandPacket, received: float, source: str) -> None:
        """Fin de la pause de visée, ou préparation : la photo de la visée est analysée
        pendant le geste. La préparation garde l'analyse du même instant déjà lancée ; une
        visée annonce toujours une nouvelle pause, même à moins de 0,2 s de la précédente."""
        aimed = _aimed_at(packet, received)
        if not self.aiming or aimed is None:  # sans visée, la photo ne désignerait rien
            return
        if source != "visée" and self._reusable(self._analysis, received, aimed):
            return
        if self._analysis:  # autre pause : l'ancienne analyse ne servira plus
            self._analysis.task.cancel()
        task = self.entry.async_create_background_task(
            self.hass, self.async_look(self._photo_delay(packet, received)), f"{DOMAIN} {source}"
        )
        self._analysis = Analysis(received, aimed, source, task)

    async def _async_spell_look(
        self, packet: WandPacket, received: float, aimed: float, analysis: Analysis | None
    ) -> tuple[Look, str, int]:
        """L'analyse lancée à la visée ou à la préparation si elle vise le même instant que le
        sort ; sinon, ou si sa photo manque et que la caméra l'a encore, une nouvelle photo ;
        avec l'annonce qui l'a lancée et son avance en ms."""

        def remaining() -> float:
            return VISION_DEADLINE - (time.monotonic() - received)

        task: asyncio.Task[Look] | None = None
        source, head_start = "sort", 0
        if analysis is not None and self._reusable(analysis, received, aimed):
            task, source = analysis.task, analysis.source
            head_start = round((received - analysis.started) * 1000)
        elif analysis:  # la pause retenue par la baguette a changé depuis
            analysis.task.cancel()
        try:
            async with asyncio.timeout(max(remaining(), 0)):  # au-delà, annulées : plus personne n'attend
                if task is not None:
                    look = await task
                    retry_ago = self._photo_delay(packet, received)
                    if (
                        look.vision is not None
                        or look.photo is not None  # photo reçue : erreur de Jev ou photo trop loin, pas de second appel
                        or remaining() < RETRY_MIN_TIME
                        or retry_ago - PHOTO_MAX_GAP_MS > PHOTO_KEEP_MS  # la caméra ne l'a plus
                    ):
                        return look, source, head_start
                    # Photo manquée (caméra injoignable) : un nouvel essai, même visée
                    _LOGGER.debug("Analyse de la %s ratée (%s) : nouvel essai pour le sort", source, look.error)
                    source, head_start = "sort", 0
                task = self.entry.async_create_background_task(
                    self.hass, self.async_look(self._photo_delay(packet, received)), f"{DOMAIN} vision du sort"
                )
                return await task, source, head_start
        except TimeoutError:
            _LOGGER.warning("Vision trop lente : le sort part sans cible")
            return Look(None, None, "vision trop lente"), source, head_start
        except asyncio.CancelledError:
            if (current := asyncio.current_task()) and current.cancelling():
                raise  # c'est le sort lui-même qu'on annule
            # Tâche de vision annulée par le déchargement de l'entrée : le sort part quand même
            return Look(None, None, "vision annulée (baguette rechargée)"), source, head_start

    async def _async_cast(self, button: int, packet: WandPacket, received: float, seq: int) -> None:
        names = self.spell_names
        spell_name = names[button - 1] if button <= len(names) else f"Bouton {button}"
        spell = slugify(spell_name) if button <= len(names) else UNKNOWN_SPELL
        pitch = packet.pitch if packet.has_aim else None
        aimed = _aimed_at(packet, received)

        analysis, self._analysis = self._analysis, None
        source: str | None = None
        head_start: int | None = None
        if self.aiming and aimed is not None:
            look, source, head_start = await self._async_spell_look(packet, received, aimed, analysis)
            result = self.aim_at(look.vision, pitch) if look.vision else AimResult(None, refusal=look.error)
        else:
            if analysis:
                analysis.task.cancel()
            # Sans visée, une photo d'un instant quelconque ne désignerait rien : Jev n'est pas appelé
            if self.spell_only:
                why = SPELL_ONLY
            elif not self.vision_enabled:
                why = VISION_DISABLED
            else:
                why = None
            look = Look(None, None, why)
            result = AimResult(None, refusal=why or NO_AIM)
        wait_ms = round((time.monotonic() - received) * 1000)
        target = self.targets.get(result.target) if result.target else None

        # Un sort plus récent a pu partir pendant l'attente de la vision : l'état reste le sien
        latest = seq == self._cast_seq
        if latest:
            self.aimed_target = result.target
            self.last_aim = result
            if look.vision:
                self.last_vision = look.vision
            self.status = f"{spell_name} → {target.name if target else 'aucune cible'}"
            if result.gap is not None:
                self.status += f" (écart {result.gap:.0f}°)"
            if result.refusal:
                self.status += f" : {result.refusal}"
            if look.vision:
                self.status += f" | {look.vision.summary}, attente {wait_ms} ms"
                if source != "sort":
                    self.status += f" (analyse dès la {source}, {head_start} ms avant)"

        spell_result = SpellResult(
            button, spell, spell_name, result.target, target.name if target else None,
            pitch, self.surface(pitch), result, look.vision, look.error, wait_ms if self.aiming else None,
            source, head_start,
        )
        # Capteurs (Cible visée…) à jour AVANT l'événement : Home Assistant lance les
        # automatisations déclenchées par l'entité Sort dans la foulée, avant de rendre la main.
        # Une condition sur le capteur lirait sinon la cible du sort précédent.
        self._notify()
        for handler in list(self._spell_listeners):
            handler(spell_result)
        self.hass.bus.async_fire(EVENT_SPELL, {"address": self.address, **spell_result.attributes})
        if latest:
            await self._async_store_image(look, result)
