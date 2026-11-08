"""Builds the motion for docs/arm-sim/index.html from i2rt's real YAM + linear_4310 model.

model.xml is i2rt's own combined arm+gripper MJCF (i2rt.robots.utils.combine_arm_and_gripper_xml(YAM, LINEAR_4310)),
with mesh paths made local. For every move of the two Fortune Teller skills this script solves inverse kinematics
with MuJoCo, inside the real joint limits, along a straight tool path, and writes motion.json: the body tree
(to draw the meshes), 20 Hz joint samples, and the step list. Any target the arm cannot reach makes it fail loudly.

    cd docs/arm-sim && python3 gen_motion.py      # needs: pip install mujoco numpy
"""

import json
import math

import mujoco
import numpy as np

HZ = 20
m = mujoco.MjModel.from_xml_path("model.xml")
d = mujoco.MjData(m)
GRASP = m.site("grasp_site").id
GRIP_BODY = m.body("gripper").id
LO, HI = m.jnt_range[:6, 0], m.jnt_range[:6, 1]
MARGIN = 0.05  # same joint-limit margin the teleop uses

# World: right arm base at the origin, x forward (toward the guest), y to the arm's left, z up.
DOWN = np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]], float)  # gripper points down, fingers close along x


def twist(deg: float) -> np.ndarray:
    a = math.radians(deg)
    rz = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
    return rz @ DOWN


def axis_rot(ax, a):
    ax = np.array(ax, float); ax /= np.linalg.norm(ax)
    K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
    return np.eye(3) + math.sin(a) * K + (1 - math.cos(a)) * K @ K


def P(xy, z, tw, tilt=0.0):
    """A tool pose: grasp point at (x, y, z); twist about vertical; tilt tips the fingertips back toward the base.
    With the fingers straight down the YAM wrist (joint 4, +-90 deg) cannot hold the grasp point much above 15 cm,
    so every pose higher than that tilts back 35 deg."""
    perp = np.cross([0, 0, 1], [xy[0], xy[1], 0])
    return ([xy[0], xy[1], z], axis_rot(perp, math.radians(tilt)) @ twist(tw))


CARD = np.array([0.30, -0.10])   # card spot (card stands upright in a small rack, handle on top)
STAND = np.array([0.33, 0.17])   # display stand slot, card face toward the guest
HANDLE_Z = 0.107                 # handle centre height when the card is in its rack
STAND_GRIP_Z = 0.125             # grasp height when the card's bottom edge sits in the stand's slot
OPEN, HANDLE = 0.0, 0.0405       # finger slide: 0 = 96 mm open, 0.0405 = closed on the 14 mm handle


def ik(pos, rot, q0):
    q = q0.copy()
    for _ in range(300):
        d.qpos[:6] = q
        d.qpos[6:] = 0
        mujoco.mj_forward(m, d)
        ep = pos - d.site_xpos[GRASP]
        R = d.xmat[GRIP_BODY].reshape(3, 3)
        er = 0.5 * sum(np.cross(R[:, i], rot[:, i]) for i in range(3))
        if np.linalg.norm(ep) < 5e-4 and np.linalg.norm(er) < 5e-3:
            return q, True
        jp = np.zeros((3, m.nv)); jr = np.zeros((3, m.nv))
        mujoco.mj_jacSite(m, d, jp, None, GRASP)
        mujoco.mj_jacBody(m, d, None, jr, GRIP_BODY)
        J = np.vstack([jp[:, :6], 0.4 * jr[:, :6]])
        e = np.concatenate([ep, 0.4 * er])
        dq = J.T @ np.linalg.solve(J @ J.T + 1e-4 * np.eye(6), e)
        q = np.clip(q + dq, LO + MARGIN, HI - MARGIN)
    return q, False


def pose_of(q):
    d.qpos[:6] = q; d.qpos[6:] = 0
    mujoco.mj_forward(m, d)
    return d.site_xpos[GRASP].copy(), d.xmat[GRIP_BODY].reshape(3, 3).copy()


def slerp_rot(R0, R1, s):
    q0 = np.zeros(4); q1 = np.zeros(4)
    mujoco.mju_mat2Quat(q0, R0.flatten()); mujoco.mju_mat2Quat(q1, R1.flatten())
    if np.dot(q0, q1) < 0:
        q1 = -q1
    dot = min(1.0, float(np.dot(q0, q1)))
    th = math.acos(dot)
    q = q0 if th < 1e-6 else (math.sin((1 - s) * th) * q0 + math.sin(s * th) * q1) / math.sin(th)
    out = np.zeros(9); mujoco.mju_quat2Mat(out, q / np.linalg.norm(q))
    return out.reshape(3, 3)


HOME = np.array([0.0, 0.9, 0.9, 0.0, 0.0, 0.0])  # a neutral raised pose; Dream homes to its own pose
frames, steps = [], []
state = {"q": HOME.copy(), "grip": OPEN, "carry": False}


def ease(s):
    return s * s * (3 - 2 * s)


def step(skill, name, seconds, grip_text, how, target=None, grip=None, joint=None, carry=None):
    """Append one move: a straight tool path to target=(xyz, rot), a joint-space move to joint, or a pause."""
    n = max(1, int(round(seconds * HZ)))
    t0 = len(frames) / HZ
    q_start = state["q"].copy()
    g0, g1 = state["grip"], state["grip"] if grip is None else grip
    if carry is not None and carry:
        state["carry"] = True
    p0, R0 = pose_of(q_start)
    q = q_start
    for i in range(1, n + 1):
        s = ease(i / n)
        if target is not None:
            p = p0 + (np.array(target[0]) - p0) * s
            R = slerp_rot(R0, target[1], s)
            q, ok = ik(p, R, q)
            if not ok:
                raise SystemExit(f"cannot reach {name} at s={s:.2f}: {p}")
        elif joint is not None:
            q = q_start + (joint - q_start) * s
        frames.append([round(float(v), 4) for v in q] + [round(g0 + (g1 - g0) * s, 4), 1 if state["carry"] else 0])
    state["q"], state["grip"] = q, g1
    if carry is not None and not carry:
        state["carry"] = False
        frames[-1][-1] = 0
    steps.append({"skill": skill, "name": name, "t0": round(t0, 2), "t1": round(len(frames) / HZ, 2), "grip": grip_text, "how": how,
                  "q_deg": [round(math.degrees(v)) for v in q]})


T_CARD, T_STAND = -90, 0   # card stands with its face sideways in the rack; in the stand it faces the guest
step(1, "Start at home", 1.0, "open", "Arm still at home, gripper open. Every take starts from the same pose.")
step(1, "Reach above the card", 3.5, "open", "Base turns toward the card and the arm stretches forward, stopping about 12 cm above the handle. The gripper is tilted back: pointing straight down this high is past the wrist's limit.",
     target=P(CARD, 0.23, T_CARD + 50, -35))
step(1, "Line up", 1.5, "fully open", "Twist the gripper so the fingers sit either side of the black handle (a twist of 50 degrees here; keep it under 90).",
     target=P(CARD, 0.23, T_CARD, -35))
step(1, "Come straight down", 2.0, "fully open", "Lower onto the handle, straightening the gripper to point straight down for the last few centimetres.",
     target=P(CARD, HANDLE_Z, T_CARD, 0))
step(1, "Close all the way", 1.0, "closed hard", "Squeeze fully on the black handle every time. The model copies how far you close.", grip=HANDLE, carry=True)
step(1, "Pause", 0.5, "closed hard", "Stay still for half a second: grip first, then move.")
step(1, "Lift straight up", 2.0, "closed hard", "Raise about 15 cm without swinging sideways, tilting back as it rises.",
     target=P(CARD, 0.25, T_CARD, -35))
step(1, "Hold in the air", 2.5, "closed hard", "Stay still, card visible to the overhead camera. Stop the take here.")

step(2, "Start holding the card", 1.0, "closed hard", "Begin where skill 1 ended: card gripped by its handle, held in the air.")
step(2, "Move above the stand", 3.0, "closed hard", "Carry the card to about 12 cm above the stand's slot.",
     target=P(STAND, 0.25, T_STAND - 40, -35))
step(2, "Line up with the slot", 1.5, "closed hard", "Twist so the card is parallel to the slot, face toward the guest (40 degrees here).",
     target=P(STAND, 0.25, T_STAND, -35))
step(2, "Lower into the slot", 2.0, "closed hard", "Down until the card's bottom edge sits in the slot, straightening to vertical.",
     target=P(STAND, STAND_GRIP_Z, T_STAND, 0))
step(2, "Let go", 1.0, "fully open", "Open fully and wait a moment so the card settles.", grip=OPEN, carry=False)
step(2, "Lift away", 1.0, "open", "Straight up 8 cm so the fingers clear the handle.",
     target=P(STAND, 0.205, T_STAND, -20))
step(2, "Return home", 3.0, "open", "Back to the same home pose, then stop the take.", joint=HOME)

# Body tree for drawing: every body's parent, offset, joint axis, meshes.
tree = []
for b in range(1, m.nbody):
    j = m.body_jntadr[b]
    joint = None
    if j >= 0:
        joint = {"type": "slide" if m.jnt_type[j] == 2 else "hinge", "axis": m.jnt_axis[j].round(6).tolist(), "qadr": int(m.jnt_qposadr[j])}
    geoms = []
    for g in range(m.ngeom):
        if m.geom_bodyid[g] == b and m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
            mid = m.geom_dataid[g]
            geoms.append({"mesh": m.mesh(mid).name, "pos": m.geom_pos[g].round(7).tolist(), "quat": m.geom_quat[g].round(7).tolist()})
    tree.append({"name": m.body(b).name, "parent": int(m.body_parentid[b]) - 1, "pos": m.body_pos[b].round(7).tolist(),
                 "quat": m.body_quat[b].round(7).tolist(), "joint": joint, "geoms": geoms})

# MuJoCo moves mesh vertices to the mesh's own frame when it compiles; the page must apply the same shift.
mesh_frames = {}
for i in range(m.nmesh):
    spec_pos = m.mesh_pos[i].round(7).tolist(); spec_quat = m.mesh_quat[i].round(7).tolist()
    mesh_frames[m.mesh(i).name] = {"pos": spec_pos, "quat": spec_quat}
files = {"base": "base.stl", "link1": "link1.stl", "link2": "link2.stl", "link3": "link3.stl", "link4": "link4.stl", "link5": "link5.stl",
         "gripper": "gripper.stl", "tip_left": "tip_left.stl", "tip_right": "tip_right.stl"}
site = {"body": int(m.site_bodyid[GRASP]) - 1, "pos": m.site_pos[GRASP].round(6).tolist()}
limits = [[round(math.degrees(a)), round(math.degrees(b))] for a, b in m.jnt_range[:6]]
out = {"hz": HZ, "tree": tree, "files": files, "mesh_frames": mesh_frames, "grasp_site": site, "frames": frames, "steps": steps,
       "limits_deg": limits, "card": CARD.tolist(), "stand": STAND.tolist(), "handle_z": HANDLE_Z, "stand_grip_z": STAND_GRIP_Z}
json.dump(out, open("motion.json", "w"), separators=(",", ":"))
worst = min(min(math.degrees(min(f[i] - LO[i], HI[i] - f[i])) for i in range(6)) for f in frames)
print(f"{len(frames)} samples, {len(frames) / HZ:.1f} s, {len(steps)} moves; closest any joint gets to a limit: {worst:.1f} deg")
for s in steps:
    print(f"  skill {s['skill']}  {s['t0']:5.1f}-{s['t1']:5.1f}s  {s['name']:<24} joints(deg) {s['q_deg']}")
