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
MODE_FILE = HERE / ".robot_mode.json"  # written by web_server.py when the operator presses the live-mode button
LIVE_MINUTES = 15


def current_mode() -> tuple[str, bool]:
    """(mode, live) for this moment: the page's live switch if it is on and not expired, else the env."""
    mode = os.environ.get("DRAW_MODE", "manual")
    live_env = mode == "robot" and os.environ.get("ROBOT_LIVE") == "1"
    try:
        state = json.loads(MODE_FILE.read_text())
        if mode != "manual" and state.get("live") and state.get("until", 0) > time.time():
            return "robot", True
        if mode != "manual" and not state.get("live"):
            return "robot-dry", False
    except (OSError, ValueError):
        pass
    return mode, live_env


def set_live(on: bool) -> dict:
    """Turn live mode on (for LIVE_MINUTES) or off. Refused when the server runs without the robot."""
    if os.environ.get("DRAW_MODE", "manual") == "manual":
        return {"error": "This server was started without the robot (DRAW_MODE=manual)."}
    MODE_FILE.write_text(json.dumps({"live": on, "until": time.time() + LIVE_MINUTES * 60 if on else 0}))
    mode, live = current_mode()
    return {"mode": mode, "live": live}
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
    """Return a reason the robot cannot draw right now, or None if it can.

    If a previous Dream-1 run is still shutting down (homing, switching off), wait up to 25 s for it first.
    """
    for _ in range(25):
        code, _out = await _sh(SSH + ["pgrep -f 'bin/dreamcli robot run' >/dev/null"], 10)
        if code != 0:
            break
        await asyncio.sleep(1)
    return await _preflight_once()


def explain(out: str) -> str:
    """Turn dreamcli's last lines into what the operator should do."""
    if "USB 2" in out or "too slow for" in out:
        cam = "right wrist" if "260322272283" in out else "left wrist" if "262122273310" in out else "overhead" if "261922273680" in out else "a"
        return (f"The {cam} camera is connected at slow USB 2 speed. Unplug it and plug it firmly into a blue USB 3 port, "
                "and give its cable slack so the arm cannot pull it.")
    if "did not open" in out:
        return "A camera did not open: check its USB cable is plugged in firmly, then try again."
    if "held by dreamcli" in out:
        return "The previous run was still shutting down: wait a few seconds and send again."
    if "file descriptor cannot be a negative integer" in out or "CanOperationError" in out:
        return ("The run lost a device mid-motion (usually a camera or CAN cable unplugged by the arm's movement). "
                "Check the cables on the arm, then try again.")
    return "dreamcli stopped with an error: " + out.strip()[-200:]


async def _preflight_once() -> str | None:
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


async def relay(action: str) -> None:
    """Tell the station's camera relay (station_cam_relay.py) to release or retake the cameras; no-op if it is not running."""
    await _sh(SSH + [f"curl -s -m 8 -X POST 127.0.0.1:8097/{action} >/dev/null 2>&1 || true"], 20)


async def run_policy(dry: bool, task: str = TASK, budget: int = BUDGET_S) -> tuple[int, str]:
    """Start Dream-1 on the station and stop it with SIGINT after `budget` s (dreamcli then homes and releases)."""
    cmd = ["dreamcli", "robot", "run", "--task", task]
    if CHECKPOINT:
        cmd += ["--checkpoint", CHECKPOINT]
    if ACTION_SPACE:
        cmd += ["--action-space", ACTION_SPACE]
    if dry:
        cmd.append("--dry-run")
    cmd.append("--view")  # dreamcli's own page on 127.0.0.1:8093: the relay mirrors its frames to the web app
    remote = (
        "source ~/dream-env/bin/activate && cd ~/physical-ai-hacks && "
        f"timeout -s INT {budget} {shlex.join(cmd)}"
    )
    # timeout's SIGINT is dreamcli's normal stop; exit 124 just means the budget ran out, which is expected.
    return await _sh(SSH + [remote], budget + 60)


async def stop_robot() -> None:
    """Stop a running Dream-1 run now: SIGINT is dreamcli's normal stop (arms go home, switch off, go limp)."""
    await _sh(SSH + ["pkill -INT -f 'bin/dreamcli robot run' || true"], 15)


async def run_command(task: str, seconds: int = 30, mode: str | None = None, on_step=None) -> dict:
    """Run Dream-1 on any task typed by the operator (no card logic). Same safety gates as draw_card()."""
    on_step = on_step or _noop
    mode, live_now = (mode, mode == "robot" and os.environ.get("ROBOT_LIVE") == "1") if mode else current_mode()
    live = mode == "robot"
    seconds = max(30, min(int(seconds), 120))  # dreamcli needs ~10-20 s to start before Dream-1 answers
    if mode == "manual":
        await on_step("failed", "The server runs without the robot (DRAW_MODE=manual): start it with DRAW_MODE=robot-dry or robot")
        return {"ok": False, "error": "no robot in manual mode"}
    await on_step("check", f'Command for Dream-1: "{task}"')
    if live and not live_now:
        await on_step("failed", "Live mode is off: press Turn on live mode first")
        return {"ok": False, "error": "ROBOT_LIVE not set"}
    if reason := await preflight():
        await on_step("failed", reason)
        return {"ok": False, "error": reason}
    await on_step("release", "Handing the cameras to Dream-1")
    await relay("release")
    try:
        await on_step("policy", (f"Arms moving home, then Dream-1 runs for {seconds} s" if live
                                 else f"Dream-1 dry run for {seconds} s: nothing is sent to the arms"))
        code, out = await run_policy(dry=not live, task=task, budget=seconds)
        await on_step("stop", "Dream-1 stopped: arms return home and switch off")
    finally:
        await relay("resume")
    ok = code in (0, 124, 130)
    await on_step("done" if ok else "failed", "Command finished" if ok else explain(out))
    return {"ok": ok}


def _log(row: dict) -> None:
    new = not LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["t", "mode", "ok", "card_id", "seconds", "note"])
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in w.fieldnames})


async def _noop(step: str, text: str) -> None:
    pass


async def draw_card(mode: str | None = None, on_step=_noop) -> dict:
    """Draw one card. on_step(step, text) is awaited at each stage, for the web page's action log.

    Steps, in order: check, stand, release, policy, stop, read, done (or failed).
    """
    mode, live_now = (mode, mode == "robot" and os.environ.get("ROBOT_LIVE") == "1") if mode else current_mode()
    t0 = time.time()
    if mode == "manual":
        for step, text, wait in [
            ("check", "Checking the arms are free and both links are up", 0.6),
            ("stand", "Checking the reveal stand is empty", 0.6),
            ("policy", "Dream-1 drawing: reach, grip the handle, lift, place (simulated)", 2.4),
            ("read", "Reading the card's tag from the overhead camera", 0.6),
        ]:
            await on_step(step, text)
            await asyncio.sleep(wait)
        result = {"ok": True, "card_id": random.choice(list(CARDS))}
    elif mode in ("robot", "robot-dry"):
        live = mode == "robot"
        await on_step("check", "Checking the arms are free and both links are up")
        if live and not live_now:
            result = {"ok": False, "error": "live mode needs ROBOT_LIVE=1, set by the person at the e-stop"}
        elif reason := await preflight():
            result = {"ok": False, "error": reason}
        else:
            await on_step("release", "Handing the cameras to Dream-1")
            await relay("release")
            try:
                await on_step("stand", "Checking the reveal stand is empty")
                if (before := await read_card()).get("seen"):
                    # The stand must be empty before a draw, or an old card would count as this draw's success.
                    result = {"ok": False, "error": f"the stand is not empty (tags {before['seen']}): clear it first"}
                else:
                    await on_step("policy", ("Arms moving home, then Dream-1 draws the card" if live
                                             else "Dream-1 running as a dry run: nothing is sent to the arms")
                                  + f" ({BUDGET_S} s)")
                    code, out = await run_policy(dry=not live)
                    await on_step("stop", "Stopping Dream-1: arms return home and switch off")
                    if code not in (0, 124, 130):
                        result = {"ok": False, "error": explain(out)}
                    else:
                        await on_step("read", "Reading the card's tag from the overhead camera")
                        result = await read_card()
            finally:
                await relay("resume")
    else:
        result = {"ok": False, "error": f"unknown DRAW_MODE {mode!r}"}
    result["seconds"] = round(time.time() - t0, 1)
    await on_step("done" if result.get("ok") else "failed",
                  f"Card drawn in {result['seconds']} s" if result.get("ok") else str(result.get("error") or "No card reached the stand"))
    _log({"t": time.strftime("%H:%M:%S"), "mode": mode, "note": result.get("error", ""), **result})
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=["manual", "robot-dry", "robot"], default=None)
    ap.add_argument("--read-only", action="store_true", help="only read the card in the stand (no Dream run)")
    a = ap.parse_args()
    print(json.dumps(asyncio.run(read_card() if a.read_only else draw_card(a.mode)), indent=2))
