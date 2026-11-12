"""Quelle cible est visée : direction du bras sur la photo, hauteur d'après la baguette.

Module sans dépendance à Home Assistant.

- **À plat**, la direction va du centre des épaules au centre de la main qui tient la
  baguette (cadres trouvés par Jev sur la photo vue du plafond). Vue depuis les épaules,
  la cible retenue est celle dont la direction est la plus proche, à `tolerance` degrés
  près. Les cibles sont placées sur la photo, à la souris (panneau Baguette).
- **En hauteur**, l'inclinaison de la baguette donne la surface visée (sol, mur,
  plafond, seuils réglables) : seules les cibles de cette surface sont comparées. Ça distingue deux objets
  l'un au-dessus de l'autre, que la photo ne sépare pas.
- **Au plafond**, si la main paraît trop près des épaules (bras levé vers la caméra, moins
  de MIN_REACH), il n'y a pas de direction : la cible placée au plafond la plus proche de
  la personne est retenue (une cible sans surface ne compte pas alors), à condition que la
  suivante soit nettement plus loin ; sinon, aucune. Avec une direction, elle est comparée
  comme ailleurs aux cibles du plafond et à celles sans surface.

Les positions sont en fractions de la largeur et de la hauteur de la photo : on repasse
en proportions réelles (x multiplié par largeur / hauteur) pour que les angles soient justes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from .const import DEFAULT_CEILING_PITCH, DEFAULT_FLOOR_PITCH

Point = tuple[float, float]

# Surface visée, d'après l'inclinaison de la baguette (seuils réglables, const.py)
LEVELS = ("sol", "mur", "plafond")

# En dessous, la main est trop près des épaules (en hauteurs de photo) pour donner une direction
MIN_REACH = 0.04
# Bras levé sans direction : la cible du plafond la plus proche n'est retenue que si la
# suivante est au moins tant de fois plus loin de la personne ; sinon, laquelle est visée ?
CEILING_DISTANCE_RATIO = 1.3


def surface_from_pitch(
    pitch: float | None, ceiling: float = DEFAULT_CEILING_PITCH, floor: float = DEFAULT_FLOOR_PITCH
) -> str | None:
    if pitch is None:
        return None
    if pitch >= ceiling:
        return "plafond"
    if pitch <= floor:
        return "sol"
    return "mur"


@dataclass
class Target:
    """Une cible : sa position sur la photo, et la surface où elle se trouve si on l'a dite."""

    name: str
    x: float | None = None
    y: float | None = None
    level: str | None = None  # sol, mur ou plafond ; None : n'importe laquelle

    @property
    def position(self) -> Point | None:
        return None if self.x is None or self.y is None else (self.x, self.y)


@dataclass
class AimResult:
    target: str | None  # clé de la cible retenue
    angle: float | None = None  # direction du bras sur la photo (0° : vers la droite, 90° : vers le bas)
    gap: float | None = None  # écart avec la cible retenue, ou avec la plus proche
    gaps: dict[str, float] = field(default_factory=dict)  # écart avec chaque cible comparée
    refusal: str | None = None  # pourquoi aucune cible


def angle_between(a: float, b: float) -> float:
    """Écart entre deux directions, de 0 à 180°."""
    return abs((a - b + 180) % 360 - 180)


def direction(origin: Point, point: Point, ratio: float) -> tuple[float, float]:
    """Angle (degrés) et distance (en hauteurs de photo) de origin vers point."""
    dx = (point[0] - origin[0]) * ratio
    dy = point[1] - origin[1]
    return math.degrees(math.atan2(dy, dx)) % 360, math.hypot(dx, dy)


def aim(
    targets: dict[str, Target],
    shoulders: Point | None,
    hand: Point | None,
    pitch: float | None,
    tolerance: float,
    ratio: float,
    ceiling: float = DEFAULT_CEILING_PITCH,
    floor: float = DEFAULT_FLOOR_PITCH,
) -> AimResult:
    surface = surface_from_pitch(pitch, ceiling, floor)
    candidates = {
        key: target
        for key, target in targets.items()
        if target.position and (target.level is None or surface is None or target.level == surface)
    }
    if not candidates:
        where = f" au {surface}" if surface else ""
        return AimResult(None, refusal=f"aucune cible placée{where}")
    if shoulders is None:
        return AimResult(None, refusal="personne introuvable sur la photo")
    if hand is None:
        return AimResult(None, refusal="main introuvable sur la photo")

    reach = direction(shoulders, hand, ratio)[1]
    if reach < MIN_REACH:
        if surface == "plafond":
            # Bras levé vers la caméra : sans direction, seules les cibles placées au plafond comptent
            ceiling = {k: t for k, t in candidates.items() if t.level == "plafond"}
            if not ceiling:
                return AimResult(None, refusal="bras levé : aucune cible placée au plafond")
            distances = {k: direction(shoulders, t.position, ratio)[1] for k, t in ceiling.items()}
            nearest, *farther = sorted(distances, key=distances.get)
            if farther and distances[farther[0]] <= distances[nearest] * CEILING_DISTANCE_RATIO:
                names = f"{targets[nearest].name} et {targets[farther[0]].name}"
                return AimResult(None, refusal=f"bras levé : {names} à distance presque égale, vise moins à la verticale")
            return AimResult(nearest)
        return AimResult(None, refusal="main introuvable ou trop près des épaules")

    angle = direction(shoulders, hand, ratio)[0]
    gaps = {
        key: round(angle_between(angle, direction(shoulders, target.position, ratio)[0]), 1)
        for key, target in candidates.items()
    }
    best = min(gaps, key=gaps.get)
    result = AimResult(None, round(angle, 1), gaps[best], gaps)
    if gaps[best] > tolerance:
        result.refusal = f"la plus proche, {targets[best].name}, est à {gaps[best]:.0f}°"
    else:
        result.target = best
    return result
