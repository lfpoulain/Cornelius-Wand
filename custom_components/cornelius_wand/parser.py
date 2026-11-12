"""Décodage des annonces BTHome v2 émises par la baguette (wand/bthome.cpp).

Module sans dépendance à Home Assistant, pour pouvoir le tester seul.
"""

from __future__ import annotations

from dataclasses import dataclass, field

OBJ_PACKET_ID = 0x00
OBJ_VOLTAGE = 0x0C
OBJ_BUTTON = 0x3A
OBJ_ROTATION = 0x3F
OBJ_DURATION = 0x42

BUTTON_PRESS = 0x01  # sort lancé
BUTTON_HOLD = 0x80  # « hold_press » : sort en préparation, le geste n'est pas fini

# Taille en octets de la valeur de chaque objet
OBJECT_SIZES = {
    OBJ_PACKET_ID: 1,
    OBJ_VOLTAGE: 2,
    OBJ_BUTTON: 1,
    OBJ_ROTATION: 2,
    OBJ_DURATION: 3,
}

# Inclinaison envoyée quand la baguette ne connaît pas sa visée (BTHOME_NO_AIM_PITCH)
NO_AIM_PITCH = -180.0


class BTHomeParseError(ValueError):
    """Annonce illisible ou dans un format non géré."""


@dataclass
class WandPacket:
    """Contenu d'une annonce de la baguette."""

    packet_id: int | None = None
    voltage: float | None = None
    pitch: float | None = None  # inclinaison de la visée, -90 à 90°
    aim_age_ms: int | None = None  # depuis combien de temps la pause de visée est finie
    buttons: list[int] = field(default_factory=list)  # événement de chaque bouton, dans l'ordre

    def _button_with(self, event: int) -> int | None:
        for index, value in enumerate(self.buttons):
            if value == event:
                return index + 1
        return None

    @property
    def pressed_button(self) -> int | None:
        """Numéro (à partir de 1) du bouton appuyé, c'est-à-dire du sort reconnu."""
        return self._button_with(BUTTON_PRESS)

    @property
    def preparing_button(self) -> int | None:
        """Sort qui commence : la baguette prévient avant la fin du geste."""
        return self._button_with(BUTTON_HOLD)

    @property
    def has_aim(self) -> bool:
        """La baguette a mesuré sa visée avant le sort."""
        return self.pitch is not None and -90.0 <= self.pitch <= 90.0

    @property
    def is_aim(self) -> bool:
        """Fin d'une pause de visée : aucun sort, mais une visée. L'annonce de présence n'en a pas."""
        return self.pressed_button is None and self.preparing_button is None and self.has_aim


def parse_bthome(data: bytes) -> WandPacket:
    """Décode les données de service BTHome (UUID 0xFCD2) d'une annonce."""
    if not data:
        raise BTHomeParseError("annonce vide")

    info = data[0]
    if info & 0x01:
        raise BTHomeParseError("annonce chiffrée non gérée")
    if (info >> 5) != 2:
        raise BTHomeParseError(f"version BTHome {info >> 5} non gérée")

    packet = WandPacket()
    pos = 1
    while pos < len(data):
        obj = data[pos]
        size = OBJECT_SIZES.get(obj)
        if size is None:
            # Comme le prévoit BTHome : on s'arrête au premier objet inconnu
            break
        value = data[pos + 1 : pos + 1 + size]
        if len(value) != size:
            raise BTHomeParseError(f"objet 0x{obj:02X} tronqué")
        pos += 1 + size

        if obj == OBJ_PACKET_ID:
            packet.packet_id = value[0]
        elif obj == OBJ_VOLTAGE:
            packet.voltage = int.from_bytes(value, "little") / 1000
        elif obj == OBJ_BUTTON:
            packet.buttons.append(value[0])
        elif obj == OBJ_ROTATION:
            packet.pitch = int.from_bytes(value, "little", signed=True) / 10
        elif obj == OBJ_DURATION:
            packet.aim_age_ms = int.from_bytes(value, "little")

    return packet
