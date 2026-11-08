"""Offline prompt check: sends saved camera frames + arm state to Dream-1 with each prompt and summarises the planned
first chunk. No arm is opened or moved."""
import base64, io, json, os, sys, warnings
import numpy as np
from PIL import Image
import dream

warnings.filterwarnings("ignore")
D = os.path.expanduser("~/.dreamlabs/last-run_inputs")
rows = [json.loads(l) for l in open(f"{D}/inputs.jsonl") if l.strip()]
ins = [r for r in rows if r.get("kind") == "inputs"][:10]
ROLE = {"top": "video.overhead", "left": "video.wrist_l", "right": "video.wrist_r"}

def b64(path):
    im = Image.open(path).convert("RGB").resize((320, 176))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()

ticks = [({ROLE[c]: b64(f"{D}/{r['frames'][c]['file']}") for c in ROLE}, r["state"], r["t_s"]) for r in ins]
PROMPTS = sys.argv[1:]
with dream.DreamClient() as client:
    client.policy({"checkpoint": "dreamlabs/dream-1-beta"})
    first = True
    for p in PROMPTS:
        for trial in range(3):
            if not first:
                client.reset_episode()
            first = False
            for frames, st, t in ticks:
                obs = {k: np.asarray(v, np.float32) for k, v in st.items() if k.startswith("state.")}
                obs["prompt"] = p
                client.set_observation(frames, obs, timestamp=t)
            a = client.get_action()
            if trial == 0 and p == PROMPTS[0]:
                print("keys:", {k: np.asarray(v).shape for k, v in a.items() if not isinstance(v, dict)}, file=sys.stderr)
            out = {}
            for side, key in (("R", "q_r_chunk"), ("L", "q_l_chunk")):
                if key in a:
                    q = np.asarray(a[key], float)
                    arm = q[:, :6]; grip = q[:, 6] if q.shape[1] > 6 else None
                    out[side] = (float(np.abs(arm - arm[0]).max()), float(np.abs(np.diff(arm, axis=0)).sum()),
                                 None if grip is None else float(grip.min()))
            print(json.dumps({"prompt": p, "trial": trial, "plan": out}), flush=True)
