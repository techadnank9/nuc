#!/usr/bin/env python3
"""Validate the exported v2 parts, independently of their generator, in mm.

No ray/graph optional dependencies are required. Run with .venv/bin/python.
"""
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import manifold3d as m
import numpy as np
import trimesh
from check_card import z_hits

OUT = Path(__file__).resolve().parent / 'out'
TOL = 3e-4


def close(actual, expected, label, tol=TOL):
    assert np.shape(actual) == np.shape(expected), f'{label}: shape {np.shape(actual)} != {np.shape(expected)}'
    assert np.allclose(actual, expected, atol=tol, rtol=0), f'{label}: {actual} != {expected}'


def load(path):
    if path.suffix == '.stl':
        return trimesh.load(path, force='mesh')
    with zipfile.ZipFile(path) as archive:
        model = ET.fromstring(archive.read('3D/3dmodel.model'))
        rels = ET.fromstring(archive.read('_rels/.rels'))
        assert rels[0].attrib['Target'] == '/3D/3dmodel.model'
        types = ET.fromstring(archive.read('[Content_Types].xml'))
        assert any(n.attrib.get('Extension') == 'model' for n in types)
    assert model.attrib['unit'] == 'millimeter'
    objects = model.findall('{*}resources/{*}object')
    items = model.findall('{*}build/{*}item')
    assert len(objects) == len(items) == 1, '3MF must contain one printable object'
    assert items[0].attrib['objectid'] == objects[0].attrib['id']
    assert 'transform' not in items[0].attrib
    assert objects[0].attrib['name'] == path.stem
    vertices = [[float(v.attrib[k]) for k in ['x', 'y', 'z']]
                for v in objects[0].findall('{*}mesh/{*}vertices/{*}vertex')]
    faces = [[int(f.attrib[k]) for k in ['v1', 'v2', 'v3']]
             for f in objects[0].findall('{*}mesh/{*}triangles/{*}triangle')]
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=True)


def topology(mesh, name):
    assert mesh.is_watertight and mesh.is_winding_consistent and mesh.is_volume, name
    assert mesh.volume > 0
    neighbors = [[] for _ in mesh.faces]
    for a, b in mesh.face_adjacency:
        neighbors[a].append(b)
        neighbors[b].append(a)
    seen, pending = {0}, [0]
    while pending:
        for n in neighbors[pending.pop()]:
            if n not in seen:
                seen.add(n)
                pending.append(n)
    assert len(seen) == len(mesh.faces), f'{name}: disconnected solid'
    close(float(mesh.bounds[0, 2]), 0., name + ' lowest Z')
    assert np.all(mesh.extents <= np.array([250, 220, 220]) + TOL), f'{name}: exceeds bed'
    bed = np.all(np.abs(mesh.triangles[:, :, 2]) < TOL, axis=1)
    assert mesh.area_faces[bed].sum() > 1000, f'{name}: insufficient bed contact'


def manifold(mesh):
    return m.Manifold(m.Mesh(np.asarray(mesh.vertices, dtype=np.float32),
                             np.asarray(mesh.faces, dtype=np.uint32)))


def no_collision(a, b, label):
    volume = (manifold(a) ^ manifold(b)).volume()
    assert volume < .003, f'{label}: {volume:.6f} mm³ interference'


def pose(card, angle, y, z):
    """Card local height is print Y+44; front is print Z."""
    s, c = np.sin(np.deg2rad(angle)), np.cos(np.deg2rad(angle))
    matrix = np.array([[1, 0, 0, 0], [0, s, -c, y+44*s],
                       [0, c, s, z+44*c], [0, 0, 0, 1]])
    return card.copy().apply_transform(matrix)


def line_hits(mesh, origin, direction):
    """Return signed distances along an arbitrary line through the exported mesh."""
    direction = np.asarray(direction, dtype=float)
    direction /= np.linalg.norm(direction)
    reference = [1., 0, 0] if abs(direction[0]) < .9 else [0., 1, 0]
    u = np.cross(direction, reference)
    u /= np.linalg.norm(u)
    v = np.cross(direction, u)
    local = mesh.copy()
    local.vertices = (mesh.vertices - origin) @ np.array([u, v, direction]).T
    return z_hits(local, 0, 0)


def check_card(card):
    close(card.bounds, [[-31.5, -44, 0], [31.5, 62, 14]], 'card bounds')
    for x in np.linspace(-29, 29, 9):
        for y in np.linspace(-41, 41, 13):
            hits = z_hits(card, x, y)
            close(float(hits[0]), 0., 'flat card back')
            assert hits[-1] <= 4 + TOL, 'raised face relief'
    for x, y in [(0, 0), (26, 38), (-26, -38)]:
        close(z_hits(card, x, y), [0, 3.4], 'sticker pocket depth')
    for x, y in [(28, 0), (0, 41), (-28, 0), (0, -41)]:
        close(z_hits(card, x, y), [0, 4], '4 mm pocket rim')
    face = np.all(np.abs(card.triangles[:, :, 2] - 3.4) < TOL, axis=1)
    vertices = card.triangles[face].reshape(-1, 3)
    close([vertices.min(0), vertices.max(0)], [[-27.5,-40,3.4], [27.5,40,3.4]], '55x80 pocket')
    expected_area = 55*80 - (4-np.pi)*1.5**2
    close(float(card.area_faces[face].sum()), expected_area, 'pocket corner radius 1.5', .01)
    for sx in [-1, 1]:
        for sy in [-1, 1]:
            assert len(z_hits(card, sx*31, sy*43.5)) == 0, 'body corner radius'
            close(z_hits(card, sx*29, sy*41), [0,4], 'body corner interior')
    for y in [45.5,48,51.2,56]:
        close(z_hits(card, 0, y), [0,14], '14 mm grip lands')
    for y in np.arange(47.2, 58, 1.6):
        close(z_hits(card, 0, y), [.4,13.6], '0.4 mm ribs both faces')
    for y in [58.2,59,60,61,61.9]:
        close(z_hits(card, 0, y), [y-58,14-(y-58)], '4 mm 45 degree top chamfers')
    close(z_hits(card, 0, -43.6), [0,3.6], 'bottom bevel')
    print('Card: body 63 × 88 × 4; overall 63 × 106 × 14; handle 30 × 18 × 14 mm')
    print('      pocket 55 × 80 × 0.6, R1.5; body R3; top front/back chamfers 4 mm; ribs 0.4 mm')


def check_stand(stand, card):
    close(stand.extents[:2], [120,100], 'stand footprint')
    assert stand.bounds[1,2] <= 60 + TOL, 'funnel too high'
    s,c = .5,np.sqrt(3)/2
    origin = np.array([0.,-20.,6.])
    up, front = np.array([0.,s,c]), np.array([0.,-c,s])
    # Slot dimensions are measured normal to the tilted card, not horizontally.
    for h in [2.,10.,19.]:
        hits = line_hits(stand, origin+h*up+2*front, front)
        close(hits[(hits > -4) & (hits < 4)], [-3,3], '6 mm slot')
        hits = line_hits(stand, origin+h*up+2*front, [1,0,0])
        close(hits[(hits > -34) & (hits < 34)], [-33,33], '66 mm slot length')
    hits = line_hits(stand, origin+2*front, up)
    close(float(hits[-1]), 0., 'blind slot bottom / seated datum')
    # Mouth boundary is an actual edge at Z=max, 80 across X and 40 across Y.
    top = stand.bounds[1,2]
    mouth_center = origin+20*up+2*front+np.array([0.,25/np.sqrt(3),25.])
    close(float(top), float(mouth_center[2]), '25 mm funnel height above slot centre')
    for direction, expected in [([1,0,0],[-40,40]), ([0,1,0],[-20,20])]:
        hits = line_hits(stand, mouth_center-[0,0,.0001], direction)
        close(hits[1:-1], expected, '80 × 40 funnel mouth', .001)
    seated = pose(card,30,-20,6)
    no_collision(stand,seated,'stand / seated 4 mm card')
    for lift in [1,5,10,20,30,45,65]:
        no_collision(stand,seated.copy().apply_translation(lift*up),'stand / insertion path')
    # Top horizontal edge is at local height 106, front thickness 4..10 after chamfer.
    handle_top_low = 6+106*c+4*s
    handle_bottom_low = 6+88*c
    assert handle_top_low-top >=18-TOL, 'handle top must stand >=18 mm above funnel'
    assert handle_bottom_low > top, 'whole handle must clear funnel'
    for x in [-52,52]:
        assert len(z_hits(stand,x,0)) == 0, 'stand clamp hole must be through'
        close(z_hits(stand,x+2.2,0),[0,4],'stand 4 mm hole perimeter / base')
    print(f'Stand: base 120 × 100; slot 66 × 6 × 20 at 30° from vertical; funnel 80 × 40 mm')
    print(f'       funnel top Z={top:.2f}; lowest handle-top point {handle_top_low-top:.2f} mm above funnel')


def check_tray(tray, card):
    close(tray.extents[:2], [110,185], 'tray footprint')
    s,c = np.sin(np.deg2rad(15)),np.cos(np.deg2rad(15))
    up,front = np.array([0.,s,c]),np.array([0.,-c,s])
    slope = np.tan(np.deg2rad(15))
    offsets = [0]+[(i*14.4+25)/(c-s*slope) for i in range(1,7)]
    origins = [np.array([0.,14+d,4+d*slope]) for d in offsets]
    # Measure both walls of each inclined seating channel in the export.
    for origin in origins:
        hits = line_hits(tray, origin+3*up+3.3*front, front)
        local = hits[(hits>-3.31) & (hits<3.31)]
        close(local,[-3.3,3.3], 'tray 6.6 mm tilted seat')
        hits = line_hits(tray,origin+2*front,up)
        close(float(hits[hits<1][-1]),0., 'tray seat floor datum')
    # Above the ramp the side walls leave a 67 mm bay, with 3 mm walls.
    # Keep probes ahead of the inclined rear wall, which closes the bay.
    for y in [20.,65.,100.,135.]:
        floor_z = 4+slope*(y-14)+8
        hits = line_hits(tray,[0,y,floor_z+20],[1,0,0])
        close(hits,[-36.5,-33.5,33.5,36.5], '67 mm bay / 3 mm walls')
        close(float(z_hits(tray,35,y)[-1]),floor_z+40,'40 mm side wall')
    close(float(z_hits(tray,0,6)[-1]),12.,'8 mm lip above 4 mm seated heel')
    rear_bottom_y = origins[-1][1]+.4
    rear_top_z = origins[-1][2]+60
    rear_top_y = rear_bottom_y+60*np.tan(np.deg2rad(15))
    close(float(z_hits(tray,0,rear_top_y+1)[-1]),rear_top_z,'60 mm rear wall')
    for z in [origins[-1][2]+25,origins[-1][2]+50]:
        hits = line_hits(tray,[0,0,z],[0,1,0])
        expected = rear_bottom_y+(z-origins[-1][2])*np.tan(np.deg2rad(15))
        assert np.any(np.isclose(hits,expected,atol=TOL)), '15 degree rear wall'
    for x in [-46,46]:
        assert len(z_hits(tray,x,14))==0, 'tray fixing hole must be through'
        close(z_hits(tray,x+2.2,14),[0,4],'4 mm hole perimeter / base')
    cards = [pose(card,15,origin[1],origin[2]) for origin in origins]
    for i,placed in enumerate(cards):
        no_collision(tray,placed,f'tray / card {i+1}')
        for j,other in enumerate(cards[:i]):
            no_collision(placed,other,f'cards {j+1} / {i+1}')
    # Reconstruct the front card's seating pose for either load, then check the
    # actual support contact; equal reported constants alone would be vacuous.
    grips=[]
    for count in [1,7]:
        first=cards[:count][0]
        no_collision(tray,first,f'{count}-card front seat')
        for delta,label in [(-.05*up,'floor'),(-.05*front,'back datum')]:
            assert (manifold(tray)^manifold(first.copy().apply_translation(delta))).volume()>.01, label+' must contact'
        grip=origins[0]+97*up+7*front
        grips.append(grip)
        print(f'Tray {count}-card load: front handle centre XYZ = {np.round(grip,3)} mm')
    close(grips[0],grips[1], 'pickup datum independent of load')
    # Collision-free 10 mm vertical lift, then pull out toward the front.
    for lift in np.linspace(0,10,21):
        moving=cards[0].copy().apply_translation([0,0,lift])
        no_collision(tray,moving,'front card vertical pickup')
        for other in cards[1:]:
            no_collision(moving,other,'pickup / remaining deck')
    for distance in np.linspace(0,60,13):
        no_collision(tray,cards[0].copy().apply_translation([0,-distance,10]),'front extraction')
    # No tray wall in the actual grasp envelope: 25 mm beyond either handle
    # side, and 25 mm outside either opposing grip face, over the usable lands.
    points=[origins[0]+[x,0,0]+h*up+t*front
            for x in [-40,40] for h in [88,102] for t in [-25,39]]
    envelope=m.Manifold.hull_points(points)
    assert (envelope^manifold(tray)).volume()<.003, 'tray wall obstructs 25 mm grasp envelope'
    # A 6 mm pad can approach each grip face without touching another card.
    for t0,t1 in [(-6,0),(14,20)]:
        pad=m.Manifold.hull_points([origins[0]+[x,0,0]+h*up+t*front
                                   for x in [-12,12] for h in [90,100] for t in [t0,t1]])
        for other in cards[1:]:
            assert (pad^manifold(other)).volume()<.003, 'neighbor blocks grip face'
    print(f'Tray: base 110 × 185; bay 67; side walls 3 × 40; front lip 8; rear wall 60 mm')
    print(f'      15° lean and floor; 14.4 mm normal staging pitch; extra pickup gap 25 mm')
    print(f'      heel span {offsets[-1]:.2f} mm; manual lift-and-advance after each draw')


def main():
    parts = {}
    for name in ['card_v2','deck_tray','reveal_stand']:
        meshes = []
        for suffix in ['stl','3mf']:
            path = OUT / f'{name}.{suffix}'
            assert path.is_file(), f'Missing {path}; run .venv/bin/python generate_v2.py'
            mesh = load(path)
            topology(mesh, path.name)
            meshes.append(mesh)
            print(f'PASS {path.name}: watertight, one solid, bed Z=0, {np.round(mesh.extents,3)} mm')
        close(meshes[0].bounds, meshes[1].bounds, name+' STL/3MF bounds')
        close(float(meshes[0].volume), float(meshes[1].volume), name+' STL/3MF volume', .03)
        close(meshes[0].triangles,meshes[1].triangles,name+' STL/3MF complete surface agreement')
        parts[name] = meshes[0]
    for name in ['card_v2','tray','stand']:
        path = OUT/f'preview_{name}.png'
        assert path.is_file() and path.stat().st_size >1000, f'Missing preview: {path}'
        assert path.read_bytes()[:8] == b'\x89PNG\r\n\x1a\n', 'invalid PNG'
    check_card(parts['card_v2'])
    check_stand(parts['reveal_stand'],parts['card_v2'])
    check_tray(parts['deck_tray'],parts['card_v2'])
    print('All v2 checks passed. Units: mm. Print each part as exported, PLA, 0.2 mm, no supports.')


if __name__ == '__main__':
    main()
