"""Banc d'essai de la vision : Jev sur des photos de la pièce dont on connaît la vérité.

    python bench/bench.py              # toutes les photos de bench/photos/
    python bench/bench.py --aide       # copies quadrillées finement pour noter les vraies positions

À lancer avec un Python où Home Assistant est installé : l'analyse est celle de
l'intégration (custom_components/cornelius_wand/vision.py), telle quelle.

Mesuré sur chaque photo : l'écart entre le centre des épaules trouvé par Jev et la vraie
position, le même écart pour la main qui tient la baguette, l'écart de direction du bras
(épaules → main, vue d'en haut, comme aim.py), la présence et le temps de réponse. Les
séries prises avec capture.py --label (avec, sans, vide) sont aussi comparées entre elles.

Clé API dans bench/.env (TYPESAFE_API_KEY=...), jamais dans le dépôt.
Copier truth.example.json en truth.json pour annoter un jeu local.
Positions de référence dans bench/truth.json (non versionné) : {"photo.jpg": {"epaules": [x, y], "main": [x, y]}},
de 0 à 1 : centre de la tête et des épaules, et centre de la main qui tient la baguette
(absente sans baguette). Résultats dans bench/results/.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import os
from pathlib import Path
import statistics
import sys
from typing import Any

import aiohttp
from PIL import Image, ImageDraw, ImageFont

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH.parent))

from custom_components.cornelius_wand import aim, vision  # noqa: E402

PHOTOS = BENCH / "photos"
RESULTS = BENCH / "results"
TRUTH = BENCH / "truth.json"

COLORS = {
    "vérité": (255, 255, 255),
    "jev": (0, 230, 255),
}


# ----------------------------------------------------------------------
# Outils
# ----------------------------------------------------------------------
def load_keys() -> dict[str, str]:
    keys = {name: os.environ.get(name, "") for name in ("TYPESAFE_API_KEY",)}
    env = BENCH / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() in keys and value.strip():
                keys[name.strip()] = value.strip().strip('"')
    return keys


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def label(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, size: int, color) -> None:
    box = draw.textbbox(xy, text, font=font(size), anchor="mm")
    draw.rectangle([box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2], fill=(0, 0, 0, 170))
    draw.text(xy, text, font=font(size), fill=color, anchor="mm")


# ----------------------------------------------------------------------
# Méthodes : chacune renvoie {x, y, main, confiance, presence, ms, detail} ou {erreur}
# ----------------------------------------------------------------------
async def jev(session, keys, raw: bytes) -> dict[str, Any]:
    """L'analyse de l'intégration : origine du bras (la tête, sinon les épaules) et main."""
    result = await vision.analyze(session, keys["TYPESAFE_API_KEY"], raw, None)
    x, y = result.origin or (None, None)
    hand = list(result.hand_point) if result.hand else None
    presence = "?" if result.person_present is None else f"{result.person_present:.0%}"
    return {
        "x": x,
        "y": y,
        "main": hand,
        "confiance": result.shoulders_confidence,
        "presence": result.person_present,
        "ms": result.latency_ms,
        "detail": f"présence {presence}, " + (f"main {hand[0]:.2f}, {hand[1]:.2f}" if hand else "pas de main"),
    }


METHODS = {"jev": jev}
NEEDS = {"jev": "TYPESAFE_API_KEY"}


async def run_method(name, session, keys, raw) -> dict[str, Any]:
    try:
        return await METHODS[name](session, keys, raw)
    except Exception as err:  # noqa: BLE001 : on veut le comparatif même si une méthode échoue
        return {"erreur": f"{type(err).__name__} : {err}"}


# ----------------------------------------------------------------------
# Rendu
# ----------------------------------------------------------------------
def shoulders_of(result: dict) -> tuple[float, float] | None:
    return None if result.get("x") is None else (result["x"], result["y"])


def draw_points(raw: bytes, truth: dict, results: dict[str, dict], path: Path) -> None:
    """Épaules en rond, main en carré : la vérité en blanc, chaque méthode dans sa couleur."""
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    width, height = image.size
    draw = ImageDraw.Draw(image)
    points = [("vérité", truth.get("epaules"), truth.get("main"))]
    points += [(n, shoulders_of(r), r.get("main")) for n, r in results.items()]
    for name, shoulders, hand in points:
        if shoulders:
            x, y = shoulders[0] * width, shoulders[1] * height
            draw.ellipse([x - 9, y - 9, x + 9, y + 9], outline=COLORS[name], width=4)
        if hand:
            x, y = hand[0] * width, hand[1] * height
            draw.rectangle([x - 7, y - 7, x + 7, y + 7], outline=COLORS[name], width=3)
    for i, name in enumerate(COLORS):
        draw.rectangle([10, 10 + i * 24, 26, 26 + i * 24], fill=COLORS[name])
        draw.text((32, 10 + i * 24), name, font=font(16), fill=COLORS[name])
    image.save(path, quality=85)


def distance(found, real) -> float | None:
    """Écart entre deux points de la photo, si les deux sont connus."""
    if not found or not real:
        return None
    return round(math.dist(found, real), 3)


def arm_angle(shoulders, hand, ratio: float) -> float | None:
    """Direction épaules → main, comme aim.py ; None si le bras ne donne pas de direction."""
    if not shoulders or not hand:
        return None
    angle, reach = aim.direction(shoulders, hand, ratio)
    return angle if reach >= aim.MIN_REACH else None


def measure(result: dict, truth: dict, ratio: float) -> None:
    """Écarts avec la vérité : épaules, main, et direction du bras."""
    if "erreur" in result:  # déjà compté dans les échecs
        return
    shoulders = shoulders_of(result)
    result["erreur_position"] = distance(shoulders, truth.get("epaules"))
    result["erreur_main"] = distance(result.get("main"), truth.get("main"))
    found = arm_angle(shoulders, result.get("main"), ratio)
    real = arm_angle(truth.get("epaules"), truth.get("main"), ratio)
    result["erreur_angle"] = None if found is None or real is None else round(aim.angle_between(found, real), 1)
    result["sans_direction"] = real is not None and found is None  # aim.py refuserait la visée


def photo_label(name: str) -> str | None:
    """« avec », « sans » ou « vide », d'après la fin du nom donnée par capture.py --label."""
    stem = Path(name).stem
    return next((label for label in ("avec", "sans", "vide") if stem.endswith(f"-{label}")), None)


def label_table(rows: list[dict], methods: list[str]) -> list[str]:
    """Présence et cadre de main, selon ce que montre vraiment la photo."""
    labelled = [r for r in rows if photo_label(r["photo"])]
    if not labelled:
        return []
    lines = ["", "## Présence et main, selon la série", "",
             "| Méthode | Série | Photos | Présence (min – moyenne – max) | "
             f"Cadre de main rendu (présence ≥ {vision.PRESENCE_MIN:.0%} "
             f"ou épaules ≥ {vision.SHOULDERS_CONFIDENCE_MIN:.0%}) |",
             "|---|---|---|---|---|"]

    def spread(values):
        values = [v for v in values if v is not None]
        if not values:
            return "–"
        return f"{min(values):.0%} – {statistics.mean(values):.0%} – {max(values):.0%}"

    for name in methods:
        for label in ("avec", "sans", "vide"):
            group = [r[name] for r in labelled if photo_label(r["photo"]) == label and "erreur" not in r[name]]
            if group:
                hands = sum(1 for m in group if m.get("main"))
                lines.append(
                    f"| {name} | {label} | {len(group)} | {spread(m.get('presence') for m in group)} | "
                    f"{hands} / {len(group)} |"
                )
    return lines


def mean_of(rows: list[dict], name: str, key: str) -> str:
    """Moyenne d'un écart, avec le nombre de photos où il est connu."""
    values = [r[name][key] for r in rows if r[name].get(key) is not None]
    return f"{statistics.mean(values):.3f} ({len(values)} photos)" if values else "–"


def angle_table(rows: list[dict], methods: list[str]) -> list[str]:
    """Direction épaules → main : c'est elle qui désigne la cible."""
    lines = []
    for name in methods:
        gaps = [r[name]["erreur_angle"] for r in rows if r[name].get("erreur_angle") is not None]
        missing = sum(1 for r in rows if r[name].get("sans_direction"))
        if gaps:
            lines.append(
                f"| {name} | {len(gaps)} | {statistics.mean(gaps):.0f}° | {statistics.median(gaps):.0f}° | "
                f"{max(gaps):.0f}° | {missing} |"
            )
        elif missing:
            lines.append(f"| {name} | 0 | – | – | – | {missing} |")
    if not lines:
        return []
    return ["", "## Direction du bras", "",
            "Écart d'angle entre la direction épaules → main trouvée et la vraie, vue d'en haut. Sans direction : "
            "main introuvable ou trop près des épaules alors que le vrai bras en donne une (aim.py refuserait la visée).",
            "", "| Méthode | Photos | Écart moyen | Écart médian | Pire | Sans direction |",
            "|---|---|---|---|---|---|", *lines]


def truth_text(truth: dict) -> str:
    points = [(label, truth.get(key)) for label, key in (("épaules", "epaules"), ("main", "main"))]
    return ", ".join(f"{label} {p[0]:.2f}, {p[1]:.2f}" for label, p in points if p) or "?"


GAPS = (("épaules", "erreur_position", ".2f", ""), ("main", "erreur_main", ".2f", ""), ("bras", "erreur_angle", ".0f", "°"))


def summary(rows: list[dict], methods: list[str]) -> str:
    lines = ["# Comparatif de la vision", "",
             "Erreur = distance à la vraie position (0 à 1 : la largeur de la photo vaut 1). "
             "Sur les images : épaules en rond, main en carré.", ""]
    lines += ["| Méthode | Épaules : erreur moyenne | Médiane | Pire | Main : erreur moyenne | Temps moyen | Échecs |",
              "|---|---|---|---|---|---|---|"]
    for name in methods:
        errors = [r[name]["erreur_position"] for r in rows if r[name].get("erreur_position") is not None]
        times = [r[name]["ms"] for r in rows if "ms" in r[name]]
        failures = sum(1 for r in rows if "erreur" in r[name])
        spread = f"{statistics.median(errors):.3f} | {max(errors):.3f}" if errors else "– | –"
        lines.append(
            f"| {name} | {mean_of(rows, name, 'erreur_position')} | {spread} | {mean_of(rows, name, 'erreur_main')} | "
            f"{statistics.mean(times) if times else 0:.0f} ms | {failures} |"
        )
    lines += angle_table(rows, methods)
    lines += label_table(rows, methods)
    lines += ["", "## Détail par photo", "", "| Photo | Vérité | " + " | ".join(methods) + " |",
              "|---|---|" + "---|" * len(methods)]
    for r in rows:
        cells = []
        for name in methods:
            m = r[name]
            if "erreur" in m:
                cells.append(f"échec : {m['erreur'][:60]}")
            else:
                conf = f"{m['confiance']:.0%}" if m.get("confiance") is not None else "?"
                gaps = [f"écart {label} {m[key]:{fmt}}{unit}" for label, key, fmt, unit in GAPS if m.get(key) is not None]
                if m.get("sans_direction"):
                    gaps.append("bras sans direction")
                cells.append(", ".join([*gaps, m["detail"]]) + f", confiance {conf}, {m['ms']} ms")
        lines.append(f"| {r['photo']} | {truth_text(r['vérité'])} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_aids(photos: list[Path]) -> None:
    """Copies quadrillées tous les 0,05 pour lire la vraie position à l'œil."""
    out = RESULTS / "aide"
    out.mkdir(parents=True, exist_ok=True)
    for path in photos:
        image = Image.open(path).convert("RGBA")
        width, height = image.size
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        for i in range(1, 20):
            strong = i % 2 == 0
            color = (0, 255, 255, 160 if strong else 70)
            draw.line([(i * width / 20, 0), (i * width / 20, height)], fill=color, width=1)
            draw.line([(0, i * height / 20), (width, i * height / 20)], fill=color, width=1)
            if strong:
                label(draw, (i * width / 20, 10), f"{i / 20:.1f}", 12, (0, 255, 255, 255))
                label(draw, (14, i * height / 20), f"{i / 20:.1f}", 12, (0, 255, 255, 255))
        Image.alpha_composite(image, layer).convert("RGB").save(out / path.name, quality=85)
    print(f"{len(photos)} copies dans {out}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aide", action="store_true", help="copies quadrillées pour noter les vraies positions")
    parser.add_argument("--methodes", default=",".join(METHODS))
    parser.add_argument("--photos", default="*.jpg")
    args = parser.parse_args()
    names = args.methodes.split(",")
    if unknown := [name for name in names if name not in METHODS]:
        parser.error(f"méthode inconnue : {', '.join(unknown)} (connues : {', '.join(METHODS)})")

    photos = sorted(PHOTOS.glob(args.photos))
    if not photos:
        sys.exit(f"Aucune photo dans {PHOTOS} : lance d'abord bench/capture.py")
    if args.aide:
        write_aids(photos)
        return

    keys = load_keys()
    methods = []
    for name in names:
        if keys.get(NEEDS[name]):
            methods.append(name)
        else:
            print(f"{name} ignorée : pas de {NEEDS[name]} dans bench/.env")
    truth = json.loads(TRUTH.read_text(encoding="utf-8")) if TRUTH.exists() else {}

    (RESULTS / "images").mkdir(parents=True, exist_ok=True)
    rows = []
    async with aiohttp.ClientSession() as session:
        for path in photos:
            raw = path.read_bytes()
            results = dict(zip(methods, await asyncio.gather(
                *(run_method(name, session, keys, raw) for name in methods)
            )))
            real = truth.get(path.name, {})
            ratio = vision.photo_ratio(raw)
            for result in results.values():
                measure(result, real, ratio)
            draw_points(raw, real, results, RESULTS / "images" / path.name)
            rows.append({"photo": path.name, "vérité": real, **results})
            print(path.name, {n: r.get("erreur") or [r[key] for _, key, _, _ in GAPS] for n, r in results.items()})

    (RESULTS / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (RESULTS / "summary.md").write_text(summary(rows, methods), encoding="utf-8")
    print(f"Résumé : {RESULTS / 'summary.md'}")


if __name__ == "__main__":
    asyncio.run(main())
