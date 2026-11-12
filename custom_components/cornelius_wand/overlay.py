"""Ce que la visée a vu, dessiné sur la photo de l'entité « Photo analysée »."""

from __future__ import annotations

import io
import math
import unicodedata

from PIL import Image, ImageDraw, ImageFont

from .aim import AimResult, Target
from .vision import Box, VisionResult

SHOULDERS = (63, 183, 163)
HEAD = (120, 170, 255)
HAND = (255, 107, 107)
TARGET = (255, 255, 255)
CHOSEN = (240, 180, 41)


# Police avec les lettres accentuées si le système en a une ; sinon celle de Pillow, sans accents
FONTS = (
    "DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "arial.ttf",
)


def _font(size: int) -> tuple[ImageFont.ImageFont, bool]:
    """La police, et si elle a les accents (« Lumière » s'affichait « Lumi□re »)."""
    for name in FONTS:
        try:
            return ImageFont.truetype(name, size), True
        except OSError:
            continue
    try:
        return ImageFont.load_default(size), False
    except TypeError:  # Pillow trop ancien pour choisir la taille
        return ImageFont.load_default(), False


def _plain(text: str) -> str:
    """Sans accents, pour la police de Pillow : « Lumière » devient « Lumiere »."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def draw(photo: bytes, vision: VisionResult, result: AimResult, targets: dict[str, Target]) -> bytes:
    """Épaules (vert), tête (bleu), main (rouge), direction du bras depuis la tête, et cibles
    (la retenue en jaune)."""
    image = Image.open(io.BytesIO(photo)).convert("RGB")
    width, height = image.size
    pen = ImageDraw.Draw(image)
    font, accents = _font(max(14, width // 60))

    def rect(box: Box, color: tuple[int, int, int]) -> None:
        pen.rectangle([box.left * width, box.top * height, box.right * width, box.bottom * height],
                      outline=color, width=3)

    for key, target in targets.items():
        if target.position:
            px, py = target.position
            # Cible hors champ (placée dans la marge du panneau) : dessinée au bord, dans sa direction
            inside = 0 <= px <= 1 and 0 <= py <= 1
            x = min(max(px * width, 9), width - 9)
            y = min(max(py * height, 9), height - 9)
            color = CHOSEN if key == result.target else TARGET
            pen.ellipse([x - 9, y - 9, x + 9, y + 9], outline=color, width=3 if inside else 1)
            name = target.name if inside else f"{target.name} (hors champ)"
            if not accents:
                name = _plain(name)
            text_width = pen.textlength(name, font=font)
            tx = x + 13 if x + 13 + text_width <= width else x - 13 - text_width
            pen.text((tx, min(max(y - 9, 0), height - 20)), name, font=font, fill=color,
                     stroke_width=2, stroke_fill=(0, 0, 0))

    if vision.shoulders:
        rect(vision.shoulders, SHOULDERS)
    if vision.head:
        rect(vision.head, HEAD)
    if vision.hand:
        rect(vision.hand, HAND)
    if (origin := vision.origin) and result.angle is not None:
        sx, sy = origin[0] * width, origin[1] * height
        length = math.hypot(width, height)
        angle = math.radians(result.angle)
        pen.line([sx, sy, sx + math.cos(angle) * length, sy + math.sin(angle) * length], fill=HAND, width=3)

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=85)
    return output.getvalue()
