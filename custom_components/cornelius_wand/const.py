"""Constantes de l'intégration Baguette Cornelius."""

from __future__ import annotations

DOMAIN = "cornelius_wand"

# Nom annoncé par la baguette (DEVICE_NAME dans wand/config.h)
LOCAL_NAME_PREFIX = "Cornelius"
BTHOME_SERVICE_UUID = "0000fcd2-0000-1000-8000-00805f9b34fb"

CONF_SPELLS = "spells"
CONF_CAMERA_URL = "camera_url"
CONF_TYPESAFE_API_KEY = "typesafe_api_key"

DEFAULT_SPELLS = "Alohomora"
MAX_SPELLS = 4  # BTHOME_BUTTONS dans le firmware

# Vision : photo de la caméra au plafond analysée par Jev (TypeSafe)
JEV_URL = "https://api.typesafe.ai/v1"
VISION_MODEL = "jev-1.14.0"  # première version avec les questions « box »

# Visée : écart maximal entre la direction du bras et celle de la cible, vue d'en haut
DEFAULT_TOLERANCE = 25.0
MIN_TOLERANCE = 5.0
MAX_TOLERANCE = 60.0

# Surface visée d'après l'inclinaison de la baguette : plafond au-dessus du premier seuil,
# sol en dessous du second, mur entre les deux
DEFAULT_CEILING_PITCH = 45.0
MIN_CEILING_PITCH, MAX_CEILING_PITCH = 15.0, 85.0
DEFAULT_FLOOR_PITCH = -30.0
MIN_FLOOR_PITCH, MAX_FLOOR_PITCH = -85.0, -5.0

# Type d'événement quand un bouton n'a pas de sort configuré
UNKNOWN_SPELL = "inconnu"

# Événement émis sur le bus de Home Assistant à chaque sort
EVENT_SPELL = f"{DOMAIN}_spell"

STORAGE_VERSION = 1
