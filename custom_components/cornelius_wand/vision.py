"""Analyse de la photo de la caméra au plafond par Jev (TypeSafe).

Jev ne rédige pas de réponse : il répond à des questions typées, chacune évaluée à part
sur le même « state » (https://docs.typesafe.ai). Sur la photo du moment où l'on visait :
- y a-t-il quelqu'un (« noul ») : une question « box » renvoie toujours un cadre, même
  sur une pièce vide, d'où cette vérification ;
- le cadre de la tête et des épaules de la personne qui tient la baguette (« box ») : sa
  confiance dit si quelqu'un est vraiment là ;
- le cadre de sa tête seule (« box ») : l'origine du bras. Plus petit et mieux placé (erreur
  0,023 contre 0,030 sur le banc du 2026-11-09), mais sa confiance sépare moins bien une
  pièce vide ; sans tête, l'origine est le centre des épaules ;
- le cadre de la main qui tient la baguette (« box »).
La direction tête → main et le choix de la cible sont calculés ensuite (aim.py).

Consignes courtes et en anglais (langue d'entraînement de Jev), contexte dans un champ
nommé du state, photo en multipart désignée par une référence unique, une seule cible par
question box, comme le recommande la doc de TypeSafe.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import struct
import time
from typing import Any
import uuid

import aiohttp

from .const import JEV_URL, VISION_MODEL

# Les images sont encore expérimentales chez TypeSafe : à activer à chaque requête
IMAGES_HEADER = "TypeSafe-Danger-Zone-Images"
PHOTO_PART = "photo"

# Une question « box » rend toujours un cadre, même sur une pièce vide : on ne le garde que
# si Jev voit quelqu'un. La présence seule ne suffit pas : sur des photos posées elle vaut
# 91–95 % avec quelqu'un et 35–77 % sur la pièce vide, mais pendant un geste réel elle est
# descendue à 19 % alors que la personne était bien sur la photo. La confiance du cadre des
# épaules sépare mieux les deux (pièce vide 17–22 %, quelqu'un 55–86 %, banc du 2026-11-09).
# Les cadres sont gardés si l'une ou l'autre est assez haute.
PRESENCE_MIN = 0.85
SHOULDERS_CONFIDENCE_MIN = 0.4
# Cadre de la main plus long que ça (rapport des côtés) : il contient l'avant-bras
HAND_ELONGATED = 2.5

SCENE = (
    "Top-down photo from a camera fixed on the ceiling, wide-angle lens. People are seen "
    "from above: mostly their head, shoulders and arms. Someone may be holding a wand, a "
    "thin stick about 30 cm long, and pointing it at an object of the room, arm outstretched."
)

DEFAULT_RATIO = 4 / 3  # photo 1024 x 768 de la caméra


class VisionError(Exception):
    """Photo ou API indisponible, ou réponse illisible."""


@dataclass
class Box:
    """Cadre trouvé sur la photo, en fractions de sa largeur et de sa hauteur."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def center(self) -> tuple[float, float]:
        return (round((self.left + self.right) / 2, 3), round((self.top + self.bottom) / 2, 3))


@dataclass
class VisionResult:
    person_present: float | None
    shoulders: Box | None  # None si personne
    shoulders_confidence: float | None
    hand: Box | None
    hand_confidence: float | None
    ratio: float  # largeur / hauteur de la photo
    latency_ms: int
    photo_age_ms: int | None
    head: Box | None = None  # None si personne, ou tête introuvable
    head_confidence: float | None = None

    @property
    def origin(self) -> tuple[float, float] | None:
        """D'où part le bras, et où est la personne : la tête, sinon le centre des épaules."""
        if self.head:
            return self.head.center
        return self.shoulders.center if self.shoulders else None

    @property
    def hand_point(self) -> tuple[float, float] | None:
        """Où est la main : le centre de son cadre, sauf s'il est très allongé.

        Un cadre au moins HAND_ELONGATED fois plus long que large contient aussi l'avant-bras
        (vu du plafond, bras tendu) : son centre tire la direction vers le coude. L'avant-bras
        le traverse alors en diagonale, et la main est le coin le plus loin de la tête. Sur un
        cadre normal, ce coin dégrade la direction (11,8° d'écart médian contre 2,7° au banc) :
        il ne sert qu'aux cadres allongés, qu'aucune photo du banc n'atteint (2 au plus).
        """
        hand = self.hand
        if hand is None:
            return None
        width = (hand.right - hand.left) * self.ratio  # en proportions réelles de la photo
        height = hand.bottom - hand.top
        origin = self.origin
        if origin is None or min(width, height) <= 0 or max(width, height) / min(width, height) < HAND_ELONGATED:
            return hand.center
        corners = [(x, y) for x in (hand.left, hand.right) for y in (hand.top, hand.bottom)]
        far = max(corners, key=lambda c: math.hypot((c[0] - origin[0]) * self.ratio, c[1] - origin[1]))
        return (round(far[0], 3), round(far[1], 3))

    @property
    def summary(self) -> str:
        if self.shoulders is None:
            seen = []
            if self.person_present is not None:
                seen.append(f"présence {self.person_present:.0%}")
            if self.shoulders_confidence is not None:
                seen.append(f"épaules {self.shoulders_confidence:.0%}")
            detail = f" ({', '.join(seen)})" if seen else ""
            return f"aucune personne détectée{detail}, Jev {self.latency_ms} ms"
        parts = []
        person = ("tête", self.head, self.head_confidence) if self.head else (
            "épaules", self.shoulders, self.shoulders_confidence
        )
        for name, box, confidence in (
            person,
            ("main", self.hand, self.hand_confidence),
        ):
            if box:
                conf = f" ({confidence:.0%})" if confidence is not None else ""
                parts.append(f"{name} {box.center[0]:.2f}, {box.center[1]:.2f}{conf}")
        return f"{', '.join(parts)}, Jev {self.latency_ms} ms"


def build_request(ref: str) -> dict[str, Any]:
    """Partie « request » du multipart ; la photo est la partie PHOTO_PART, désignée par ref."""
    return {
        "model": VISION_MODEL,
        "state": {"photo": ref, "scene": SCENE},
        "assets": {ref: {"type": "image", "part": PHOTO_PART}},
        "questions": {
            "presence": {
                "type": "noul",
                "instructions": "Is there a person in `photo`?",
            },
            "epaules": {
                "type": "box",
                "instructions": "the head and shoulders of the person holding the wand in `photo`",
            },
            "tete": {
                "type": "box",
                "instructions": "the head of the person holding the wand in `photo`",
            },
            "main": {
                "type": "box",
                # « Seulement la main » : sans, Jev englobe souvent l'avant-bras (banc du
                # 2026-11-09 : direction 1,6° d'écart médian contre 2,7°)
                "instructions": "only the hand holding the wand in `photo`, not the arm",
            },
        },
    }


def photo_ratio(photo: bytes) -> float:
    """Largeur / hauteur d'un JPEG, lue dans son en-tête."""
    i = 2
    while i + 9 < len(photo) and photo[:2] == b"\xff\xd8" and photo[i] == 0xFF:
        marker = photo[i + 1]
        length = struct.unpack(">H", photo[i + 2 : i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", photo[i + 5 : i + 9])
            return width / height if height else DEFAULT_RATIO
        i += 2 + length
    return DEFAULT_RATIO


def _answer(answers: dict[str, Any], key: str) -> dict[str, Any]:
    """Réponse à une question, vide si elle manque ou n'a pas la forme attendue."""
    answer = answers.get(key)
    return answer if isinstance(answer, dict) else {}


def _number(value: Any) -> float | None:
    """Probabilité ou confiance, si c'est bien un nombre."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _read_box(answer: dict[str, Any]) -> Box | None:
    try:
        box = answer["box"]
        left, top, right, bottom = (float(box[side]) for side in ("left", "top", "right", "bottom"))
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (left, top, right, bottom)):
        return None
    # Un cadre inversé reste un cadre : on remet les côtés dans l'ordre
    left, right = sorted((left, right))
    top, bottom = sorted((top, bottom))
    return Box(left, top, right, bottom)


def parse_response(
    data: dict[str, Any], ratio: float, latency_ms: int, photo_age_ms: int | None
) -> VisionResult:
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        raise VisionError(f"réponse inattendue de Jev : {str(data)[:200]}")
    present = _number(_answer(answers, "presence").get("noul"))
    if present is not None and not 0 <= present <= 1:
        present = None  # hors de [0, 1], NaN et infinis ne sont pas des probabilités
    shoulders = _answer(answers, "epaules")
    head = _answer(answers, "tete")
    hand = _answer(answers, "main")
    # Jev peut fournir des cadres même sans personne : il faut une présence valide, et que Jev
    # voie quelqu'un (présence haute, ou cadre des épaules sûr). La confiance des épaules est
    # gardée même refusée quand la présence est valide : le statut dit pourquoi.
    shoulders_confidence = _number(shoulders.get("confidence")) if present is not None else None
    if shoulders_confidence is not None and not 0 <= shoulders_confidence <= 1:
        shoulders_confidence = None
    seen = present is not None and (
        present >= PRESENCE_MIN
        or (shoulders_confidence is not None and shoulders_confidence >= SHOULDERS_CONFIDENCE_MIN)
    )
    shoulders_box = _read_box(shoulders) if seen else None
    head_box = _read_box(head) if seen else None
    hand_box = _read_box(hand) if seen else None
    return VisionResult(
        person_present=present,
        shoulders=shoulders_box,
        shoulders_confidence=shoulders_confidence if shoulders_box or not seen else None,
        hand=hand_box,
        hand_confidence=_number(hand.get("confidence")) if hand_box else None,
        ratio=ratio,
        latency_ms=latency_ms,
        photo_age_ms=photo_age_ms,
        head=head_box,
        head_confidence=_number(head.get("confidence")) if head_box else None,
    )


async def fetch_photo(
    session: aiohttp.ClientSession, camera_url: str, ago_ms: int
) -> tuple[bytes, int | None]:
    """Photo de la caméra, prise il y a ago_ms millisecondes (elle en garde au moins 2,6 s, PHOTO_KEEP_MS)."""
    url = f"{camera_url.rstrip('/')}/capture.jpg"
    try:
        # Court : le sort attend la vision au plus 1,5 s (VISION_DEADLINE dans wand.py)
        async with session.get(
            url, params={"ago": str(ago_ms)}, timeout=aiohttp.ClientTimeout(total=1.5, connect=0.5)
        ) as response:
            if response.status != 200:
                raise VisionError(f"caméra : erreur {response.status}")
            age = response.headers.get("X-Photo-Age-Ms")
            return await response.read(), int(age) if age and age.isdigit() else None
    except (aiohttp.ClientError, TimeoutError) as err:
        raise VisionError(f"caméra injoignable ({url})") from err


async def analyze(
    session: aiohttp.ClientSession, api_key: str, photo: bytes, photo_age_ms: int | None
) -> VisionResult:
    # Une référence neuve par photo, qui ne peut pas apparaître par hasard dans le texte
    request = build_request(f"{uuid.uuid4()}-photo")
    form = aiohttp.FormData()
    form.add_field("request", json.dumps(request), content_type="application/json")
    form.add_field(PHOTO_PART, photo, filename="photo.jpg", content_type="image/jpeg")
    started = time.monotonic()
    try:
        async with session.post(
            f"{JEV_URL}/systemone",
            data=form,
            headers={"Authorization": f"Bearer {api_key}", IMAGES_HEADER: "true"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status != 200:
                detail = (await response.text())[:200]
                raise VisionError(f"Jev : erreur {response.status} {detail}")
            data = await response.json()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise VisionError("Jev injoignable") from err
    except ValueError as err:  # JSON tronqué, ou texte qui n'est pas de l'UTF-8
        raise VisionError("réponse illisible de Jev") from err
    latency_ms = round((time.monotonic() - started) * 1000)
    return parse_response(data, photo_ratio(photo), latency_ms, photo_age_ms)
