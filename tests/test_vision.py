"""Tests de la vision : requête à Jev, lecture de sa réponse, photo annotée."""

import io

from PIL import Image
import pytest

from custom_components.cornelius_wand.aim import Target, aim
from custom_components.cornelius_wand.overlay import draw
from custom_components.cornelius_wand.vision import (
    PHOTO_PART,
    PRESENCE_MIN,
    SHOULDERS_CONFIDENCE_MIN,
    VisionError,
    build_request,
    parse_response,
    photo_ratio,
)

from .helpers import PHOTO, jev_answer, jpeg


def test_request_follows_the_jev_format():
    request = build_request("ref-photo")
    assert request["model"] == "jev-1.14.0"
    assert request["state"]["photo"] == "ref-photo"
    assert "ceiling" in request["state"]["scene"]
    assert request["assets"] == {"ref-photo": {"type": "image", "part": PHOTO_PART}}
    questions = request["questions"]
    assert list(questions) == ["presence", "epaules", "tete", "main"]
    assert questions["presence"]["type"] == "noul"
    assert questions["epaules"]["type"] == questions["tete"]["type"] == questions["main"]["type"] == "box"
    assert "`photo`" in questions["main"]["instructions"]


def test_photo_ratio_is_read_from_the_jpeg():
    assert photo_ratio(PHOTO) == pytest.approx(4 / 3)
    assert photo_ratio(jpeg(800, 800)) == 1
    assert photo_ratio(b"pas une photo") == pytest.approx(4 / 3)


def test_boxes_are_read():
    vision = parse_response(jev_answer(shoulders=(0.3, 0.6), hand=(0.4, 0.55)), 4 / 3, 170, 450)
    assert vision.shoulders.center == (0.3, 0.6)
    assert vision.hand.center == (0.4, 0.55)
    assert vision.head.center == (0.3, 0.6)
    assert vision.origin == (0.3, 0.6)
    assert vision.summary == "tête 0.30, 0.60 (80%), main 0.40, 0.55 (70%), Jev 170 ms"


def test_arm_starts_from_the_head():
    """Vue d'en haut, le cadre tête + épaules peut couvrir tout le buste : le bras part de la tête."""
    vision = parse_response(jev_answer(shoulders=(0.5, 0.85), head=(0.5, 0.95)), 4 / 3, 170, None)
    assert vision.shoulders.center == (0.5, 0.85)
    assert vision.origin == (0.5, 0.95)


def test_without_head_the_arm_starts_from_the_shoulders():
    vision = parse_response(jev_answer(shoulders=(0.3, 0.6), head=None), 4 / 3, 170, None)
    assert vision.head is None
    assert vision.origin == (0.3, 0.6)
    assert vision.summary.startswith("épaules 0.30, 0.60 (86%)")


def test_empty_room_has_no_head_either():
    vision = parse_response(jev_answer(present=0.5, shoulders_confidence=0.2), 4 / 3, 170, None)
    assert vision.head is None and vision.origin is None


def test_empty_room_has_no_boxes():
    # Mesuré sur la pièce vide : présence 37–77 %, cadre des épaules 17–22 %
    vision = parse_response(jev_answer(present=0.77, shoulders_confidence=0.22), 4 / 3, 170, None)
    assert vision.shoulders is None and vision.hand is None
    assert vision.summary == "aucune personne détectée (présence 77%, épaules 22%), Jev 170 ms"


def test_low_presence_with_confident_shoulders_keeps_boxes():
    """Pendant un geste, la présence de Jev peut tomber à 19 % alors que la personne est là :
    un cadre des épaules sûr suffit."""
    vision = parse_response(jev_answer(present=0.19, shoulders_confidence=0.8), 4 / 3, 170, None)
    assert vision.shoulders is not None and vision.hand is not None
    assert vision.shoulders_confidence == 0.8


@pytest.mark.parametrize("confidence", [None, "0.9", float("nan"), 1.5])
def test_low_presence_needs_a_valid_shoulders_confidence(confidence):
    vision = parse_response(jev_answer(present=0.5, shoulders_confidence=confidence), 4 / 3, 170, None)
    assert vision.shoulders is None


def test_shoulders_confidence_threshold():
    kept = parse_response(jev_answer(present=0.3, shoulders_confidence=SHOULDERS_CONFIDENCE_MIN), 4 / 3, 1, None)
    assert kept.shoulders is not None


@pytest.mark.parametrize(
    "present",
    [None, "0.94", True, False, float("nan"), float("inf"), float("-inf"), -0.1, 1.1],
)
def test_invalid_presence_rejects_otherwise_valid_boxes(present):
    vision = parse_response(jev_answer(present=present), 4 / 3, 170, None)
    assert vision.person_present is None
    assert vision.shoulders is None and vision.hand is None
    assert vision.shoulders_confidence is None and vision.hand_confidence is None


def test_missing_presence_rejects_otherwise_valid_boxes():
    answer = jev_answer()
    del answer["answers"]["presence"]
    vision = parse_response(answer, 4 / 3, 170, None)
    assert vision.person_present is None
    assert vision.shoulders is None and vision.hand is None


@pytest.mark.parametrize("present", [PRESENCE_MIN, 1])
def test_valid_presence_at_threshold_or_above_keeps_boxes(present):
    vision = parse_response(jev_answer(present=present), 4 / 3, 170, None)
    assert vision.person_present == present
    assert vision.shoulders is not None and vision.hand is not None


def test_unexpected_answer_raises():
    with pytest.raises(VisionError):
        parse_response({"error": "nope"}, 4 / 3, 100, None)


def test_answers_of_an_unexpected_shape_are_ignored():
    vision = parse_response({"answers": {"presence": "x"}}, 4 / 3, 100, None)
    assert vision.person_present is None and vision.shoulders is None

    vision = parse_response({"answers": {"presence": {"noul": "0.9"}, "epaules": []}}, 4 / 3, 100, None)
    assert vision.person_present is None and vision.shoulders is None

    answer = jev_answer()
    answer["answers"]["main"]["confidence"] = "haute"
    assert parse_response(answer, 4 / 3, 100, None).summary.startswith("tête 0.50, 0.50 (80%), main 0.62")


def test_inverted_box_is_put_back_in_order():
    answer = jev_answer()
    answer["answers"]["epaules"]["box"] = {"left": 0.6, "right": 0.4, "top": 0.55, "bottom": 0.45}
    answer["answers"]["main"]["box"] = {"left": float("nan"), "right": 0.7, "top": 0.4, "bottom": 0.5}
    vision = parse_response(answer, 4 / 3, 170, None)
    assert (vision.shoulders.left, vision.shoulders.right) == (0.4, 0.6)
    assert (vision.shoulders.top, vision.shoulders.bottom) == (0.45, 0.55)
    assert vision.hand is None
    draw(PHOTO, vision, aim({}, vision.shoulders.center, None, 0, 25, 4 / 3), {})


def test_annotated_photo_keeps_its_size():
    vision = parse_response(jev_answer(), 4 / 3, 170, None)
    targets = {"lampe": Target("Lampe", 0.9, 0.5)}
    drawn = draw(PHOTO, vision, aim(targets, vision.shoulders.center, vision.hand.center, 0, 25, 4 / 3), targets)
    assert Image.open(io.BytesIO(drawn)).size == (1024, 768)
    assert drawn != PHOTO


def test_target_out_of_view_is_drawn_at_the_edge():
    """Cible placée dans la marge du panneau : visible au bord de la photo, dans sa direction."""
    vision = parse_response(jev_answer(), 4 / 3, 170, None)
    targets = {"couloir": Target("Couloir", 1.3, 0.1)}
    result = aim(targets, vision.shoulders.center, vision.hand.center, 0, 25, 4 / 3)

    def edge(drawn: bytes) -> list:
        image = Image.open(io.BytesIO(drawn)).convert("L")
        return [image.getpixel((x, y)) for x in range(1000, 1024) for y in range(60, 95)]

    with_target, without = edge(draw(PHOTO, vision, result, targets)), edge(draw(PHOTO, vision, result, {}))
    assert sum(abs(a - b) > 60 for a, b in zip(with_target, without)) > 20


def test_target_names_keep_their_accents_or_lose_them_cleanly():
    """« Lumière » s'affichait « Lumi□re » : la police de Pillow n'a pas les accents."""
    from custom_components.cornelius_wand import overlay

    assert overlay._plain("Lumière (hors champ)") == "Lumiere (hors champ)"
    vision = parse_response(jev_answer(), 4 / 3, 170, None)
    targets = {"lumiere": Target("Lumière", 0.2, 0.2)}
    drawn = draw(PHOTO, vision, aim(targets, vision.origin, vision.hand.center, 0, 25, 4 / 3), targets)
    assert Image.open(io.BytesIO(drawn)).size == (1024, 768)


def test_hand_is_the_center_of_a_normal_box():
    vision = parse_response(jev_answer(hand=(0.62, 0.5)), 4 / 3, 170, None)
    assert vision.hand_point == vision.hand.center == (0.62, 0.5)


def test_hand_is_the_far_corner_of_a_box_that_holds_the_forearm():
    """Photo du 2026-11-09 : cadre « main » haut et étroit (avant-bras compris), bras vers la
    Cuisine à ~38°. Son centre donnait 12,7° et ratait la cible ; le coin loin de la tête la trouve."""
    answer = jev_answer(shoulders=(0.565, 0.635), head=(0.565, 0.635))
    answer["answers"]["main"]["box"] = {"left": 0.642, "right": 0.689, "top": 0.5625, "bottom": 0.768}
    vision = parse_response(answer, 4 / 3, 170, None)
    assert vision.hand_point == (0.689, 0.768)
    cuisine = {"cuisine": Target("Cuisine", 0.83, 0.99)}
    result = aim(cuisine, vision.origin, vision.hand_point, 0, 25, 4 / 3)
    assert 36 < result.angle < 42
    assert result.target == "cuisine"
    assert aim(cuisine, vision.origin, vision.hand.center, 0, 25, 4 / 3).target is None  # l'ancien calcul

