"""Claude Fable orchestrator for Dream-1: a director picks short prompts, a tester watches the cameras and re-plans.

    director (Fable 5.1, effort medium)  goal + what happened so far + the latest frames -> next short Dream-1 prompt
    tester   (Fable 5.1, effort low)     every CHECK_S seconds while a prompt runs: frames + station status ->
                                         on_track | off_track | done | unsafe | unclear

Two off_track verdicts in a row, a done, or a prompt running out of time go back to the director, which sends a new
prompt (a fresh Dream-1 episode, ~2 s ramp) or finishes. The loop and every safety stop are plain code: the arms stop
and go home on an unsafe verdict, a dead arm, a station error, Ctrl+C, or the prompt/time caps.

It drives the station agent through the API show_sequence.py uses (port 8091) and reads the frames Dream-1 saw from
its view page (port 8093), both tunnelled over SSH. The person at the emergency stop starts the agent first:
    ssh pah@10.103.0.133
    source ~/dream-env/bin/activate && dreamcli robot up --view      # 'dreamcli robot down' when done

From agent/ (ANTHROPIC_API_KEY in agent/.env, or exported):
    uv run python orchestrator.py --goal "move the wooden block at the end of the row to the other end"   # plan only
    uv run python orchestrator.py --goal "..." --live        # the arms move: hand on the emergency stop

Every decision is appended to agent/orchestrator_log.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")  # ANTHROPIC_API_KEY lives in agent/.env (gitignored)

HOST = os.environ.get("ROBOT_HOST", "pah@10.103.0.133")
API_PORT, VIEW_PORT = 8092, 8094  # local ends of the tunnels to the station's 8091 (agent API) and 8093 (view page)
API, VIEW = f"http://127.0.0.1:{API_PORT}", f"http://127.0.0.1:{VIEW_PORT}"
MODEL = "claude-fable-5-1"
CHECK_S = 4.0  # pause between tester checks (each check itself takes a few seconds)
LOG = Path(__file__).resolve().parent / "orchestrator_log.jsonl"

# Dream's filter refused "hold up ... to show it" twice on Nov 7; keep prompts to tabletop actions.
BANNED = re.compile(r"\b(show|showing|hold(ing)? (it |them )?up|reveal|present|camera|audience|person|face the)\b", re.I)
MAX_PROMPT_CHARS = 120

DIRECTOR_SYSTEM = """You direct Dream-1, a vision-language-action model driving two YAM robot arms with parallel grippers \
over a table. You never move the arms yourself: you choose the next instruction Dream-1 receives.

Dream-1 does best with one short, concrete sentence: one object named by colour and shape, one action, one \
destination it can see ("pick up the red block and place it to the right of the green block"). It does badly with \
vague, repeated or two-handed goals ("shuffle", "tidy up"). Break the goal into such single steps and send one at a \
time. If a step failed, change the wording (a clearer object name, a nearer or more visible destination, a smaller \
sub-step) rather than resending it. Never ask Dream-1 to hold something up, show or reveal it, or aim at a camera or \
a person: Dream's API refuses those.

Reply with JSON only. action "prompt" sends `prompt` for `seconds` (8-40; a single pick and place needs about 20). \
action "finish" when the goal is met. action "give_up" when the history shows it is not working (the same failure \
after three different wordings) or the scene makes it impossible."""

TESTER_SYSTEM = """You check a robot while it works. You get the latest camera frames from the robot (overhead and \
the two wrist cameras), the instruction it is executing, and the station's status. Judge only from what you see.

verdict:
- on_track: it is making progress on the instruction, or is plausibly about to.
- off_track: it is clearly doing something else, is stuck (same pose as before with no progress), grabbed the wrong \
object, or dropped the object.
- done: the instruction is visibly complete.
- unsafe: a person's hand or body is in the workspace, an arm is pressing on something that is not the object, \
anything fell off the table, or the arms are colliding with each other. Prefer unsafe when unsure about safety.
- unclear: the frames do not let you tell.
Put what you see in `seen` (one sentence) and, for off_track, a better instruction in `suggestion`."""

DIRECTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["prompt", "finish", "give_up"]},
        "prompt": {"type": "string"},
        "seconds": {"type": "integer"},
        "why": {"type": "string"},
    },
    "required": ["action", "prompt", "seconds", "why"],
    "additionalProperties": False,
}
TESTER_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["on_track", "off_track", "done", "unsafe", "unclear"]},
        "seen": {"type": "string"},
        "suggestion": {"type": "string"},
    },
    "required": ["verdict", "seen", "suggestion"],
    "additionalProperties": False,
}


class Stop(Exception):
    """A safety or limit stop: the arms are stopped and sent home, then the run ends."""


def log(kind: str, **data: object) -> None:
    entry = {"t": time.strftime("%H:%M:%S"), "kind": kind, **data}
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"[{entry['t']}] {kind}: " + "  ".join(f"{k}={v}" for k, v in data.items() if k != "frames"), flush=True)


# --- station ------------------------------------------------------------------------------------------------------

def http(method: str, url: str, body: dict | None = None, timeout: float = 70) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise Stop(f"{method} {url} refused ({e.code}): {e.read().decode(errors='replace')}") from None


def status() -> dict:
    return http("GET", f"{API}/status", timeout=10)


def check_station(s: dict) -> None:
    """The code-side safety checks, run before every tester call."""
    if s.get("state") == "error" or s.get("last_error"):
        raise Stop(f"station error: {s.get('last_error') or s.get('state')}")
    dead = [name for name, alive in (s.get("arms") or {}).items() if not alive]
    if dead:
        raise Stop(f"arm driver not answering: {', '.join(dead)} (check the e-stop and the arm power)")


def latest_frames() -> dict[str, str]:
    """The newest JPEG (base64) per camera from the last round trip Dream-1 made; {} before the first one."""
    try:
        trips = http("GET", f"{VIEW}/trips", timeout=5)
        if not trips:
            return {}
        trip = http("GET", f"{VIEW}/trip/{trips[-1]['n']}", timeout=10)
    except (Stop, urllib.error.URLError, TimeoutError):
        return {}
    return {key.removeprefix("video."): window[-1] for key, window in (trip.get("frames") or {}).items() if window}


def status_line(s: dict) -> str:
    keep = ("state", "prompt", "run_elapsed_s", "chunks", "stalls", "clamps", "arms", "last_error")
    return json.dumps({k: s.get(k) for k in keep})


# --- Claude ---------------------------------------------------------------------------------------------------------

client = anthropic.Anthropic()


def ask(system: str, schema: dict, effort: str, text: str, frames: dict[str, str]) -> dict:
    """One Fable call with the frames as images; returns the parsed JSON, or raises Stop on a refusal."""
    content: list[dict] = []
    for name, b64 in sorted(frames.items()):
        content.append({"type": "text", "text": f"camera: {name}"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
    content.append({"type": "text", "text": text})
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise Stop(f"Claude declined ({getattr(response.stop_details, 'category', None)})")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def direct(goal: str, history: list[dict], frames: dict[str, str]) -> dict:
    text = (f"Goal: {goal}\n\nWhat happened so far (oldest first):\n"
            + (json.dumps(history, indent=1) if history else "nothing yet; this is the first step")
            + "\n\nThe frames above are the robot's cameras now" + ("" if frames else " (none yet: no run so far)") + ".")
    for _ in range(2):
        d = ask(DIRECTOR_SYSTEM, DIRECTOR_SCHEMA, "medium", text, frames)
        if d["action"] != "prompt":
            return d
        problem = ("longer than %d characters" % MAX_PROMPT_CHARS if len(d["prompt"]) > MAX_PROMPT_CHARS
                   else "uses words Dream's filter refuses (show / hold up / reveal / camera / person)"
                   if BANNED.search(d["prompt"]) else "")
        if not problem:
            d["seconds"] = max(8, min(40, int(d["seconds"])))
            return d
        log("director_prompt_rejected", prompt=d["prompt"], problem=problem)
        text += f"\n\nYour prompt {d['prompt']!r} was rejected: it is {problem}. Give another."
    raise Stop("the director kept proposing prompts that cannot be sent")


def test(prompt: str, s: dict, frames: dict[str, str], previous: str) -> dict:
    text = (f"Instruction being executed: {prompt}\nStation status: {status_line(s)}\n"
            f"Your previous check said: {previous or 'nothing (first check)'}")
    return ask(TESTER_SYSTEM, TESTER_SCHEMA, "low", text, frames)


# --- the loop -------------------------------------------------------------------------------------------------------

def run(goal: str, max_prompts: int, max_total_s: float) -> None:
    s = status()
    if s.get("state") not in ("idle", "running"):
        raise Stop(f"the station agent is {s.get('state')}, not idle")
    check_station(s)
    history: list[dict] = []
    started = time.monotonic()
    running = s.get("state") == "running"
    for n in range(1, max_prompts + 1):
        d = direct(goal, history, latest_frames())
        log("director", step=n, **d)
        if d["action"] != "prompt":
            return
        if running:
            http("POST", f"{API}/prompt", {"prompt": d["prompt"]})  # a fresh episode with this prompt
        else:
            http("POST", f"{API}/start", {"prompt": d["prompt"]})
            running = True
        step_end, off, previous, outcome = time.monotonic() + d["seconds"], 0, "", "timed out"
        notes: list[str] = []
        while time.monotonic() < step_end:
            if time.monotonic() - started > max_total_s:
                raise Stop(f"total time cap of {max_total_s:.0f} s reached")
            time.sleep(CHECK_S)
            s = status()
            check_station(s)
            frames = latest_frames()
            if not frames:
                continue  # the episode is still ramping up
            t = test(d["prompt"], s, frames, previous)
            log("tester", step=n, **t)
            previous = f"{t['verdict']}: {t['seen']}"
            notes.append(previous)
            if t["verdict"] == "unsafe":
                raise Stop(f"tester: unsafe: {t['seen']}")
            if t["verdict"] == "done":
                outcome = "done"
                break
            off = off + 1 if t["verdict"] == "off_track" else 0
            if off >= 2:  # one off_track can be a frame mid-motion; two in a row is a pattern
                outcome = f"off track; tester suggests: {t['suggestion']}"
                break
        history.append({"prompt": d["prompt"], "seconds": d["seconds"], "outcome": outcome, "checks": notes[-4:]})
    raise Stop(f"prompt cap of {max_prompts} reached")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--goal", required=True, help="what the arms should achieve, in plain words")
    p.add_argument("--live", action="store_true", help="send prompts to the arms (without it: plan the first step only)")
    p.add_argument("--max-prompts", type=int, default=6)
    p.add_argument("--max-seconds", type=float, default=180)
    args = p.parse_args()

    if not args.live:
        d = direct(args.goal, [], {})
        log("plan_only", **d)
        print("\nNothing was sent to the robot. Add --live to run it (hand on the emergency stop).")
        return 0

    tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-N",
                               "-L", f"{API_PORT}:127.0.0.1:8091", "-L", f"{VIEW_PORT}:127.0.0.1:8093", HOST])
    time.sleep(2)
    code = 0
    try:
        log("start", goal=args.goal, max_prompts=args.max_prompts, max_seconds=args.max_seconds)
        run(args.goal, args.max_prompts, args.max_seconds)
        log("finished")
    except Stop as e:
        log("stop", reason=str(e))
        code = 1
    except KeyboardInterrupt:
        log("stop", reason="Ctrl+C")
        code = 130
    except urllib.error.URLError as e:
        log("stop", reason=f"no station agent on port 8091 ({e.reason}); start it with: dreamcli robot up --view")
        code = 1
    finally:
        try:
            http("POST", f"{API}/stop", timeout=30)
            http("POST", f"{API}/home", timeout=30)
            log("arms_home")
        except Exception as e:  # noqa: BLE001 - whatever failed, the operator must know the arms may not be home
            log("stop_failed", error=str(e), action="use the emergency stop or 'dreamcli robot down'")
        tunnel.terminate()
    return code


if __name__ == "__main__":
    sys.exit(main())
