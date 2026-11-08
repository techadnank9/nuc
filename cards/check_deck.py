#!/usr/bin/env python3
"""Checks for the deck made by generate_deck.py. Run: .venv/bin/python check_deck.py

For every card in out/deck: one watertight solid, flat on the bed, expected size, grip faces 14 mm apart,
grip faces with enough flat contact, face engraved. Across cards: the backs are identical.
"""
import sys
from pathlib import Path

import manifold3d as m
import numpy as np
import trimesh

HERE = Path(__file__).resolve().parent
DECK_DIR = HERE / "out" / "deck"
V2 = HERE / "out" / "card_v2.stl"
PLA, INFILL = 1.24, 0.2  # g/cm3, rough mass estimate as in check_v2.py

failures = []


def check(ok, msg):
    print(("  PASS  " if ok else "  FAIL  ") + msg)
    if not ok:
        failures.append(msg)


def manifold(mesh):
    return m.Manifold(m.Mesh(np.asarray(mesh.vertices, np.float32), np.asarray(mesh.faces, np.uint32)))


def region_volume(mesh, lo, hi):
    """Volume of the mesh inside an axis-aligned box."""
    return (manifold(mesh) ^ m.Manifold.cube(np.subtract(hi, lo)).translate(lo)).volume()


HANDLE_BOX = ((-16, 43.9, -1), (16, 58, 15))        # the grip block below any top styling (v2 chamfers start at 58)
BACK_BOX = ((-40, -50, 0.9), (40, 95, 20))          # everything above the face engraving
FACE_BOX = ((-40, -50, -1), (40, 44, 0.8))          # the engraved layer of the body

cards = sorted(DECK_DIR.glob("card_*.stl"))
if not cards:
    sys.exit("no cards in out/deck: run generate_deck.py first")

v2_handle = region_volume(trimesh.load(V2), *HANDLE_BOX) if V2.exists() else None
backs, faces = {}, {}
for path in cards:
    print(path.name)
    mesh = trimesh.load(path)
    lo, hi = mesh.bounds
    size = hi - lo
    check(mesh.is_watertight and mesh.is_volume, "watertight, a valid volume")
    check(len(manifold(mesh).decompose()) == 1, "one solid (no loose letters or islands)")
    check(abs(lo[2]) < 1e-3, f"flat on the bed: lowest Z = {lo[2]:.3f}")
    check(np.allclose(size[[0, 2]], [63, 14], atol=0.05) and 105.9 <= size[1] <= 121.1,
          f"size {size[0]:.1f} x {size[1]:.1f} x {size[2]:.1f} mm (63 x 106 to 121 x 14 expected)")
    # Grip faces: the handle's extreme Z at mid-handle height, away from the ribs' channels.
    slab = manifold(mesh) ^ m.Manifold.cube((40, 0.2, 30)).translate((-20, 52.0, -5))
    bb = slab.bounding_box()  # (xmin, ymin, zmin, xmax, ymax, zmax)
    check(abs((bb[5] - bb[2]) - 14.0) < 0.05, f"grip faces {bb[5] - bb[2]:.2f} mm apart (14 proven)")
    # Flat contact left on each grip face (the block, Y 44 to 62): faces lying in Z = 0 and Z = 14.
    tri = mesh.triangles; cen = tri.mean(axis=1); area = mesh.area_faces
    in_block = (cen[:, 1] > 44) & (cen[:, 1] < 62) & (np.abs(cen[:, 0]) < 15)
    for z, label in ((0.0, "face side"), (14.0, "back side")):
        flat = area[in_block & np.all(np.abs(tri[:, :, 2] - z) < 1e-3, axis=1)].sum()
        check(flat >= 0.6 * 30 * 18, f"grip face ({label}): {flat:.0f} mm2 flat contact of {30 * 18} (60% minimum)")
    backs[path.name] = region_volume(mesh, *BACK_BOX)
    faces[path.name] = region_volume(mesh, *FACE_BOX)
    full_face = 63 * 88 * 0.8 - (4 - np.pi) * 3 ** 2 * 0.8  # rounded rectangle, 0.8 mm slab
    check(faces[path.name] < full_face - 100, f"face engraved: {full_face - faces[path.name]:.0f} mm3 removed")
    check(size[0] <= 250 and size[1] <= 220 and size[2] <= 220, "fits a 250 x 220 x 220 mm bed")
    print(f"        mass about {mesh.volume / 1000 * PLA * INFILL + 0:.1f} g at 20% infill (walls add more)")

print("deck")
vals = np.array(list(backs.values()))
check(np.ptp(vals) < 0.05, f"backs identical on all {len(vals)} cards (spread {np.ptp(vals):.3f} mm3)")
print(f"\n{len(failures)} failure(s)" if failures else "\nall checks pass")
sys.exit(1 if failures else 0)
