#!/usr/bin/env python3
"""Fortune props v2, millimetres; run with .venv/bin/python generate_v2.py.

Tray loading: populate the front pickup seat first, then the six staging seats.
After pickup manually advance the cards (lift 10 mm, move forward, reseat).
The inclined floor has indexed seats: gravity alone cannot maintain a backward
leaning variable-size stack at a fixed front datum without a moving follower.
The front staging gap leaves both grip faces accessible. No moving parts.

All exports are single solids in their support-free print orientation. Only v2
output names are written. V1 helpers are imported without running its main().
"""
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import manifold3d as m
import trimesh
from generate_card import (box, prism, yz_prism, union, subtract,
                           plt, PolyCollection, Polygon, GOLD, LIGHT, INK,
                           style, dimension)

OUT = Path(__file__).resolve().parent/'out'
TRAY_ANGLE = 15.
TRAY_WIDTH, TRAY_DEPTH = 110., 185.
TRAY_FRONT_Y, TRAY_FRONT_Z = 14., 4.
TRAY_PITCH = 14.4             # Normal pitch of the 14 mm thick handles.
TRAY_GRIP_GAP = 25.          # Additional normal gap behind the pickup handle.
TRAY_SLOPE = np.tan(np.deg2rad(15.))
STAND_ORIGIN = np.array([0., -20., 6.])


def rounded_rectangle(width, height, radius, z, depth):
    points = []
    for cx, cy, start in [(width/2-radius,height/2-radius,0),
                           (-width/2+radius,height/2-radius,90),
                           (-width/2+radius,-height/2+radius,180),
                           (width/2-radius,-height/2+radius,270)]:
        for a in np.deg2rad(np.linspace(start,start+90,49)):
            points.append([cx+radius*np.cos(a),cy+radius*np.sin(a)])
    return prism(points,depth,z)


def build_card():
    body = rounded_rectangle(63,88,3,0,4)
    body -= yz_prism([(-45,2.2),(-42,5.2),(-45,5.2)],-33,66)
    body -= rounded_rectangle(55,80,1.5,3.4,1)
    handle = box((30,18,14),(-15,44,0))
    cutters = [
        yz_prism([(58,0),(63,5),(63,-1),(58,-1)],-16,32),
        yz_prism([(58,14),(63,9),(63,15),(58,15)],-16,32),
        # Preserve the v1 two-mm chamfers on the left/right top corners.
        prism([(15,60),(12,63),(16,63),(16,60)],16,-1),
        prism([(-15,60),(-12,63),(-16,63),(-16,60)],16,-1),
    ]
    for y in np.arange(47.2,58,1.6):
        cutters += [yz_prism([(y-.5,-.1),(y,.4),(y+.5,-.1)],-14,28),
                    yz_prism([(y-.5,14.1),(y,13.6),(y+.5,14.1)],-14,28)]
    return body+subtract(handle,cutters)


def basis(angle):
    a = np.deg2rad(angle)
    return np.array([0,np.sin(a),np.cos(a)]), np.array([0,-np.cos(a),np.sin(a)])


def local_prism(x_half, t_min, t_max, h_min, h_max, angle, origin):
    """Box in a card frame (X, axial height H, front thickness T)."""
    up, front = basis(angle)
    points = [np.array(origin)+[x,0,0]+h*up+t*front
              for x in [-x_half,x_half] for h in [h_min,h_max] for t in [t_min,t_max]]
    return m.Manifold.hull_points(points)


def tray_origins(count=7):
    assert 1 <= count <=7
    a = np.deg2rad(TRAY_ANGLE)
    projection = np.cos(a)-TRAY_SLOPE*np.sin(a)
    offsets = [0]+[(i*TRAY_PITCH+TRAY_GRIP_GAP)/projection for i in range(1,count)]
    return [np.array([0,TRAY_FRONT_Y+d,TRAY_FRONT_Z+TRAY_SLOPE*d]) for d in offsets]


def floor_height(y):
    return TRAY_FRONT_Z+TRAY_SLOPE*(y-TRAY_FRONT_Y)+8


def build_tray():
    origins = tray_origins()
    last = origins[-1]
    up, front = basis(TRAY_ANGLE)
    # 0.4 mm gap beyond the last back face; 60 mm vertical rise.
    rear_bottom = last + [0,.4,0]
    rear_top = rear_bottom + [0,60*np.tan(np.deg2rad(15)),60]
    ramp_start = TRAY_FRONT_Y-7
    ramp_end = rear_top[1]+3
    ramp = yz_prism([(ramp_start,3.5),(ramp_end,3.5),
                     (ramp_end,floor_height(ramp_end)),(ramp_start,floor_height(ramp_start))],-33.5,67)
    # Front lip ends exactly 8 mm above the front-card heel datum.
    stop = box((67,3,8.5),(-33.5,ramp_start-2,3.5))
    side_profile = [(ramp_start-2,3.5),(ramp_end,3.5),
                    (ramp_end,floor_height(ramp_end)+40),
                    (ramp_start-2,floor_height(ramp_start-2)+40)]
    sides = [yz_prism(side_profile,-36.5,3), yz_prism(side_profile,33.5,3)]
    rear = yz_prism([(rear_bottom[1],3.5),(rear_bottom[1]+3,3.5),
                     (rear_top[1]+3,rear_top[2]),(rear_top[1],rear_top[2]),
                     (rear_bottom[1],rear_bottom[2])],-33.5,67)
    base = box((TRAY_WIDTH,TRAY_DEPTH,4),(-TRAY_WIDTH/2,0,0))
    solid = union([base,ramp,stop,rear,*sides])
    # Each seat has a 15° back datum and 2.6 mm front clearance. Their floors
    # are perpendicular to the card, allowing the entire bottom edge to seat.
    # Seats interrupt the ramp, so indexing is deliberately manual.
    # 2.6 mm >= 10*sin(15°): the arm can lift vertically without binding.
    cutters = [local_prism(33.5,0,6.6,0,30,TRAY_ANGLE,origin) for origin in origins]
    for x in [-46,46]:
        cutters.append(m.Manifold.cylinder(6,2,2,64).translate((x,14,-1)))
    return subtract(solid,cutters)


def stand_mouth():
    up,front = basis(30)
    return STAND_ORIGIN+20*up+2*front+[0,25/np.sqrt(3),25]


def build_stand():
    up,front = basis(30)
    mouth = stand_mouth()
    top_z = mouth[2]
    # A broad supporting plinth tapers INWARD as it rises. Thus the wide
    # mouth does not create a cantilevered outer lip requiring supports.
    bottom = [[x,y,3.5] for x in [-46,46] for y in [-35,35]]
    top = [[x,mouth[1]+y,top_z] for x in [-43,43] for y in [-23,23]]
    outer = m.Manifold.hull_points(bottom+top)
    base = box((120,100,4),(-60,-50,0))
    slot = local_prism(33,-1,5,0,20.01,30,STAND_ORIGIN)
    throat = [STAND_ORIGIN+[x,0,0]+20*up+t*front
              for x in [-33,33] for t in [-1,5]]
    opening = [np.array([x,mouth[1]+y,top_z]) for x in [-40,40] for y in [-20,20]]
    funnel = m.Manifold.hull_points(throat+opening)
    # Continue the mouth above the part for a robust open top subtraction.
    extension = box((80,40,2),(-40,mouth[1]-20,top_z-.001))
    holes = [m.Manifold.cylinder(6,2,2,64).translate((x,0,-1)) for x in [-52,52]]
    return subtract(outer+base,[slot,funnel,extension,*holes])


def mesh_from(solid, name):
    assert solid.status() == m.Error.NoError, f'{name}: {solid.status()}'
    raw = solid.to_mesh()
    mesh = trimesh.Trimesh(vertices=np.asarray(raw.vert_properties)[:,:3],
                           faces=np.asarray(raw.tri_verts),process=True)
    mesh.units = 'mm'
    mesh.metadata['name'] = name
    assert mesh.is_volume and mesh.is_watertight, name
    return mesh


def export_3mf(mesh, path):
    """3MF Core with explicit mm units, identity build transform, one object."""
    model = ET.Element('model',xmlns='http://schemas.microsoft.com/3dmanufacturing/core/2015/02',unit='millimeter')
    obj = ET.SubElement(ET.SubElement(model,'resources'),'object',id='1',type='model',name=path.stem)
    geometry = ET.SubElement(obj,'mesh')
    vertices = ET.SubElement(geometry,'vertices')
    for vertex in mesh.vertices:
        ET.SubElement(vertices,'vertex',**dict(zip(['x','y','z'],map(str,vertex))))
    triangles = ET.SubElement(geometry,'triangles')
    for face in mesh.faces:
        ET.SubElement(triangles,'triangle',**dict(zip(['v1','v2','v3'],map(str,face))))
    ET.SubElement(ET.SubElement(model,'build'),'item',objectid='1')
    rels = ET.Element('Relationships',xmlns='http://schemas.openxmlformats.org/package/2006/relationships')
    ET.SubElement(rels,'Relationship',Id='rel0',Target='/3D/3dmodel.model',
                  Type='http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel')
    types = ET.Element('Types',xmlns='http://schemas.openxmlformats.org/package/2006/content-types')
    ET.SubElement(types,'Default',Extension='rels',ContentType='application/vnd.openxmlformats-package.relationships+xml')
    ET.SubElement(types,'Default',Extension='model',ContentType='application/vnd.ms-package.3dmanufacturing-3dmodel+xml')
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,tree in [('3D/3dmodel.model',model),('_rels/.rels',rels),('[Content_Types].xml',types)]:
            archive.writestr(name,ET.tostring(tree,encoding='utf-8',xml_declaration=True))


def placed_card(card, angle, origin):
    up,front = basis(angle)
    transform = np.eye(4)
    transform[:3,:3] = np.array([[1,0,0],up,front]).T
    transform[:3,3] = origin+44*up
    return card.copy().apply_transform(transform)


def section(ax, mesh, axes=(1,2), color=LIGHT, alpha=1, linewidth=1):
    """Actual exported mesh section; all loops stitched without shapely/rtree."""
    edges = set()
    for triangle in mesh.triangles:
        points = []
        for a,b in zip(triangle,np.roll(triangle,-1,axis=0)):
            if (a[0]-.00013)*(b[0]-.00013) < 0:
                point = a+(.00013-a[0])/(b[0]-a[0])*(b-a)
                points.append(tuple(np.round(point[list(axes)],5)))
        if len(points)==2 and points[0]!=points[1]:
            edges.add(tuple(sorted(points)))
    adjacency = {}
    for a,b in edges:
        adjacency.setdefault(a,set()).add(b)
        adjacency.setdefault(b,set()).add(a)
    assert adjacency and all(len(v)==2 for v in adjacency.values()), 'section must close'
    while edges:
        start,current = next(iter(edges))
        points,previous = [start],start
        while current != start:
            points.append(current)
            edges.discard(tuple(sorted((previous,current))))
            following = next(p for p in adjacency[current] if p != previous)
            previous,current = current,following
        edges.discard(tuple(sorted((previous,start))))
        ax.add_patch(Polygon(points,closed=True,facecolor=color,
                             edgecolor=INK,alpha=alpha,lw=linewidth))


def save(fig,name):
    fig.savefig(OUT/f'preview_{name}.png',dpi=180,bbox_inches='tight',facecolor='#fcfaf5')
    plt.close(fig)


def preview_card(card):
    fig,(ax,side) = plt.subplots(1,2,figsize=(11,9),gridspec_kw={'width_ratios':[1.65,1]})
    visible = card.face_normals[:,2]>1e-6
    triangles = card.triangles[visible]
    centers = triangles.mean(1)
    order = np.argsort(centers[:,2])
    colors = ['#aec5ce' if abs(centers[i,2]-3.4)<.001 else
              (('#b6832b' if centers[i,2]<13.99 else GOLD)
               if centers[i,1]>44 else LIGHT) for i in order]
    ax.add_collection(PolyCollection(triangles[order,:,:2],facecolors=colors,edgecolors='none',antialiaseds=False))
    ax.text(0,0,'FULL-COLOUR STICKER\n55 × 80 mm pocket\n0.6 mm deep · R1.5\n\nArt + ArUco on sticker',ha='center',va='center',fontsize=11,color=INK)
    dimension(ax,(-31.5,-50),(31.5,-50),'63 mm')
    dimension(ax,(38,-44),(38,62),'106 mm')
    ax.set(xlim=(-39,47),ylim=(-55,72))
    style(ax,'CARD V2 · front')
    section(side,card,axes=(2,1))
    dimension(side,(0,68),(14,68),'14 mm')
    dimension(side,(21,44),(21,62),'18 mm',(3,0))
    side.annotate('4 mm / 45°\ntop chamfers',xy=(2,60),xytext=(-12,35),fontsize=9,
                  arrowprops=dict(arrowstyle='->',color=INK))
    side.annotate('0.6 mm pocket',xy=(3.4,0),xytext=(10,10),fontsize=9,
                  arrowprops=dict(arrowstyle='->',color=INK))
    side.set(xlim=(-14,34),ylim=(-55,72))
    style(side,'SIDE · mesh section','Print Z (mm)','Card Y (mm)')
    fig.text(.5,.025,'Flat back on bed • 0.4 mm recessed grip ribs • one print serves all seven stickers',ha='center',color=INK)
    fig.tight_layout(rect=(0,.05,1,1))
    save(fig,'card_v2')


def preview_tray(tray,card):
    fig,ax = plt.subplots(figsize=(13,9))
    section(ax,tray)
    origins = tray_origins()
    for i,origin in enumerate(origins):
        section(ax,placed_card(card,15,origin),color=GOLD if i==0 else '#aec5ce',linewidth=.8)
        top = origin+101*basis(15)[0]+7*basis(15)[1]
        ax.text(top[1],top[2]+5,str(i+1),ha='center',color=INK,fontsize=10)
    grip = origins[0]+97*basis(15)[0]+7*basis(15)[1]
    ax.plot(grip[1],grip[2],'o',color='#ae4938',ms=5)
    ax.annotate('Fixed pickup datum\n1 or 7 cards',xy=grip[1:],xytext=(-3,136),fontsize=10,
                arrowprops=dict(arrowstyle='->',color=INK))
    ax.annotate('8 mm front lip\n10 mm lift clears',xy=(8,12),xytext=(-2,49),fontsize=10,
                arrowprops=dict(arrowstyle='->',color=INK))
    ax.annotate('15° seats on\n15° forward slope',xy=(100,floor_height(100)),xytext=(94,12),fontsize=10,
                arrowprops=dict(arrowstyle='->',color=INK))
    ax.annotate('25 mm extra gap\nbehind pickup handle',xy=(54,99),xytext=(72,77),fontsize=10,
                arrowprops=dict(arrowstyle='->',color=INK))
    dimension(ax,(0,-7),(185,-7),'185 mm base depth · 110 mm across')
    ax.set(xlim=(-8,193),ylim=(-13,157))
    style(ax,'DECK TRAY · actual centre section with seven cards','Front ← Y (mm) → Back','Z (mm)')
    fig.text(.5,.025,'67 mm inner width • 3 mm side walls (40 mm above ramp) • 60 mm rear support • two Ø4 mm fixing holes\n'
             'MANUAL ADVANCE: lift and reseat remaining cards after each draw; always fill the front seat first.',
             ha='center',fontsize=10,color=INK)
    fig.tight_layout(rect=(0,.07,1,1))
    save(fig,'tray')


def preview_stand(stand,card):
    fig,ax = plt.subplots(figsize=(10,9))
    section(ax,stand)
    section(ax,placed_card(card,30,STAND_ORIGIN),color=GOLD)
    mouth = stand_mouth()
    dimension(ax,(mouth[1]-20,mouth[2]+4),(mouth[1]+20,mouth[2]+4),'40 mm mouth')
    dimension(ax,(-50,-6),(50,-6),'100 mm base depth · 120 mm across')
    ax.annotate('6 mm slot\n20 mm axial depth',xy=(-16,16),xytext=(-49,31),fontsize=10,
                arrowprops=dict(arrowstyle='->',color=INK))
    ax.annotate('25 mm funnel flare\n80 mm long across',xy=(19,42),xytext=(32,63),fontsize=10,
                arrowprops=dict(arrowstyle='->',color=INK))
    ax.annotate('Card: 60° to horizontal\nHandle fully above funnel',xy=(25,91),xytext=(-48,108),fontsize=10,
                arrowprops=dict(arrowstyle='->',color=INK))
    ax.set(xlim=(-57,67),ylim=(-12,120))
    style(ax,'REVEAL STAND · seated card / centre section','Guest / front ← Y (mm) → Back','Z (mm)')
    fig.text(.5,.025,'Open funnel and blind tilted slot • broad base and supported outer walls • two Ø4 mm tape/clamp holes',
             ha='center',fontsize=10,color=INK)
    fig.tight_layout(rect=(0,.05,1,1))
    save(fig,'stand')


def main():
    OUT.mkdir(exist_ok=True)
    parts = {}
    for name,builder in [('card_v2',build_card),('deck_tray',build_tray),('reveal_stand',build_stand)]:
        mesh = mesh_from(builder(),name)
        mesh.export(OUT/f'{name}.stl')
        export_3mf(mesh,OUT/f'{name}.3mf')
        parts[name] = mesh
        print(f'{name}: {np.round(mesh.extents,3)} mm; solid volume {mesh.volume/1000:.2f} cm³')
    preview_card(parts['card_v2'])
    preview_tray(parts['deck_tray'],parts['card_v2'])
    preview_stand(parts['reveal_stand'],parts['card_v2'])
    print('Wrote six mesh exports and three previews. Run .venv/bin/python check_v2.py.')
    print('PLA, 0.2 mm layers, no supports; card back/base plates on bed. Suggested 3 walls, 15–20% infill.')
    print('Tray is manually indexed: load front seat first; lift 10 mm and advance after each pickup.')


if __name__ == '__main__':
    main()
