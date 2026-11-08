"""Warms a Dream-1 checkpoint on DreamAPI without the arms: sends saved camera frames + state until a chunk comes back.

A fine-tune's first start can take up to ~15 minutes. Warming it this way leaves the arms free for other runs.

    ~/dream-env/bin/python warm_api.py dream-1-ft:<id> <a copy of ~/.dreamlabs/last-run_inputs>
    ~/dream-env/bin/python warm_api.py dream-1-ft:<id> <inputs> --keep 300   # then one call every 300 s (keep warm)
"""
import base64
import io
import json
import sys
import time
import warnings

import dream
import numpy as np
from PIL import Image

warnings.filterwarnings("ignore")
CKPT, D = sys.argv[1], sys.argv[2]
KEEP = float(sys.argv[sys.argv.index("--keep") + 1]) if "--keep" in sys.argv else 0
rows = [json.loads(line) for line in open(f"{D}/inputs.jsonl") if line.strip()]
ins = [r for r in rows if r.get("kind") == "inputs"][:10]
ROLE = {"top": "video.overhead", "left": "video.wrist_l", "right": "video.wrist_r"}


def b64(path: str) -> str:
    im = Image.open(path).convert("RGB").resize((320, 176))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


ticks = [({ROLE[c]: b64(f"{D}/{r['frames'][c]['file']}") for c in ROLE}, r["state"], r["t_s"]) for r in ins]
def one_call() -> None:
    with dream.DreamClient() as client:
        client.policy({"checkpoint": CKPT})
        for frames, st, t in ticks:
            obs = {k: np.asarray(v, np.float32) for k, v in st.items() if k.startswith("state.")}
            obs["prompt"] = "can you shuffle these cards using both arms"
            client.set_observation(frames, obs, timestamp=t)
        client.get_action()


start = time.monotonic()
while time.monotonic() - start < 1800:
    try:
        with dream.DreamClient() as client:
            client.policy({"checkpoint": CKPT})
            for frames, st, t in ticks:
                obs = {k: np.asarray(v, np.float32) for k, v in st.items() if k.startswith("state.")}
                obs["prompt"] = "can you shuffle these cards using both arms"
                client.set_observation(frames, obs, timestamp=t)
            a = client.get_action()
            print(time.strftime("%H:%M:%S"), "MODEL READY", sorted(k for k in a if not isinstance(a[k], dict))[:6], flush=True)
            break
    except Exception as e:  # a cold start answers with an error or a timeout: keep asking
        print(time.strftime("%H:%M:%S"), "not ready yet:", type(e).__name__, str(e)[:160], flush=True)
        time.sleep(20)

while KEEP:  # keep the checkpoint loaded: one call per interval (no arm moves)
    time.sleep(KEEP)
    try:
        t0 = time.monotonic()
        one_call()
        print(time.strftime("%H:%M:%S"), f"keep-warm call ok ({time.monotonic() - t0:.1f} s)", flush=True)
    except Exception as e:
        print(time.strftime("%H:%M:%S"), "keep-warm call failed:", type(e).__name__, str(e)[:160], flush=True)
