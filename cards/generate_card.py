#!/usr/bin/env python3
"""Build the Sun XIX fortune card in millimetres, already in print orientation."""
from pathlib import Path
import os
import tempfile
import xml.etree.ElementTree as ET
import zipfile

# Use the installed matplotlib without writing to the user's home directory.
os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir()) / 'fortune-card-mpl'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.patches import Polygon, Rectangle
import manifold3d as m
import numpy as np
import trimesh

OUT = Path(__file__).resolve().parent / 'out'
BODY_WIDTH, BODY_HEIGHT, BODY_THICKNESS = 63., 88., 4.
CORNER_RADIUS = 3.
HANDLE_WIDTH, HANDLE_HEIGHT, HANDLE_DEPTH = 30., 18., 14.
TOP_CHAMFER, BOTTOM_CHAMFER, RIB_RELIEF = 2., .8, .4
RIB_CENTERS = np.arange(47.2, 59, 1.6)
GOLD, LIGHT, INK = '#e5ae3f', '#f6d78d', '#283847'


def box(size, origin):
    return m.Manifold.cube(size).translate(origin)


def prism(points, height, z=0):
    points = np.array(points, dtype=float)
    following = np.roll(points, -1, axis=0)
    signed_area = np.sum(points[:, 0] * following[:, 1] - following[:, 0] * points[:, 1])
    if signed_area < 0:
        points = points[::-1]
    return m.Manifold.extrude(m.CrossSection([points]), height).translate((0, 0, z))


def yz_prism(points, x0, width):
    """Extrude a YZ polygon along X, with a proper cyclic axis rotation."""
    return prism(points, width).transform(np.array([[0, 0, 1, x0], [1, 0, 0, 0], [0, 1, 0, 0]], dtype=float))


def union(solids):
    return m.Manifold.batch_boolean(solids, m.OpType.Add)


def subtract(solid, cutters):
    return m.Manifold.batch_boolean([solid, *cutters], m.OpType.Subtract)


def rounded_body():
    contour = []
    for cx, cy, start in [(28.5, 41, 0), (-28.5, 41, 90), (-28.5, -41, 180), (28.5, -41, 270)]:
        for angle in np.linspace(start, start + 90, 49):
            a = np.deg2rad(angle)
            contour.append((cx + CORNER_RADIUS * np.cos(a), cy + CORNER_RADIUS * np.sin(a)))
    body = prism(contour, BODY_THICKNESS)
    # A front-only bottom bevel retains the entire rear outline on the bed.
    bottom_bevel = yz_prism([(-45, 2.2), (-42, 5.2), (-45, 5.2)], -33, 66)
    pocket = box((30, 30, .7), (-15, -36, -.1))
    return subtract(body, [bottom_bevel, pocket])


def handle():
    solid = box((HANDLE_WIDTH, HANDLE_HEIGHT, HANDLE_DEPTH), (-15, 44, 0))
    # All four edges bordering the Y=62 top are chamfered 2 mm at 45 degrees.
    cutters = [
        yz_prism([(60, 0), (63, 3), (63, -1), (60, -1)], -16, 32),
        yz_prism([(60, 14), (63, 11), (63, 15), (60, 15)], -16, 32),
        prism([(15, 60), (12, 63), (16, 63), (16, 60)], 16, -1),
        prism([(-15, 60), (-12, 63), (-16, 63), (-16, 60)], 16, -1),
    ]
    # Recess between ribs rather than raising them past Z=0/14. The resulting
    # horizontal flat lands are 0.8 mm wide with 0.4 mm relief, on a 1.6 mm pitch.
    # Triangular channels have 45-degree walls and leave a 1 mm border at each end.
    for y in RIB_CENTERS:
        cutters += [
            yz_prism([(y - .5, -.1), (y, RIB_RELIEF), (y + .5, -.1)], -14, 28),
            yz_prism([(y - .5, 14.1), (y, 14 - RIB_RELIEF), (y + .5, 14.1)], -14, 28),
        ]
    return subtract(solid, cutters)


def bar(a, b, width=2.2):
    a, b = np.array(a, dtype=float), np.array(b, dtype=float)
    direction = b - a
    perpendicular = np.array([-direction[1], direction[0]]) / np.linalg.norm(direction) * width / 2
    return prism([a + perpendicular, a - perpendicular, b - perpendicular, b + perpendicular], 1.1, 3.9)


def artwork():
    # A 1 mm relief above the Z=4 body, with 0.1 mm overlap for robust union.
    parts = [m.Manifold.cylinder(1.1, 9, 9, 144).translate((0, 13, 3.9))]
    for angle in np.arange(12) * np.pi / 6:
        radial = np.array([np.cos(angle), np.sin(angle)])
        tangent = np.array([-radial[1], radial[0]])
        center = np.array([0, 13])
        parts.append(prism([center + 11 * radial - 2 * tangent,
                            center + 18 * radial,
                            center + 11 * radial + 2 * tangent], 1.1, 3.9))
    for x in [-11, 11]:
        parts += [bar((x - 3.6, -26), (x + 3.6, -14)),
                  bar((x - 3.6, -14), (x + 3.6, -26))]
    parts += [bar((0, -26), (0, -14), 2.4),
              bar((-3, -26), (3, -26), 2), bar((-3, -14), (3, -14), 2)]
    return union(parts)


def build_card():
    result = union([rounded_body(), handle(), artwork()])
    assert result.status() == m.Error.NoError, f'Manifold error: {result.status()}'
    raw = result.to_mesh()
    mesh = trimesh.Trimesh(vertices=np.asarray(raw.vert_properties)[:, :3],
                           faces=np.asarray(raw.tri_verts), process=True)
    mesh.units = 'mm'
    mesh.metadata['name'] = 'Card 19 - Sun XIX'
    assert mesh.is_watertight and mesh.is_volume, 'Boolean result is not a closed solid'
    return mesh


def export_3mf(mesh, path):
    """Single-object 3MF Core package; no optional networkx/lxml dependency."""
    model = ET.Element('model', xmlns='http://schemas.microsoft.com/3dmanufacturing/core/2015/02', unit='millimeter')
    resources = ET.SubElement(model, 'resources')
    obj = ET.SubElement(resources, 'object', id='1', type='model', name='Card 19 - Sun XIX')
    geometry = ET.SubElement(obj, 'mesh')
    vertices = ET.SubElement(geometry, 'vertices')
    for vertex in mesh.vertices:
        ET.SubElement(vertices, 'vertex', **dict(zip(['x', 'y', 'z'], map(str, vertex))))
    triangles = ET.SubElement(geometry, 'triangles')
    for face in mesh.faces:
        ET.SubElement(triangles, 'triangle', **dict(zip(['v1', 'v2', 'v3'], map(str, face))))
    ET.SubElement(ET.SubElement(model, 'build'), 'item', objectid='1')
    rels = ET.Element('Relationships', xmlns='http://schemas.openxmlformats.org/package/2006/relationships')
    ET.SubElement(rels, 'Relationship', Id='rel0', Target='/3D/3dmodel.model',
                  Type='http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel')
    types = ET.Element('Types', xmlns='http://schemas.openxmlformats.org/package/2006/content-types')
    ET.SubElement(types, 'Default', Extension='rels', ContentType='application/vnd.openxmlformats-package.relationships+xml')
    ET.SubElement(types, 'Default', Extension='model', ContentType='application/vnd.ms-package.3dmanufacturing-3dmodel+xml')
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, tree in [('3D/3dmodel.model', model), ('_rels/.rels', rels), ('[Content_Types].xml', types)]:
            archive.writestr(name, ET.tostring(tree, encoding='utf-8', xml_declaration=True))


def style(ax, title, xlabel='Width X (mm)', ylabel='Height Y (mm)'):
    ax.set_aspect('equal')
    ax.set_title(title, loc='left', fontsize=14, fontweight='bold', pad=14, color=INK)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_facecolor('#fcfaf5')
    ax.grid(alpha=.15)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color('#d9d9d9')


def dimension(ax, a, b, text, offset=(0, 0)):
    ax.annotate('', xy=a, xytext=b, arrowprops=dict(arrowstyle='<->', color=INK, lw=1))
    midpoint = (np.array(a) + np.array(b)) / 2 + offset
    ax.text(*midpoint, text, ha='center', va='center', fontsize=9, color=INK,
            bbox=dict(facecolor='#fcfaf5', edgecolor='none', pad=2))


def save(fig, name):
    fig.savefig(OUT / f'preview_{name}.png', dpi=180, bbox_inches='tight', facecolor='#fcfaf5')
    plt.close(fig)


def face_preview(mesh, back=False):
    fig, ax = plt.subplots(figsize=(7, 10))
    sign = -1 if back else 1
    visible = mesh.face_normals[:, 2] * sign > 1e-6
    triangles = mesh.triangles[visible]
    centers = triangles.mean(axis=1)
    order = np.argsort(centers[:, 2] * sign)
    polygons, colors = [], []
    for i in order:
        tri, c = triangles[i], centers[i]
        polygon = tri[:, :2].copy()
        if back:
            polygon[:, 0] *= -1
        polygons.append(polygon)
        if back:
            color = '#a2b4bf' if c[1] < 0 and c[2] > .1 else (GOLD if c[2] > .05 else LIGHT)
        else:
            color = GOLD if c[2] > 4.05 else LIGHT
            if c[1] > 44 and c[2] < 13.99:
                color = '#b6832b'
        colors.append(color)
    ax.add_collection(PolyCollection(polygons, facecolors=colors, edgecolors='none', antialiaseds=False))
    # Outline from a perpendicular section is unnecessary here: projected mesh
    # facets show the actual rounded corners, chamfers, and recessed rib channels.
    dimension(ax, (-31.5, -49), (31.5, -49), '63 mm', (0, 0))
    dimension(ax, (38, -44), (38, 62), '106 mm', (0, 0))
    dimension(ax, (-15, 67), (15, 67), '30 mm', (0, 0))
    if back:
        ax.text(0, -21, 'ArUco ID 19\n30 × 30 mm\n0.6 mm deep', ha='center', va='center', color=INK, fontsize=10)
        caption = 'Back datum Z=0 • blue area is the sticker recess (sticker not included)'
    else:
        caption = 'Sun disc + 12 rays and bar-built XIX • 1 mm raised relief'
    ax.set_xlim(-40, 47)
    ax.set_ylim(-54, 73)
    style(ax, 'BACK · sticker pocket' if back else 'FRONT · XIX / THE SUN')
    fig.text(.5, .035, caption, ha='center', fontsize=9, color=INK)
    save(fig, 'back' if back else 'front')


def draw_profile(ax, mesh):
    # Intersect triangles directly and stitch the section loops. A microscopic
    # offset avoids ambiguous vertices exactly on the symmetry plane.
    edges = set()
    for triangle in mesh.triangles:
        points = []
        for a, b in zip(triangle, np.roll(triangle, -1, axis=0)):
            if (a[0] - .0001) * (b[0] - .0001) < 0:
                p = a + (.0001 - a[0]) / (b[0] - a[0]) * (b - a)
                points.append(tuple(np.round(p[[2, 1]], 5)))
        if len(points) == 2 and points[0] != points[1]:
            edges.add(tuple(sorted(points)))
    adjacency = {}
    for a, b in edges:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    assert all(len(v) == 2 for v in adjacency.values()), 'section must form closed loops'
    while edges:
        start, current = next(iter(edges))
        points = [start]
        previous = start
        while current != start:
            points.append(current)
            edges.discard(tuple(sorted((previous, current))))
            following = next(p for p in adjacency[current] if p != previous)
            previous, current = current, following
        edges.discard(tuple(sorted((previous, start))))
        ax.add_patch(Polygon(points, closed=True, facecolor=LIGHT, edgecolor=INK, lw=1))
    ax.axvline(0, color='#87949b', lw=1, ls=':')


def side_preview(mesh):
    fig, ax = plt.subplots(figsize=(6, 10))
    draw_profile(ax, mesh)
    dimension(ax, (0, 68), (14, 68), '14 mm', (0, 0))
    dimension(ax, (20, 44), (20, 62), '18 mm', (5, 0))
    dimension(ax, (4, 38), (14, 38), '10 mm proud', (0, -3))
    ax.annotate('Back / bed: Z=0', xy=(0, 15), xytext=(-13, 21), fontsize=9,
                arrowprops=dict(arrowstyle='->', color=INK), rotation=90)
    ax.annotate('Pocket roof\nZ=0.6', xy=(.6, -30), xytext=(10, -34), fontsize=9,
                arrowprops=dict(arrowstyle='->', color=INK))
    ax.set_xlim(-16, 33)
    ax.set_ylim(-48, 74)
    style(ax, 'SIDE · center section at X=0', 'Print height Z (mm)')
    fig.text(.5, .03, 'Front points right • bottom bevel preserves the flat back', ha='center', fontsize=9)
    save(fig, 'side')


def grip_preview(mesh):
    fig, ax = plt.subplots(figsize=(12, 6))
    draw_profile(ax, mesh)
    # Illustrative finger dimensions only: the specification supplies opening,
    # not finger-pad dimensions. Opening is measured between the inner faces.
    for z in [-6, 14]:
        ax.add_patch(Rectangle((z, 46), 6, 11, facecolor='#567c95', edgecolor=INK, alpha=.85))
    for z in [-49, 57]:
        ax.add_patch(Rectangle((z, 46), 6, 11, fill=False, edgecolor='#567c95', ls='--', lw=2))
    for a, b in [((-42, 51.5), (-8, 51.5)), ((56, 51.5), (22, 51.5))]:
        ax.annotate('', xy=b, xytext=a, arrowprops=dict(arrowstyle='->', color='#567c95', ls='--'))
    dimension(ax, (-43, 65), (57, 65), '100 mm full opening', (0, 0))
    dimension(ax, (0, 42), (14, 42), '14 mm closed grip', (0, 0))
    dimension(ax, (-43, 60), (0, 60), '43 mm margin', (0, 0))
    dimension(ax, (14, 60), (57, 60), '43 mm margin', (0, 0))
    ax.text(7, 69, '45° top chamfers guide an off-center approach', ha='center', fontsize=10, color=INK)
    ax.set_xlim(-54, 68)
    ax.set_ylim(37, 73)
    style(ax, 'GRIP FIT · handle side section', 'Print height / grip direction Z (mm)')
    fig.text(.5, .06, 'Solid fingers: closed on Z=0 and Z=14 lands • dashed fingers: 100 mm opening\n'
             'Finger shapes are schematic; only the opening and contact positions are specified.',
             ha='center', fontsize=10, color=INK)
    save(fig, 'grip')


def main():
    OUT.mkdir(exist_ok=True)
    mesh = build_card()
    mesh.export(OUT / 'card_19_sun.stl')
    export_3mf(mesh, OUT / 'card_19_sun.3mf')
    face_preview(mesh)
    face_preview(mesh, back=True)
    side_preview(mesh)
    grip_preview(mesh)
    print(f'Wrote STL, 3MF, and four previews to {OUT}')
    print(f'Bounds (mm): {mesh.bounds.tolist()}; volume: {mesh.volume / 1000:.3f} cm³')


if __name__ == '__main__':
    main()
