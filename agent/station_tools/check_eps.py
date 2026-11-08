"""Summarises each saved episode of a LeRobot dataset: length, how far each arm moved, how far each gripper closed."""
import glob, json, sys
import numpy as np
import pyarrow.parquet as pq
d = sys.argv[1]
info = json.load(open(f"{d}/meta/info.json"))
names = info["features"]["observation.state"].get("names")
for f in sorted(glob.glob(f"{d}/data/*/*.parquet")):
    t = pq.read_table(f)
    s = np.array(t.column("observation.state").to_pylist(), float)
    ep = int(t.column("episode_index")[0].as_py())
    n = s.shape[1]
    # YAM layout: left arm 6 + left grip, right arm 6 + right grip (names tell, else assume)
    if names and isinstance(names, list) and len(names) == n:
        idx = {k: i for i, k in enumerate(names)}
    R = list(range(0, 6)); Rg = 6; L = list(range(7, 13)); Lg = 13  # names: right_joint_0..5, right_gripper, left_...
    tl = np.abs(np.diff(s[:, L], axis=0)).sum(); tr = np.abs(np.diff(s[:, R], axis=0)).sum()
    flag = "STILL (arms barely moved)" if max(tl, tr) < 0.5 else ""
    print(f"episode {ep}: {len(s)/info['fps']:.1f}s  left moved {tl:.1f} rad, grip min {s[:,Lg].min():.2f} | "
          f"right moved {tr:.1f} rad, grip min {s[:,Rg].min():.2f}  {flag}")
