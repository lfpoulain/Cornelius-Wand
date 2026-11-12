"""Prend une série de photos avec la caméra au plafond, pour le banc d'essai de la vision.

    python bench/capture.py --count 12 --every 4 --label avec

Entre deux photos, changer de position dans la pièce, puis rester immobile quand le
compte à rebours arrive à zéro. --label dit ce que montre la série : « avec » (baguette en main),
« sans » (main tendue, sans baguette) ou « vide » (personne dans la pièce). Il finit le
nom des photos, rangées dans bench/photos/.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import time
import urllib.request

PHOTOS = Path(__file__).parent / "photos"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", default="http://cornelius-camera.local")
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--every", type=float, default=4.0, help="secondes entre deux photos")
    parser.add_argument("--label", choices=("avec", "sans", "vide"), help="baguette en main, main vide, pièce vide")
    args = parser.parse_args()

    PHOTOS.mkdir(exist_ok=True)
    for i in range(1, args.count + 1):
        for left in range(int(args.every), 0, -1):
            print(f"Photo {i}/{args.count} dans {left} s…", end="\r", flush=True)
            time.sleep(1)
        with urllib.request.urlopen(f"{args.camera.rstrip('/')}/capture.jpg", timeout=5) as response:
            photo = response.read()
        suffix = f"-{args.label}" if args.label else ""
        name = PHOTOS / f"{datetime.now():%Y%m%d-%H%M%S}{suffix}.jpg"
        name.write_bytes(photo)
        print(f"Photo {i}/{args.count} : {name.name} ({len(photo) // 1024} Ko)        ")


if __name__ == "__main__":
    main()
