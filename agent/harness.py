"""The robot side of draw_card(): run Dream-1 on the station, then read which card landed in the stand.

Modes (env DRAW_MODE, or --mode on the command line):
- manual     no robot: a random card after a short pause (test the conversation)
- robot-dry  Dream-1 runs for real but nothing is sent to the arms (--dry-run); the card is then read
             from the overhead camera, so place one in the stand by hand to test the reading
- robot      LIVE: the arms move. Refused unless ROBOT_LIVE=1 is also set, by the person at the e-stop.

Every attempt is appended to agent/attempts.csv.

Command line (from agent/):  uv run python harness.py --mode robot-dry
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import shlex
import time
from pathlib import Path

from cards import CARDS

HOST = os.environ.get("ROBOT_HOST", "pah@10.103.0.133")
TASK = os.environ.get("DRAW_TASK", "draw the front card from the deck and place it in the stand")
CHECKPOINT = os.environ.get("DRAW_CHECKPOINT", "")  # e.g. dream-1-ft:<account>-fortune; empty = base Dream-1
# NUCK-8's arms are YAMs (the 11:43 live run tracked its commands under body: yam). A YAM fine-tune drives
# the joints it learned, so it runs with --action-space joint (docs/running_your_model.md).
ACTION_SPACE = os.environ.get("DRAW_ACTION_SPACE", "joint" if CHECKPOINT else "")
BUDGET_S = int(os.environ.get("DRAW_BUDGET_S", "35"))  # homing + draw + place; tune on the real arm
HERE = Path(__file__).resolve().parent
LOG = HERE / "attempts.csv"
REMOTE_READER = "/tmp/station_read_card.py"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", HOST]


async def _sh(args: list[str], timeout: float) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        return 124, "timed out"
    return proc.returncode or 0, out.decode(errors="replace")


async def preflight() -> str | None:
    """Return a reason the robot cannot draw right now, or None if it can."""
    code, out = await _sh(
        SSH
        + [
            "pgrep -af 'bin/(dreamcli|dhk-collect)' && exit 3; "
            "for l in can_follower_l can_follower_r; do ip -br link show $l 2>/dev/null | grep -q UP || exit 5; done; "
            "exit 0"
        ],
        20,
    )
    return {
        0: None,
        3: "another program holds the arms: " + out.strip(),
        5: "an arm's CAN link is down",
        124: "the station did not answer",
    }.get(code, f"preflight failed ({code}): {out.strip()[-200:]}")


async def read_card() -> dict:
    code, out = await _sh(["scp", "-q", "-o", "BatchMode=yes", str(HERE / "station_read_card.py"),
                           f"{HOST}:{REMOTE_READER}"], 20)
    if code:
        return {"ok": False, "error": f"could not copy the reader: {out.strip()}"}
    code, out = await _sh(SSH + [f"~/physical-ai-hacks/.venv/bin/python {REMOTE_READER}"], 40)
    try:
        result = json.loads(out.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"ok": False, "error": f"reader gave no answer ({code}): {out.strip()[-200:]}"}
    known = [i for i in result.get("ids", []) if i in CARDS]
    return {"ok": bool(known), "card_id": known[0] if known else None, "seen": result.get("ids", [])}


async def run_policy(dry: bool) -> tuple[int, str]:
    """Start Dream-1 on the station and stop it with SIGINT after BUDGET_S (dreamcli then homes and releases)."""
    cmd = ["dreamcli", "robot", "run", "--task", TASK]
    if CHECKPOINT:
        cmd += ["--checkpoint", CHECKPOINT]
    if ACTION_SPACE:
        cmd += ["--action-space", ACTION_SPACE]
    if dry:
        cmd.append("--dry-run")
    remote = (
        "source ~/dream-env/bin/activate && cd ~/physical-ai-hacks && "
        f"timeout -s INT {BUDGET_S} {shlex.join(cmd)}"
    )
    # timeout's SIGINT is dreamcli's normal stop; exit 124 just means the budget ran out, which is expected.
    return await _sh(SSH + [remote], BUDGET_S + 60)


def _log(row: dict) -> None:
    new = not LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["t", "mode", "ok", "card_id", "seconds", "note"])
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in w.fieldnames})


async def draw_card(mode: str | None = None) -> dict:
    mode = mode or os.environ.get("DRAW_MODE", "manual")
    t0 = time.time()
    if mode == "manual":
        await asyncio.sleep(3)
        result = {"ok": True, "card_id": random.choice(list(CARDS))}
    elif mode in ("robot", "robot-dry"):
        if mode == "robot" and os.environ.get("ROBOT_LIVE") != "1":
            result = {"ok": False, "error": "live mode needs ROBOT_LIVE=1, set by the person at the e-stop"}
        elif reason := await preflight():
            result = {"ok": False, "error": reason}
        elif (before := await read_card()).get("seen"):
            # The stand must be empty before a draw, or an old card would count as this draw's success.
            result = {"ok": False, "error": f"the stand is not empty (tags {before['seen']}): clear it first"}
        else:
            code, out = await run_policy(dry=(mode == "robot-dry"))
            if code not in (0, 124, 130):
                result = {"ok": False, "error": f"dreamcli exited {code}: {out.strip()[-300:]}"}
            else:
                result = await read_card()
    else:
        result = {"ok": False, "error": f"unknown DRAW_MODE {mode!r}"}
    result["seconds"] = round(time.time() - t0, 1)
    _log({"t": time.strftime("%H:%M:%S"), "mode": mode, "note": result.get("error", ""), **result})
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=["manual", "robot-dry", "robot"], default=None)
    ap.add_argument("--read-only", action="store_true", help="only read the card in the stand (no Dream run)")
    a = ap.parse_args()
    print(json.dumps(asyncio.run(read_card() if a.read_only else draw_card(a.mode)), indent=2))
