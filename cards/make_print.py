#!/usr/bin/env python3
"""Build the print folder from out/deck: single cards, one 2-card plate per filament colour, and the zip.

Run after generate_deck.py: .venv/bin/python make_print.py
Plates: two cards side by side, 12 mm apart (138 x 113 mm), same print orientation as the single cards.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import trimesh

from generate_deck import COLORS, OUT as DECK_OUT, export_3mf, slug

OUT = Path(__file__).parent / "out"
PRINT = OUT / "print"
PLATES = [  # (plate name, left card, right card)
    ("plate_red_tower_ace_of_wands", 16, 101),
    ("plate_blue_star_ace_of_cups", 17, 102),
    ("plate_orange_sun_judgement", 19, 20),
    ("plate_black_moon_world", 18, 21),
]
OFFSET = 37.5  # card centre to plate centre: 63 mm cards, 12 mm gap


def main():
    PRINT.mkdir(parents=True, exist_ok=True)
    for cid in COLORS:
        shutil.copy(DECK_OUT / f"{slug(cid)}.stl", PRINT / f"{slug(cid)}.stl")
    for name, left, right in PLATES:
        parts = []
        for cid, dx in ((left, -OFFSET), (right, OFFSET)):
            m = trimesh.load(DECK_OUT / f"{slug(cid)}.stl")
            m.apply_translation((dx, 0, 0))
            parts.append(m)
        plate = trimesh.util.concatenate(parts)
        plate.export(PRINT / f"{name}.stl")
        export_3mf(plate, PRINT / f"{name}.3mf")
        print(f"wrote {name}  {plate.extents.round(1).tolist()} mm")
    zpath = OUT / "fortune_deck_print.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(PRINT, "print/")
        for f in sorted(PRINT.iterdir()):
            z.write(f, f"print/{f.name}")
    print(f"wrote {zpath}")


if __name__ == "__main__":
    main()
