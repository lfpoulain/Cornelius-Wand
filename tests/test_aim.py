"""Tests de la visée : direction du bras sur la photo et surface d'après l'inclinaison."""

import pytest

from custom_components.cornelius_wand.aim import Target, aim, angle_between, direction, surface_from_pitch

RATIO = 4 / 3
SHOULDERS = (0.5, 0.5)
TOWARDS_RIGHT = (0.6, 0.5)  # main à droite des épaules : bras vers 0°


@pytest.mark.parametrize(
    ("pitch", "surface"), [(80, "plafond"), (45, "plafond"), (10, "mur"), (-30, "sol"), (None, None)]
)
def test_surface_from_pitch(pitch, surface):
    assert surface_from_pitch(pitch) == surface


def test_surface_thresholds_can_be_moved():
    assert surface_from_pitch(50, ceiling=60) == "mur"  # lampe haute sur un mur
    assert surface_from_pitch(-25, floor=-20) == "sol"
    lamp = {"applique": Target("Applique", 0.9, 0.5, "mur")}
    assert aim(lamp, SHOULDERS, TOWARDS_RIGHT, 50, 25, RATIO).refusal == "aucune cible placée au plafond"
    assert aim(lamp, SHOULDERS, TOWARDS_RIGHT, 50, 25, RATIO, ceiling=60).target == "applique"


def test_angle_between_wraps_round():
    assert angle_between(355, 10) == 15
    assert angle_between(10, 190) == 180


def test_direction_corrects_the_photo_ratio():
    # 0,4 en largeur vaut 0,533 en hauteurs de photo : 37° et non 45°
    angle, reach = direction(SHOULDERS, (0.9, 0.9), RATIO)
    assert angle == pytest.approx(36.87, abs=0.01)
    assert reach == pytest.approx(0.667, abs=0.001)


def test_the_target_in_the_direction_of_the_arm():
    targets = {"lampadaire": Target("Lampadaire", 0.95, 0.52), "couloir": Target("Couloir", 0.5, 0.95)}
    result = aim(targets, SHOULDERS, TOWARDS_RIGHT, 0, 25, RATIO)
    assert result.target == "lampadaire"
    assert result.angle == 0
    assert result.gap == pytest.approx(1.9, abs=0.1)
    assert result.gaps["couloir"] == pytest.approx(90)


def test_too_far_from_any_target():
    result = aim({"couloir": Target("Couloir", 0.5, 0.95)}, SHOULDERS, TOWARDS_RIGHT, 0, 25, RATIO)
    assert result.target is None
    assert result.refusal == "la plus proche, Couloir, est à 90°"


def test_pitch_separates_targets_one_above_the_other():
    targets = {
        "lampe": Target("Lampe", 0.9, 0.5, "mur"),
        "prise": Target("Prise", 0.9, 0.5, "sol"),
        "radio": Target("Radio", 0.9, 0.5),  # sans surface : n'importe laquelle
    }
    assert aim(targets, SHOULDERS, TOWARDS_RIGHT, -40, 25, RATIO).gaps.keys() == {"prise", "radio"}
    assert aim(targets, SHOULDERS, TOWARDS_RIGHT, 5, 25, RATIO).gaps.keys() == {"lampe", "radio"}


def test_ceiling_with_the_arm_raised_takes_the_nearest_ceiling_target():
    targets = {
        "plafonnier": Target("Plafonnier", 0.55, 0.5, "plafond"),
        "stripled": Target("Stripled", 0.05, 0.95, "plafond"),
    }
    # Bras levé vers la caméra : la main est presque sur les épaules
    result = aim(targets, SHOULDERS, (0.51, 0.5), 80, 25, RATIO)
    assert result.target == "plafonnier"
    assert result.angle is None


@pytest.mark.parametrize("pitch", [None, -45, 0, 45, 80])
def test_missing_hand_never_selects_a_target(pitch):
    targets = {
        "plafonnier": Target("Plafonnier", 0.55, 0.5, "plafond"),
        "prise": Target("Prise", 0.9, 0.5, "sol"),
        "applique": Target("Applique", 0.9, 0.5, "mur"),
    }
    result = aim(targets, SHOULDERS, None, pitch, 25, RATIO)
    assert result.target is None
    assert result.refusal == "main introuvable sur la photo"


def test_arm_raised_between_two_ceiling_targets_at_almost_the_same_distance():
    # Lustre à 0,11 de la personne, plafonnier à 0,13 : lequel est visé ? Aucun plutôt que le mauvais
    targets = {
        "plafonnier": Target("Plafonnier", 0.6, 0.5, "plafond"),
        "lustre": Target("Lustre", 0.5, 0.39, "plafond"),
    }
    result = aim(targets, SHOULDERS, (0.51, 0.5), 80, 25, RATIO)
    assert result.target is None
    assert result.refusal == "bras levé : Lustre et Plafonnier à distance presque égale, vise moins à la verticale"
    # Bras moins vertical : sa direction les sépare
    assert aim(targets, SHOULDERS, TOWARDS_RIGHT, 80, 25, RATIO).target == "plafonnier"
    # Lustre nettement plus loin : le plafonnier, le plus proche, est retenu
    targets["lustre"].y = 0.3
    assert aim(targets, SHOULDERS, (0.51, 0.5), 80, 25, RATIO).target == "plafonnier"


def test_arm_raised_ignores_targets_without_a_surface():
    # Sans direction, une cible sans surface ne peut pas être désignée
    radio = {"radio": Target("Radio", 0.9, 0.9)}
    assert aim(radio, SHOULDERS, (0.51, 0.5), 80, 25, RATIO).refusal == "bras levé : aucune cible placée au plafond"
    targets = radio | {"lampe": Target("Lampe", 0.52, 0.5), "stripled": Target("Stripled", 0.05, 0.95, "plafond")}
    assert aim(targets, SHOULDERS, (0.51, 0.5), 80, 25, RATIO).target == "stripled"


def test_short_arm_elsewhere_gives_no_target():
    result = aim({"lampe": Target("Lampe", 0.9, 0.5)}, SHOULDERS, (0.51, 0.5), 0, 25, RATIO)
    assert result.refusal == "main introuvable ou trop près des épaules"


def test_nobody_or_nothing_placed():
    placed = {"lampe": Target("Lampe", 0.9, 0.5)}
    assert aim(placed, None, None, 0, 25, RATIO).refusal == "personne introuvable sur la photo"
    assert aim({"lampe": Target("Lampe")}, SHOULDERS, TOWARDS_RIGHT, None, 25, RATIO).refusal == "aucune cible placée"
    assert aim(placed | {"p": Target("P", 0.1, 0.1, "plafond")}, SHOULDERS, TOWARDS_RIGHT, 0, 25, RATIO).target == "lampe"
    wall = {"lampe": Target("Lampe", 0.9, 0.5, "mur")}
    assert aim(wall, SHOULDERS, TOWARDS_RIGHT, -60, 25, RATIO).refusal == "aucune cible placée au sol"
