#!/usr/bin/env python3
"""The Robot Fortune Teller deck (v3), in millimetres. Run: .venv/bin/python generate_deck.py [--only 17,18]

Every card shares the proven v2 body and grip handle (base Dream-1 picked it up on Nov 7).
- FACE (printed down on the bed, Z = 0): engraved 0.6 mm, unique per card: numeral, a simple symbol, the name.
  Swap filament at 0.6 mm for a contrasting colour inside the engraving.
- BACK (facing up, Z = 4): identical on every card, raised 0.6 mm: "ROBOT / FORTUNE TELLER", a sun-and-moon
  emblem and a border. The handle stands 10 mm proud of this side.
- No tag sticker by default (TAG_POCKET = False), so the backs are truly identical.

Coordinates: X across the card (63 mm), Y up the card (88 mm body, handle above), Z through it (print height).
The face is seen from below (-Z), so its artwork is mirrored in X here and reads correctly on the printed card.
"""
from __future__ import annotations

import argparse
import os
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "fortune-card-mpl"))
import matplotlib

matplotlib.use("Agg")
import manifold3d as m
import matplotlib.pyplot as plt
import numpy as np
import trimesh
from matplotlib.font_manager import FontProperties
from matplotlib.textpath import TextPath
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box as sbox
from shapely.ops import unary_union

OUT = Path(__file__).resolve().parent / "out" / "deck"

# The deck: tag id (= tarot number) -> name, numeral, upright meaning (for the agent's cards.py).
DECK = {
    16: ("THE TOWER", "XVI", "sudden upheaval, broken pride, disaster"),
    17: ("THE STAR", "XVII", "hope, faith, rejuvenation"),
    18: ("THE MOON", "XVIII", "the unconscious, illusions, intuition"),
    19: ("THE SUN", "XIX", "joy, success, celebration, positivity"),
    20: ("JUDGEMENT", "XX", "reflection, reckoning, awakening"),
    21: ("THE WORLD", "XXI", "completion, integration, accomplishment"),
    # Minor arcana aces (ids above the majors' 0 to 21; the face says ACE instead of a numeral)
    101: ("ACE OF WANDS", "ACE", "inspiration, new opportunities, growth, potential"),
    102: ("ACE OF CUPS", "ACE", "love, new feelings, compassion, an open heart"),
}

# The printed deck: one filament colour per card (the whole card), and the colour of the first 0.6 mm (the face
# skin, swapped after 3 layers) so the engraved art shows the card's colour on a contrasting face.
# Cards without a colour stay in the generator but are not built by default.
FILAMENT = {"red": "#c62828", "blue": "#1f5fbf", "orange": "#ef7d18", "black": "#222222"}
COLORS = {  # card id -> (card colour, face-skin colour); two cards per colour
    16: ("red", "black"),      # The Tower: fire, upheaval
    101: ("red", "black"),     # Ace of Wands: the fire suit
    17: ("blue", "black"),     # The Star: night sky, calm hope
    102: ("blue", "black"),    # Ace of Cups: the water suit
    19: ("orange", "black"),   # The Sun: warmth, joy
    20: ("orange", "black"),   # Judgement: the dawn call, awakening
    18: ("black", "orange"),   # The Moon: night, mystery
    21: ("black", "orange"),   # The World: the cosmos
}

# Proven geometry (do not change without testing on the arm): same as card v2.
BODY_W, BODY_H, BODY_T, CORNER_R = 63.0, 88.0, 4.0, 3.0
HANDLE = dict(width=30.0, height=18.0, depth=14.0, y0=44.0)  # grip faces at Z = 0 and Z = 14
# The top of the handle above the proven 30 x 18 mm grip block: "dome" (semicircle, +15 mm), "low_dome"
# (gentle arch, +7 mm) or "block" (card v2's flat top with lead-in chamfers). The grip block is the same in all.
TOP_STYLE = os.environ.get("TOP_STYLE", "low_dome")
DOME_RISE = {"dome": 15.0, "low_dome": 7.0}
DOME_CHAMFER = 2.5  # 45-degree chamfer around the dome's edge on both faces: a finger landing on top slides off
# A crystal ball engraved 0.6 mm on both faces of the handle, covering the dome, the same on every card
# (CRYSTAL_BALL=0 for a plain handle). GRIP_RIBS=1 restores v2's friction ribs (then the ball must stay small).
CRYSTAL_BALL = os.environ.get("CRYSTAL_BALL", "1") == "1" and TOP_STYLE == "low_dome"
GRIP_RIBS = os.environ.get("GRIP_RIBS", "0") == "1"
BALL_DEPTH = 0.6

# The arm flips the card end over end to show it, so the handle ends up at the bottom. The face art is
# turned 180 degrees so it reads upright for the guest after that flip (it looks upside down handle-up).
FACE_ROTATED = True
FACE_DEPTH = 0.6   # engraving into the face (3 layers at 0.2 mm): swap filament at 0.6 mm to colour it
BACK_RAISE = 0.6   # raised relief on the back (3 layers)
LINE = 1.4         # minimum stroke for a 0.4 mm nozzle
TAG_POCKET = False  # True: 27 x 27 x 0.6 mm pocket on the back for an ArUco sticker (harness card reading)
TAG_SIZE, TAG_CENTER_Y = 27.0, -24.0
FONT = FontProperties(family="DejaVu Sans", weight="bold")


# ---------- solids ----------

def box(size, origin):
    return m.Manifold.cube(size).translate(origin)


def prism(points, height, z=0.0):
    points = np.array(points, dtype=float)
    following = np.roll(points, -1, axis=0)
    if np.sum(points[:, 0] * following[:, 1] - following[:, 0] * points[:, 1]) < 0:
        points = points[::-1]
    return m.Manifold.extrude(m.CrossSection([points]), height).translate((0, 0, z))


def yz_prism(points, x0, width):
    return prism(points, width).transform(np.array([[0, 0, 1, x0], [1, 0, 0, 0], [0, 1, 0, 0]], dtype=float))


def union(solids):
    return m.Manifold.batch_boolean(solids, m.OpType.Add)


def subtract(solid, cutters):
    return m.Manifold.batch_boolean([solid, *cutters], m.OpType.Subtract)


def extrude_2d(geom, z0, height):
    """Extrude a shapely (Multi)Polygon with holes between z0 and z0 + height."""
    polys = [geom] if isinstance(geom, Polygon) else list(getattr(geom, "geoms", []))
    contours = []
    for p in polys:
        if p.is_empty:
            continue
        contours.append(np.array(p.exterior.coords[:-1]))
        contours += [np.array(r.coords[:-1]) for r in p.interiors]
    section = m.CrossSection(contours, m.FillRule.EvenOdd)
    return m.Manifold.extrude(section, height).translate((0, 0, z0))


def rounded_rect(w, h, r, z, depth):
    return extrude_2d(sbox(-w / 2 + r, -h / 2 + r, w / 2 - r, h / 2 - r).buffer(r, 32), z, depth)


def tab_profile():
    """2D outline of the handle seen from the front: the grip block plus its top."""
    block = sbox(-15, 44, 15, 62)
    if TOP_STYLE == "block":
        return block
    h = DOME_RISE[TOP_STYLE]
    r = (15 ** 2 + h ** 2) / (2 * h)            # circle through both top corners and the apex
    cap = Point(0, 62 + h - r).buffer(r, 128).intersection(sbox(-15, 62, 15, 62 + h))
    return unary_union([block, cap])


def crystal_ball_icon():
    """A 17 mm crystal ball drawn as an outline, filling the dome and the top of the handle: a ring, a highlight
    arc and a small stand. An outline keeps most of each grip face flat for the fingers.
    Its top stops where the dome's chamfer starts, so it stays on the flat face."""
    cy, r = 57.8, 8.5
    ring_ = ring(r - 0.8, 1.6, (0, cy))
    highlight = ring(r - 3.6, 1.4, (0, cy)).intersection(
        Polygon([(0, cy), (-12, cy + 1), (-12, cy + 12), (-1, cy + 12)]))
    sparkle = star(4, 1.8, 0.6, (2.6, cy - 2.4), 0)
    stand = Polygon([(-8.5, 45.2), (8.5, 45.2), (5.6, 48.6), (-5.6, 48.6)])
    return unary_union([ring_, highlight, sparkle, stand])


def dome():
    """The top above the grip block: convex, flat faces continuing up from the grip faces, chamfered edges."""
    h = DOME_RISE[TOP_STYLE]
    r = (15 ** 2 + h ** 2) / (2 * h)
    cap = Point(0, 62 + h - r).buffer(r, 128).intersection(sbox(-15, 58, 15, 62 + h))
    inset = cap.buffer(-DOME_CHAMFER, 64).union(sbox(-15, 58, 15, 60))  # no chamfer where it meets the block
    c = DOME_CHAMFER
    layers = [extrude_2d(inset, 0, 0.01), extrude_2d(cap, c, 14 - 2 * c), extrude_2d(inset, 14 - 0.01, 0.01)]
    return union(layers).hull()


def body_and_handle():
    """Card v2's body and grip block (without v2's front sticker pocket), with the chosen top."""
    body = rounded_rect(BODY_W, BODY_H, CORNER_R, 0, BODY_T)
    # Bevel on the back's bottom edge: helps the card drop into the tray and the stand.
    body -= yz_prism([(-45, 2.2), (-42, 5.2), (-45, 5.2)], -33, 66)
    handle = box((30, 18, 14), (-15, 44, 0))
    cutters = []
    if TOP_STYLE == "block":
        cutters += [
            yz_prism([(58, 0), (63, 5), (63, -1), (58, -1)], -16, 32),  # 4 mm lead-in chamfers, both grip faces
            yz_prism([(58, 14), (63, 9), (63, 15), (58, 15)], -16, 32),
            prism([(15, 60), (12, 63), (16, 63), (16, 60)], 16, -1),      # 2 mm side chamfers
            prism([(-15, 60), (-12, 63), (-16, 63), (-16, 60)], 16, -1),
        ]
    else:
        handle = handle + dome()
    for y in (np.arange(47.2, 58, 1.6) if GRIP_RIBS or TOP_STYLE == "block" else []):  # v2's 0.4 mm ribs
        cutters += [yz_prism([(y - .5, -.1), (y, .4), (y + .5, -.1)], -14, 28),
                    yz_prism([(y - .5, 14.1), (y, 13.6), (y + .5, 14.1)], -14, 28)]
    return body + subtract(handle, cutters)


# ---------- 2D artwork (card coordinates as seen on that side: X right, Y up) ----------

def text(s, height, center, max_width=None):
    """Bold text as shapely geometry, cap height ~= height, centred on `center`."""
    path = TextPath((0, 0), s, size=1.0, prop=FONT)
    geom = Polygon()
    for ring in path.to_polygons():
        if len(ring) >= 3:
            geom = geom.symmetric_difference(Polygon(ring).buffer(0))
    x0, y0, x1, y1 = geom.bounds
    scale = height / (y1 - y0)
    if max_width and (x1 - x0) * scale > max_width:
        scale = max_width / (x1 - x0)
    geom = affinity.scale(geom, scale, scale, origin=(0, 0))
    x0, y0, x1, y1 = geom.bounds
    return affinity.translate(geom, center[0] - (x0 + x1) / 2, center[1] - (y0 + y1) / 2)


def ring(r, width, center=(0, 0)):
    return Point(center).buffer(r + width / 2, 64).difference(Point(center).buffer(r - width / 2, 64))


def star(n, r_out, r_in, center=(0, 0), rot=90.0):
    pts = []
    for i in range(2 * n):
        a = np.deg2rad(rot + i * 180.0 / n)
        r = r_out if i % 2 == 0 else r_in
        pts.append((center[0] + r * np.cos(a), center[1] + r * np.sin(a)))
    return Polygon(pts)


def stroke(points, width=LINE):
    return LineString(points).buffer(width / 2, cap_style=2, join_style=2)


def symbol(card_id):
    """One simple symbol per card, about 34 mm, centred at the origin."""
    if card_id == 16:  # The Tower: a crenellated tower struck by lightning
        tower = sbox(-6, -16, 6, 8)
        for x in (-6, -1.5, 3):
            tower = tower.union(sbox(x, 8, x + 3, 11.5))
        tower = tower.difference(sbox(-1.6, -16, 1.6, -10).union(Point(0, -10).buffer(1.6, 32)))  # door
        tower = tower.difference(sbox(-1.2, 1, 1.2, 5))                                           # window
        bolt = stroke([(16, 18), (8.5, 9.5), (13, 8.5), (6.5, 0)], 2.2)
        return tower.union(bolt)
    if card_id == 17:  # The Star: a big eight-pointed star and two small ones
        return unary_union([star(8, 13, 5.5, (0, 1)), star(4, 4, 1.4, (-12, -12), 0), star(4, 4, 1.4, (12, -12), 0)])
    if card_id == 18:  # The Moon: a crescent
        return Point(0, 0).buffer(15, 96).difference(Point(6.5, 3).buffer(12.5, 96))
    if card_id == 19:  # The Sun: a disc with twelve rays
        rays = []
        for i in range(12):
            a = np.deg2rad(i * 30)
            tip, l, r = 16.5, np.deg2rad(i * 30 - 7), np.deg2rad(i * 30 + 7)
            rays.append(Polygon([(10 * np.cos(l), 10 * np.sin(l)), (tip * np.cos(a), tip * np.sin(a)),
                                 (10 * np.cos(r), 10 * np.sin(r))]))
        return unary_union([Point(0, 0).buffer(8, 64), *rays])
    if card_id == 20:  # Judgement: a trumpet sounding
        tube = stroke([(-15, -13), (3, 5)], 2.4)
        bell = Polygon([(1, 7.5), (5.5, 3), (12, 15), (13, 14)]).convex_hull
        mouth = stroke([(-16, -12), (-14, -14)], 3.2)
        waves = unary_union([ring(r, LINE, (8, 10)).intersection(
            Polygon([(8, 10), (30, 2), (24, 30), (0, 30)])) for r in (9, 13)])
        return unary_union([tube, bell, mouth, waves])
    if card_id == 21:  # The World: a laurel wreath (an oval ring) around a small star
        oval = affinity.scale(ring(13, 2.2), 0.75, 1.1)
        leaves = [affinity.rotate(affinity.scale(Point(0, 0).buffer(1.8, 24), 1.0, 2.0), ang,
                                  origin=(0, 0)).buffer(0) for ang in (0,)]
        tufts = unary_union([affinity.translate(leaves[0], x, y) for x, y in ((-9.8, 0), (9.8, 0))])
        return unary_union([oval, tufts.difference(sbox(-7, -20, 7, 20)), star(4, 4.5, 1.6, (0, 0), 0)])
    if card_id == 101:  # Ace of Wands: a staff sprouting leaves, a spark at the tip
        staff = stroke([(-9, -15), (8, 13)], 3.0)
        knob = Point(-9.3, -15.4).buffer(2.3, 32)
        leaf = affinity.scale(Point(0, 0).buffer(1.6, 32), 1.0, 2.4)
        leaves = []
        for t, side in ((0.35, 1), (0.55, -1), (0.75, 1)):
            x, y = -9 + 17 * t, -15 + 28 * t
            leaves.append(affinity.translate(affinity.rotate(leaf, -30 + side * 55, origin=(0, 0)),
                                             x + side * 3.0, y + 0.8))
        return unary_union([staff, knob, *leaves, star(4, 4.0, 1.3, (11.5, 17.0), 0)])
    if card_id == 102:  # Ace of Cups: a chalice with three drops falling into it
        bowl = Point(0, 4).buffer(10, 96).intersection(sbox(-10, -6, 10, 4)).union(sbox(-11, 3, 11, 5.5))
        stem = sbox(-1.6, -12, 1.6, -5)
        knot = affinity.scale(Point(0, -8.5).buffer(2.6, 32), 1.0, 0.7)
        base = Polygon([(-7.5, -16), (7.5, -16), (4, -12), (-4, -12)])
        drops = [Point(x, y).buffer(1.3, 24).union(Polygon([(x - 1.25, y + 0.3), (x + 1.25, y + 0.3), (x, y + 3.2)]))
                 for x, y in ((-5, 9.5), (0, 11.5), (5, 9.5))]
        return unary_union([bowl, stem, knot, base, *drops])
    raise ValueError(card_id)


def face_art(card_id):
    """Everything engraved on the face, as seen looking at the face."""
    name, numeral, _ = DECK[card_id]
    frame = sbox(-27.5, -40, 27.5, 40).buffer(1.5).difference(sbox(-26.1, -38.6, 26.1, 38.6).buffer(1.5))
    return unary_union([
        frame,
        text(numeral, 7.0, (0, 31.5), max_width=40),
        affinity.translate(symbol(card_id), 0, 2),
        text(name, 6.0, (0, -31.5), max_width=46),
    ])


def back_art():
    """Identical on every card, as seen looking at the back (handle side)."""
    frame = sbox(-27.5, -40, 27.5, 40).buffer(1.5).difference(sbox(-26.1, -38.6, 26.1, 38.6).buffer(1.5))
    title = [text("ROBOT", 5.0, (0, 33.5), max_width=44), text("FORTUNE TELLER", 4.2, (0, 25.5), max_width=46)]
    if TAG_POCKET:  # small emblem above the sticker pocket
        cy, r, sun, moon_x, moon_r = 7.0, 9.0, 4.0, 14.5, 3.4
    else:           # the emblem fills the middle of the card
        cy, r, sun, moon_x, moon_r = -3.0, 12.5, 6.0, 20.0, 4.2

    def moon(x, mirror):
        c = Point(x, cy).buffer(moon_r, 48).difference(Point(x + 0.38 * moon_r, cy + 0.3 * moon_r).buffer(0.82 * moon_r, 48))
        return affinity.scale(c, -1, 1, origin=(x, cy)) if mirror else c

    emblem = [ring(r, LINE, (0, cy)), Point(0, cy).buffer(sun, 48), moon(-moon_x, False), moon(moon_x, True)]
    if TAG_POCKET:
        extra = [sbox(-TAG_SIZE / 2 - 2, TAG_CENTER_Y - TAG_SIZE / 2 - 2,
                      TAG_SIZE / 2 + 2, TAG_CENTER_Y + TAG_SIZE / 2 + 2).exterior.buffer(0.7)]
    else:           # a row of three small stars near the bottom
        extra = [star(4, 3.2 if x else 4.2, 1.2 if x else 1.5, (x, -29), 0) for x in (-10, 0, 10)]
    return unary_union([frame, *title, *emblem, *extra])


def tag_pocket():
    return sbox(-TAG_SIZE / 2, TAG_CENTER_Y - TAG_SIZE / 2, TAG_SIZE / 2, TAG_CENTER_Y + TAG_SIZE / 2)


# ---------- build ----------

def build_back():
    """The shared part: body, handle, raised back art, tag pocket. Identical for every card."""
    solid = body_and_handle() + extrude_2d(back_art(), BODY_T - 0.01, BACK_RAISE + 0.01)
    if TAG_POCKET:
        solid -= extrude_2d(tag_pocket(), BODY_T - 0.6, 1.0)
    if CRYSTAL_BALL:  # on the handle's two faces: Z = 14 (back, reads from +Z) and Z = 0 (face, mirrored)
        icon = crystal_ball_icon()
        solid -= extrude_2d(icon, 14 - BALL_DEPTH, 1.0)
        solid -= extrude_2d(affinity.scale(icon, -1, 1, origin=(0, 0)), -0.1, BALL_DEPTH + 0.1)
    return solid


def face_layout(card_id):
    """The face art as it sits on the card with the handle up: turned 180 degrees when FACE_ROTATED."""
    art = face_art(card_id)
    return affinity.rotate(art, 180, origin=(0, 0)) if FACE_ROTATED else art


def build_card(card_id, back=None):
    back = back if back is not None else build_back()
    engraving = affinity.scale(face_layout(card_id), -1, 1, origin=(0, 0))  # seen from -Z: mirror X
    return back - extrude_2d(engraving, -0.1, FACE_DEPTH + 0.1)


def to_mesh(solid):
    mesh = solid.to_mesh()
    return trimesh.Trimesh(mesh.vert_properties[:, :3], mesh.tri_verts, process=True)


def export_3mf(mesh, path):
    ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    model = ET.Element("model", unit="millimeter", xmlns=ns)
    res = ET.SubElement(model, "resources")
    obj = ET.SubElement(res, "object", id="1", type="model")
    msh = ET.SubElement(obj, "mesh")
    vs = ET.SubElement(msh, "vertices")
    for x, y, z in mesh.vertices:
        ET.SubElement(vs, "vertex", x=f"{x:.4f}", y=f"{y:.4f}", z=f"{z:.4f}")
    ts = ET.SubElement(msh, "triangles")
    for a, b, c in mesh.faces:
        ET.SubElement(ts, "triangle", v1=str(a), v2=str(b), v3=str(c))
    build = ET.SubElement(model, "build")
    ET.SubElement(build, "item", objectid="1")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Default Extension="rels" ContentType="application/'
                   'vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType='
                   '"application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type='
                   '"http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        z.writestr("3D/3dmodel.model", ET.tostring(model, xml_declaration=True, encoding="UTF-8"))


def slug(card_id):
    return f"card_{card_id}_" + DECK[card_id][0].lower().replace("the ", "").replace(" ", "_")


# ---------- previews (drawn from the same 2D artwork the solids are cut from) ----------

def draw(ax, geom, **kw):
    """Fill polygons with their holes left open (exterior CCW + holes CW, non-zero rule)."""
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as MPath
    from shapely.geometry.polygon import orient
    for p in ([geom] if isinstance(geom, Polygon) else getattr(geom, "geoms", [])):
        p = orient(p, 1.0)
        verts, codes = [], []
        for r in [p.exterior, *p.interiors]:
            pts = list(r.coords)
            verts += pts
            codes += [MPath.MOVETO] + [MPath.LINETO] * (len(pts) - 2) + [MPath.CLOSEPOLY]
        ax.add_patch(PathPatch(MPath(verts, codes), lw=0, **kw))


def card_outline():
    return unary_union([sbox(-31.5 + 3, -44 + 3, 31.5 - 3, 44 - 3).buffer(3, 32), tab_profile()])


def preview(ids):
    """Each card's face as printed (face-skin colour, art in the card colour), then one back per colour."""
    def card_ax(ax, art, base, ink, title):
        ax.set_aspect("equal"); ax.axis("off"); ax.set_xlim(-34, 34); ax.set_ylim(-47, 74)
        draw(ax, card_outline(), color=base)
        if ink:
            draw(ax, art, color=ink)
        else:  # the raised back art is the same filament: shown lighter, like light catching the relief
            draw(ax, art, color="white", alpha=0.28)
        if CRYSTAL_BALL:
            draw(ax, crystal_ball_icon(), color=ink or "white", alpha=1.0 if ink else 0.28)
        ax.set_title(title, fontsize=9)

    backs = list(dict.fromkeys(COLORS[i][0] for i in ids if i in COLORS))
    cols = 4
    rows = -(-len(ids) // cols) + -(-len(backs) // cols)
    fig, axes = plt.subplots(rows, cols, figsize=(2.3 * cols, 3.5 * rows), squeeze=False)
    for ax in axes.flat:
        ax.axis("off")
    for k, cid in enumerate(ids):
        card_c, skin_c = (FILAMENT[c] for c in COLORS.get(cid, ("black", "orange")))
        card_ax(axes.flat[k], face_layout(cid), skin_c, card_c, f"{DECK[cid][0].title()} ({COLORS.get(cid, ('-',))[0]})")
    start = -(-len(ids) // cols) * cols
    for k, colour in enumerate(backs):
        card_ax(axes.flat[start + k], back_art(), FILAMENT[colour], None, f"back, {colour}")
    fig.savefig(OUT / "preview_deck.png", dpi=150, bbox_inches="tight", facecolor="#fcfaf5")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated card ids, e.g. 16,17,18,20 (default: the coloured deck)")
    a = ap.parse_args()
    ids = [int(x) for x in a.only.split(",")] if a.only else [i for i in DECK if i in COLORS]
    OUT.mkdir(parents=True, exist_ok=True)
    back = build_back()
    for cid in ids:
        mesh = to_mesh(build_card(cid, back))
        mesh.export(OUT / f"{slug(cid)}.stl")
        export_3mf(mesh, OUT / f"{slug(cid)}.3mf")
        card_c, skin_c = COLORS.get(cid, ("any", "a contrasting colour"))
        print(f"wrote {slug(cid)}.stl/.3mf  ({DECK[cid][0]}: print in {card_c}, first 0.6 mm in {skin_c})")
    preview(ids)
    print(f"wrote {OUT / 'preview_deck.png'}. Next: .venv/bin/python check_deck.py")


if __name__ == "__main__":
    main()
