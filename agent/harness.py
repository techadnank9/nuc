"""The robot side of draw_card(): run Dream-1 on the station, then read which card landed in the stand.

Modes (env DRAW_MODE, or --mode on the command line):
- manual     no robot: a random card after a short pause (test the conversation)
             from the overhead camera, so place one in the stand by hand to test the reading
- robot      the arms move: Dream-1 drives them (hand on the e-stop).

Every attempt is appended to agent/attempts.csv.

Command line (from agent/):  uv run python harness.py --mode robot
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
# The two moves the Nov 7 fine-tune learned (exact training sentences; the model matches this text).
SHOW_TASK = "pick up the card from the handle and show it, then put it back down"
SHUFFLE_TASK = "can you shuffle these cards using both arms"
TASK = os.environ.get("DRAW_TASK", SHOW_TASK)
CHECKPOINT = os.environ.get("DRAW_CHECKPOINT", "")  # e.g. dream-1-ft:<account>-fortune; empty = base Dream-1
# NUCK-8's arms are YAMs (the 11:43 live run tracked its commands under body: yam). A YAM fine-tune drives
# the joints it learned, so it runs with --action-space joint (docs/running_your_model.md).
ACTION_SPACE = os.environ.get("DRAW_ACTION_SPACE", "joint" if CHECKPOINT else "")
BUDGET_S = int(os.environ.get("DRAW_BUDGET_S", "90"))  # ~20 s startup/homing + ~70 s of Dream-1
# Until the cards carry ArUco tag stickers, a completed run reveals a random card (READ_TAG=1 to use the camera).
READ_TAG = os.environ.get("READ_TAG", "0") == "1"
HERE = Path(__file__).resolve().parent
LOG = HERE / "attempts.csv"


def current_mode() -> tuple[str, bool]:
    """(mode, live): with the robot connected (DRAW_MODE=robot) the arms always move; manual = no robot at all."""
    mode = "robot" if os.environ.get("DRAW_MODE", "manual") != "manual" else "manual"
    return mode, mode == "robot"
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
    if await agent_status() is not None:
        return None  # the station agent holds the arms for us; it reports its own errors
    return await _preflight_classic()


async def _preflight_classic() -> str | None:
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


AGENT_API = "http://127.0.0.1:8091"  # the station agent (dreamcli robot up), on the station itself


async def _agent(method: str, path: str, body: dict | None = None, timeout: int = 20) -> dict | None:
    """One call to the station agent over SSH; None when no agent answers (then the page runs dreamcli itself)."""
    data = f"-H 'Content-Type: application/json' -d {shlex.quote(json.dumps(body))}" if body is not None else ""
    code, out = await _sh(SSH + [f"curl -s -m {timeout - 5} -X {method} {data} {AGENT_API}{path}"], timeout)
    try:
        return json.loads(out.strip().splitlines()[-1]) if code == 0 and out.strip() else None
    except (IndexError, json.JSONDecodeError):
        return None


async def agent_status() -> dict | None:
    """The station agent's status, or None when it is not running."""
    return await _agent("GET", "/status", timeout=10)


REMOTE_SHOW_DONE = "~/dream-env/bin/python ~/nuc-tools/show_done.py"


async def _show_rows() -> int:
    code, out = await _sh(SSH + [f"{REMOTE_SHOW_DONE} count"], 15)
    try:
        return int(out.strip().splitlines()[-1]) if code == 0 else 0
    except (IndexError, ValueError):
        return 0


async def _show_check(baseline: int) -> dict:
    code, out = await _sh(SSH + [f"{REMOTE_SHOW_DONE} check {baseline}"], 15)
    try:
        return json.loads(out.strip().splitlines()[-1]) if code == 0 else {}
    except (IndexError, json.JSONDecodeError):
        return {}


async def _run_on_agent(status: dict, task: str, budget: int, fresh: bool = False,
                        stop_at_end: bool = True, until_shown: bool = False) -> tuple[int, str]:
    """Run `task` on the running station agent: the arms stay powered and the model stays warm between commands.

    Idle agent: start a run (with our checkpoint). Running agent: change the prompt, which starts a fresh episode in
    about 2 s. After `budget` s the agent stops and the arms hold where they are (still on, ready for the next one).
    """
    if status.get("state") in ("running", "starting") and status.get("prompt") == task and not fresh:
        reply = status  # already switched to this task (the page sends it the moment the button is pressed)
    elif status.get("state") in ("running", "starting"):
        reply = await _agent("POST", "/prompt", {"prompt": task})
    else:
        reply = await _agent("POST", "/start", {"prompt": task, **({"checkpoint": CHECKPOINT} if CHECKPOINT else {})},
                             timeout=70)
    if not reply or reply.get("error"):
        return 1, f"the station agent refused: {(reply or {}).get('error', 'no answer')}"
    end = time.monotonic() + budget
    baseline = None
    while time.monotonic() < end:
        await asyncio.sleep(2)
        now = await agent_status() or {}
        if until_shown and now.get("state") == "running":
            # One show is enough: stop once an arm has held a card up and put it back down (show_done.py).
            if baseline is None:
                baseline = await _show_rows()
            elif (await _show_check(baseline)).get("done"):
                await _agent("POST", "/stop", timeout=70)
                await _agent("POST", "/home", timeout=70)
                return 0, "shown"
        if now.get("state") not in ("running", "starting"):
            err = now.get("last_error")
            return (1, f"Dream-1 run ended: {err}") if err else (0, "")
        if now.get("prompt") != task:  # another command took over: this one is done
            return 0, ""
    if stop_at_end:
        await _agent("POST", "/stop", timeout=70)
    return 124, ""


# One press of "Shuffle & show": the fine-tune's two trained instructions in a row (exact training sentences).
SHUFFLE_TASK_ = "can you shuffle these cards using both arms"
SHOW_TASK_ = "pick up the card from the handle and show it, then put it back down"
SHUFFLE_ROUNDS = [("Shuffle, round 1 of 3", SHUFFLE_TASK_, 30), ("Shuffle, round 2 of 3", SHUFFLE_TASK_, 30),
                  ("Shuffle, round 3 of 3", SHUFFLE_TASK_, 30)]
SHOW = [("Show the card", SHOW_TASK_, 150)]
ACTS = {"full": SHUFFLE_ROUNDS + SHOW, "shuffle": SHUFFLE_ROUNDS, "show": SHOW}
ACT = ACTS["full"]


async def run_act(on_step=None, name: str = "full", rounds: int = 1) -> dict:
    """Run one of ACTS on the station agent from the start position: "full" (shuffle three rounds, then show one
    card), "shuffle" or "show". Each step is a fresh episode; the arms stay connected throughout."""
    rounds = max(1, min(int(rounds), 5))
    shuffle = [(f"Shuffle, round {k} of {rounds}", SHUFFLE_TASK_, 30) for k in range(1, rounds + 1)]
    steps = {"full": shuffle + SHOW, "shuffle": shuffle, "show": SHOW}.get(name, shuffle + SHOW)
    global STOP_REQUESTED
    on_step = on_step or _noop
    STOP_REQUESTED = False
    if current_mode()[0] == "manual":
        await on_step("failed", "No robot connected (started with DRAW_MODE=manual)")
        return {"ok": False}
    if await agent_status() is None:
        await on_step("failed", "Start the station agent first (dreamcli robot up joint --view): the act needs the "
                                "arms to stay connected between steps")
        return {"ok": False}
    await on_step("release", "Handing the cameras to Dream-1")
    await relay("release")
    try:
        await on_step("policy", "Arms to the start position")
        if (await agent_status() or {}).get("state") in ("running", "starting"):
            await _agent("POST", "/stop", timeout=70)
        await _agent("POST", "/home", timeout=70)  # every act starts from the same pose
        for i, (label, task, secs) in enumerate(steps):
            if STOP_REQUESTED:
                await on_step("stop", "Stopped: arms hold, still connected")
                return {"ok": False}
            if task == SHOW_TASK_ and i > 0:
                # The show was recorded from home with the cards on the table. Stop the shuffle, then home: the home
                # move opens the grippers, so a card still held drops onto the table on the way. Then show, fresh.
                await on_step("policy", "Back to the start position before showing a card")
                await _agent("POST", "/stop", timeout=70)
                await _agent("POST", "/home", timeout=70)
            await on_step("policy", f"{label}: {secs} s")
            status = await agent_status() or {}
            code, out = await _run_on_agent(status, task, secs, fresh=True, stop_at_end=i == len(steps) - 1,
                                            until_shown=task == SHOW_TASK_)
            if out == "shown":
                await on_step("done", "Card shown and put back: arms home")
            if STOP_REQUESTED:
                await on_step("stop", "Stopped: arms hold, still connected")
                return {"ok": False}
            if code not in (0, 124):
                await on_step("failed", out or "Dream-1 stopped")
                return {"ok": False}
        await on_step("stop", "Done: arms hold, still connected")
    finally:
        await relay("resume")
    await on_step("done", {"full": "Shuffle & show finished", "shuffle": "Shuffle finished",
                           "show": "Show the card finished"}.get(name, "Finished"))
    return {"ok": True}


async def run_policy(task: str = TASK, budget: int = BUDGET_S) -> tuple[int, str]:
    """Run Dream-1 on `task` for `budget` s.

    With a station agent up (dreamcli robot up joint --view for our fine-tune), it runs there: no 20 s start-up, and
    the arms hold instead of switching off. Otherwise it starts dreamcli robot run and stops it with SIGINT after
    `budget` s (dreamcli then homes and releases).
    """
    if (status := await agent_status()) is not None:
        return await _run_on_agent(status, task, budget)
    cmd = ["dreamcli", "robot", "run", "--task", task]
    if CHECKPOINT:
        cmd += ["--checkpoint", CHECKPOINT]
    if ACTION_SPACE:
        cmd += ["--action-space", ACTION_SPACE]
    cmd.append("--view")  # dreamcli's own page on 127.0.0.1:8093: the relay mirrors its frames to the web app
    remote = (
        "source ~/dream-env/bin/activate && cd ~/physical-ai-hacks && "
        f"timeout -s INT {budget} {shlex.join(cmd)}"
    )
    # timeout's SIGINT is dreamcli's normal stop; exit 124 just means the budget ran out, which is expected.
    return await _sh(SSH + [remote], budget + 60)


async def reset_home(on_step=None) -> dict:
    """Stop whatever runs, reopen the arms if one faulted (agent reset), then move both arms to the start position."""
    global STOP_REQUESTED
    on_step = on_step or _noop
    STOP_REQUESTED = True  # ends a running Shuffle & show too
    status = await agent_status()
    if status is None:
        await stop_robot()  # dreamcli run: SIGINT homes and switches off by itself
        await on_step("done", "Stopped: Dream-1 homes the arms and switches them off")
        return {"ok": True}
    if status.get("state") in ("running", "starting"):
        await on_step("stop", "Stopping the current run")
        await _agent("POST", "/stop", timeout=70)
    for _ in range(30):  # wait out a stop or a home move in progress
        status = await agent_status() or {}
        if status.get("state") in ("idle", "error"):
            break
        await asyncio.sleep(1)
    arms_ok = all((status.get("arms") or {}).values())
    if status.get("state") == "error" or not arms_ok:
        await on_step("policy", "Resetting the arms (reopening their drivers), then going home")
        reply = await _agent("POST", "/reset", timeout=70)
    else:
        await on_step("policy", "Moving both arms to the start position")
        reply = await _agent("POST", "/home", timeout=70)
    for _ in range(40):
        status = await agent_status() or {}
        if status.get("state") in ("idle", "error"):
            break
        await asyncio.sleep(1)
    if (reply or {}).get("error") or status.get("state") == "error":
        why = (reply or {}).get("error") or status.get("last_error") or "unknown"
        await on_step("failed", f"Reset or home failed: {why}")
        return {"ok": False}
    await on_step("done", "Arms at the start position, still connected")
    return {"ok": True}


STOP_REQUESTED = False  # set by stop_robot(): ends a running Shuffle & show after the current step


async def stop_robot() -> None:
    """Stop Dream-1 now. Station agent: the arms hold where they are. dreamcli run: SIGINT (home, switch off)."""
    global STOP_REQUESTED
    STOP_REQUESTED = True
    if await agent_status() is not None:
        await _agent("POST", "/stop", timeout=70)
        return
    await _sh(SSH + ["pkill -INT -f 'bin/dreamcli robot run' || true"], 15)


async def run_command(task: str, seconds: int = 30, mode: str | None = None, on_step=None) -> dict:
    """Run Dream-1 on any task typed by the operator (no card logic). Same safety gates as draw_card()."""
    on_step = on_step or _noop
    mode = mode or current_mode()[0]
    seconds = max(30, min(int(seconds), 120))  # dreamcli needs ~10-20 s to start before Dream-1 answers
    if mode == "manual":
        await on_step("failed", "No robot connected (started with DRAW_MODE=manual): start the page with ./start_web.sh")
        return {"ok": False, "error": "no robot in manual mode"}
    await on_step("check", f'Command for Dream-1: "{task}"')
    if reason := await preflight():
        await on_step("failed", reason)
        return {"ok": False, "error": reason}
    await on_step("release", "Handing the cameras to Dream-1")
    await relay("release")
    try:
        agent = await agent_status() is not None
        await on_step("policy", f"Dream-1 runs for {seconds} s" if agent
                      else f"Arms moving home, then Dream-1 runs for {seconds} s")
        code, out = await run_policy(task=task, budget=seconds)
        await on_step("stop", "Dream-1 paused: arms hold, still connected" if agent
                      else "Dream-1 stopped: arms return home and switch off")
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
    mode = mode or current_mode()[0]
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
    elif mode == "robot":
        await on_step("check", "Checking the arms are free and both links are up")
        if reason := await preflight():
            result = {"ok": False, "error": reason}
        else:
            await on_step("release", "Handing the cameras to Dream-1")
            await relay("release")
            try:
                await on_step("stand", "Checking the reveal stand is empty" if READ_TAG else "Card tags not in use yet: skipping the stand check")
                if READ_TAG and (before := await read_card()).get("seen"):
                    # The stand must be empty before a draw, or an old card would count as this draw's success.
                    result = {"ok": False, "error": f"the stand is not empty (tags {before['seen']}): clear it first"}
                else:
                    await on_step("policy", f"Arms moving home, then Dream-1 draws the card ({BUDGET_S} s)")
                    code, out = await run_policy()
                    await on_step("stop", "Stopping Dream-1: arms return home and switch off")
                    if code not in (0, 124, 130):
                        result = {"ok": False, "error": explain(out)}
                    elif READ_TAG:
                        await on_step("read", "Reading the card's tag from the overhead camera")
                        result = await read_card()
                    else:
                        await on_step("read", "Run complete: the spirits choose the card (tag stickers not in use yet)")
                        result = {"ok": True, "card_id": random.choice(list(CARDS)), "seen": []}
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
    ap.add_argument("--mode", choices=["manual", "robot"], default=None)
    ap.add_argument("--read-only", action="store_true", help="only read the card in the stand (no Dream run)")
    a = ap.parse_args()
    print(json.dumps(asyncio.run(read_card() if a.read_only else draw_card(a.mode)), indent=2))
