#!/usr/bin/env python3
"""Check the exported geometry independently of the generator (millimetres)."""
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import trimesh

OUT = Path(__file__).resolve().parent / 'out'
TOL = 2e-4


def close(actual, expected, label):
    assert np.allclose(actual, expected, atol=TOL, rtol=0), f'{label}: {actual} != {expected}'


def z_hits(mesh, x, y):
    """Intersect a vertical line with triangles, without optional ray dependencies."""
    tri = mesh.triangles
    a = tri[:, 0, :2]
    u, v = tri[:, 1, :2] - a, tri[:, 2, :2] - a
    det = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    valid = np.abs(det) > 1e-10
    a, u, v, det, tri = a[valid], u[valid], v[valid], det[valid], tri[valid]
    q = np.array([x, y]) - a
    s = (q[:, 0] * v[:, 1] - q[:, 1] * v[:, 0]) / det
    t = (u[:, 0] * q[:, 1] - u[:, 1] * q[:, 0]) / det
    inside = (s >= -1e-8) & (t >= -1e-8) & (s + t <= 1 + 1e-8)
    z = tri[:, 0, 2] + s * (tri[:, 1, 2] - tri[:, 0, 2]) + t * (tri[:, 2, 2] - tri[:, 0, 2])
    return np.unique(np.round(z[inside], 5))


def check_geometry(mesh):
    assert mesh.is_watertight, 'mesh must be watertight'
    assert mesh.is_winding_consistent and mesh.is_volume, 'outward closed solid required'
    neighbors = [[] for _ in mesh.faces]
    for a, b in mesh.face_adjacency:
        neighbors[a].append(b)
        neighbors[b].append(a)
    seen, pending = {0}, [0]
    while pending:
        for neighbor in neighbors[pending.pop()]:
            if neighbor not in seen:
                seen.add(neighbor)
                pending.append(neighbor)
    assert len(seen) == len(mesh.faces), 'all features must form one connected solid'
    assert mesh.volume > 0, 'positive volume required'
    close(mesh.bounds, [[-31.5, -44, 0], [31.5, 62, 14]], 'overall bounds')
    close(mesh.extents, [63, 106, 14], 'overall dimensions')
    close(mesh.vertices[:, 2].min(), 0, 'lowest Z / bed')
    assert np.all(mesh.vertices[:, 2] >= -TOL), 'nothing may protrude below the bed'

    # Actual flat grip lands, measured from the exported surface, not constants.
    for x in [-13, 0, 13]:
        for y in [45.5, 48, 51.2, 56, 59.2]:
            hits = z_hits(mesh, x, y)
            close(hits, [0, 14], 'opposing grip lands')
            close(hits[-1] - hits[0], 14, 'grip face separation')
    grip = z_hits(mesh, 0, 48)
    margin = (100 - (grip[-1] - grip[0])) / 2
    assert margin >= 40, 'at least 40 mm centering margin per side in 100 mm opening'

    # Every non-recessed back surface is on Z=0. Probe the complete body and handle.
    for x in np.linspace(-30, 30, 25):
        for y in np.linspace(-42, 42, 29):
            if abs(x) <= 15 and -36 <= y <= -6:
                continue  # intentional sticker pocket
            hits = z_hits(mesh, x, y)
            assert len(hits) >= 2, f'missing body at {x}, {y}'
            close(hits[0], 0, 'body back must lie on bed')
    for y in [44.5, 46, 50, 58, 59.8]:
        close(z_hits(mesh, 14.5, y)[0], 0, 'handle back coplanar with body')
    bed = np.all(np.abs(mesh.triangles[:, :, 2]) < TOL, axis=1)
    assert mesh.area_faces[bed].sum() > 4700, 'substantial body and handle bed contact required'
    close(z_hits(mesh, 25, 0), [0, 4], 'body thickness')

    # 3 mm corner radii: the four corner squares must be cut back.
    for sx in [-1, 1]:
        for sy in [-1, 1]:
            assert len(z_hits(mesh, sx * 31, sy * 43.5)) == 0, 'rounded corner missing'
            close(z_hits(mesh, sx * 29, sy * 41), [0, 4], 'corner interior')

    pocket = np.all(np.abs(mesh.triangles[:, :, 2] - 0.6) < TOL, axis=1)
    assert pocket.any(), 'pocket floor missing'
    p = mesh.triangles[pocket].reshape(-1, 3)
    close([p.min(axis=0), p.max(axis=0)], [[-15, -36, .6], [15, -6, .6]], 'pocket bounds')
    close(mesh.area_faces[pocket].sum(), 900, 'pocket area')
    close(z_hits(mesh, 0, -30)[0], .6, 'pocket depth')
    for x, y in [(15.2, -20), (-15.2, -20), (0, -36.2), (0, -5.8)]:
        close(z_hits(mesh, x, y)[0], 0, 'pocket perimeter on bed')

    # Ribs have 0.4 mm relief on BOTH faces, and never extend below the bed.
    for y in np.arange(47.2, 59, 1.6):
        close(z_hits(mesh, 0, y), [.4, 13.6], 'rib valley depth')
    # Top chamfers: 2 mm at 45 degrees on front/back and left/right.
    close(z_hits(mesh, 0, 61), [1, 13], 'top front/back chamfers')
    assert len(z_hits(mesh, 14.5, 61)) == 0, 'top side chamfer missing'
    close(z_hits(mesh, 0, -43.6), [0, 3.6], 'bottom 45 degree bevel')

    # One-mm raised disc, all 12 rays, and bar-built XIX.
    close(z_hits(mesh, 0, 13)[-1], 5, 'sun disc relief')
    for angle in np.arange(12) * np.pi / 6:
        close(z_hits(mesh, 14 * np.cos(angle), 13 + 14 * np.sin(angle))[-1], 5, 'sun ray')
    for x, y in [(0, -20), (-11, -20), (11, -20), (-14, -15), (14, -25)]:
        close(z_hits(mesh, x, y)[-1], 5, 'XIX bars')
    return margin


def main():
    meshes = []
    for suffix in ['stl', '3mf']:
        path = OUT / f'card_19_sun.{suffix}'
        assert path.is_file(), f'Missing output: {path}; run generate_card.py first'
        if suffix == 'stl':
            mesh = trimesh.load(path, force='mesh')
        else:
            with zipfile.ZipFile(path) as archive:
                model = ET.fromstring(archive.read('3D/3dmodel.model'))
                rels = ET.fromstring(archive.read('_rels/.rels'))
                assert rels[0].attrib['Target'] == '/3D/3dmodel.model'
                types = ET.fromstring(archive.read('[Content_Types].xml'))
                assert any(n.attrib.get('Extension') == 'model' for n in types)
            objects = model.findall('{*}resources/{*}object')
            items = model.findall('{*}build/{*}item')
            assert len(objects) == len(items) == 1
            assert items[0].attrib['objectid'] == objects[0].attrib['id']
            assert 'transform' not in items[0].attrib, 'build must preserve print orientation'
            vertices = [[float(v.attrib[k]) for k in ['x', 'y', 'z']]
                        for v in objects[0].findall('{*}mesh/{*}vertices/{*}vertex')]
            faces = [[int(f.attrib[k]) for k in ['v1', 'v2', 'v3']]
                     for f in objects[0].findall('{*}mesh/{*}triangles/{*}triangle')]
            mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
        margin = check_geometry(mesh)
        meshes.append(mesh)
        print(f'PASS {path.name}: watertight, one solid, {len(mesh.faces)} triangles')
    close(meshes[0].volume, meshes[1].volume, 'STL/3MF volume agreement')
    with zipfile.ZipFile(OUT / 'card_19_sun.3mf') as archive:
        model = ET.fromstring(archive.read('3D/3dmodel.model'))
        assert model.attrib.get('unit') == 'millimeter', '3MF units must be millimetres'
    for name in ['front', 'back', 'side', 'grip']:
        path = OUT / f'preview_{name}.png'
        assert path.is_file() and path.stat().st_size > 1000, f'missing/empty preview: {name}'
        assert path.read_bytes()[:8] == b'\x89PNG\r\n\x1a\n', 'invalid PNG header'
    volume = meshes[0].volume
    print(f'Overall: 63 × 106 × 14 mm; body: 63 × 88 × 4 mm')
    print(f'Handle: 30 × 18 × 14 mm; grip spacing: 14 mm; opening margin: ±{margin:.0f} mm')
    print(f'Volume: {volume / 1000:.3f} cm³; rough PLA mass (1.24 g/cm³ × 20%): {volume / 1000 * 1.24 * .2:.2f} g')
    print('All checks passed; back datum and lowest Z = 0 mm.')


if __name__ == '__main__':
    main()
