"""Claude Fable orchestrator for Dream-1: a director picks short prompts, a tester watches the cameras and re-plans.

    director (Fable 5.1, effort medium)  goal + every attempt so far + the latest frames -> the next Dream-1 prompt
    tester   (Fable 5.1, effort low)     at the end of each attempt: frames + station status ->
                                         done | progress | failed | unsafe | unclear

Each attempt sends one prompt and leaves that Dream-1 episode running while the tester checks every few seconds
(--check-every, plus the ~5 s the check itself takes). progress / unclear keep it running untouched (resending would
restart the episode and lose a grasp in progress) for up to --attempt-seconds (45); failed goes straight back to the
director for a reworded prompt; done moves to the next step or finishes. The loop and every safety stop are plain code
(arms alive and station errors every 2 s): the arms stop and go home on an unsafe verdict, a dead arm, a station
error, Ctrl+C, or the attempt/time caps. The e-stop is still the real safety.

It drives the station agent through the API show_sequence.py uses (port 8091) and reads the frames Dream-1 saw from
its view page (port 8093), both tunnelled over SSH. The person at the emergency stop starts the agent first:
    ssh pah@10.103.0.133
    source ~/dream-env/bin/activate && dreamcli robot up --view      # 'dreamcli robot down' when done

On the station itself (no tunnel; uv builds a throwaway env, dream-env is untouched):
    export ANTHROPIC_API_KEY=...
    uv run --no-project --with "anthropic>=2.6" --with python-dotenv python orchestrator.py --goal "..." --live

From agent/ on a laptop (ANTHROPIC_API_KEY in agent/.env, or exported; needs SSH key access to the station):
    uv run python orchestrator.py --goal "move the wooden block at the end of the row to the other end"   # plan only
    uv run python orchestrator.py --goal "..." --live        # the arms move: hand on the emergency stop

Every decision is appended to agent/orchestrator_log.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
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


def _station_agent_here() -> bool:
    """True when this runs on the station itself (its agent answers on 8091 here): no SSH tunnel is needed."""
    try:
        urllib.request.urlopen("http://127.0.0.1:8091/status", timeout=2).close()
        return True
    except (urllib.error.URLError, OSError):
        return False


ON_STATION = _station_agent_here()
# On a laptop: local ends of SSH tunnels to the station's 8091 (agent API) and 8093 (view page).
API_PORT, VIEW_PORT = (8091, 8093) if ON_STATION else (8092, 8094)
API, VIEW = f"http://127.0.0.1:{API_PORT}", f"http://127.0.0.1:{VIEW_PORT}"
MODEL = "claude-fable-5-1"
POLL_S = 2.0  # how often the code-side checks (arms alive, station error) run during an attempt
LOG = Path(__file__).resolve().parent / "orchestrator_log.jsonl"

MAX_PROMPT_CHARS = 140

DIRECTOR_SYSTEM = """You direct Dream-1, a vision-language-action model driving two YAM robot arms with parallel grippers \
over a table. You never move the arms yourself: you choose the next instruction Dream-1 receives.

Prompts that worked on this robot today:
- "pick up the red block and put it beside the green one"
- "pick up the red card by the handle and hold it flipped up to show its back"
Write in that style: one sentence, "pick up the <colour> <object>" (+ where to grasp it, e.g. "by the handle"), then \
one result it can see ("put it beside the green one", "hold it flipped up to show its back"). Avoid vague or \
repeated goals ("shuffle", "tidy up"): break the goal into single steps like these and send one at a time.

The cards are 3D-printed with a handle sticking up: a flat card cannot be gripped, its handle can. Every prompt about \
a card says to pick it up by the handle: "pick up the grey card by the handle and put it on the other side of the \
orange card". Never send the goal's wording unchanged when it leaves the handle out.

Each prompt runs while a tester checks the cameras every few seconds; you are asked again when it reports done, \
failed, or the prompt ran out of time while still making progress. After failed or out of time, change one thing: a \
clearer object name, a grasp point, a nearer or more visible result, or a smaller step (just "pick up the grey card \
by the handle" first). After done, send the next step or finish. Keep calling each object by the same name in every \
prompt unless the tester showed that name picks the wrong object.

Reply with JSON only. action "prompt" sends `prompt` for the next attempt. action "finish" when the goal is met. \
action "give_up" when the history shows it is not working (the same failure after three different wordings) or the \
scene makes it impossible."""

TESTER_SYSTEM = """You check a robot while it works. You get the latest camera frames from the robot (overhead and \
the two wrist cameras), the instruction it is executing, and the station's status. Judge only from what you see.

You are called every few seconds while the instruction runs. verdict:
- done: the instruction is visibly complete.
- progress: not finished, but closer than before (approaching, grasping or carrying the right object).
- failed: it is doing something else, is stuck where the previous check saw it, grabbed the wrong object, or \
dropped the object.
- unsafe: a person's hand or body is in the workspace, an arm is pressing on something that is not the object, \
anything fell off the table, or the arms are colliding with each other. Prefer unsafe when unsure about safety.
- unclear: the frames do not let you tell.
Put what you see in `seen` (one sentence) and, for failed, a better instruction in `suggestion`."""

DIRECTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["prompt", "finish", "give_up"]},
        "prompt": {"type": "string"},
        "why": {"type": "string"},
    },
    "required": ["action", "prompt", "why"],
    "additionalProperties": False,
}
TESTER_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["done", "progress", "failed", "unsafe", "unclear"]},
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
    for _ in range(3):
        d = ask(DIRECTOR_SYSTEM, DIRECTOR_SCHEMA, "medium", text, frames)
        if d["action"] != "prompt":
            return d
        p = d["prompt"].lower()
        problem = (f"longer than {MAX_PROMPT_CHARS} characters" if len(p) > MAX_PROMPT_CHARS
                   else "about a card but does not say to pick it up by the handle"
                   if "card" in p and "handle" not in p else "")
        if not problem:
            return d
        log("director_prompt_rejected", prompt=d["prompt"], problem=problem)
        text += f"\n\nYour prompt {d['prompt']!r} was rejected: it is {problem}. Give another."
    raise Stop("the director kept proposing prompts that cannot be sent")


def test(prompt: str, s: dict, frames: dict[str, str], previous: str) -> dict:
    text = (f"Instruction being executed: {prompt}\nStation status: {status_line(s)}\n"
            f"The previous check said: {previous or 'nothing (first attempt)'}")
    return ask(TESTER_SYSTEM, TESTER_SCHEMA, "low", text, frames)


def refused(s: dict) -> str:
    """Dream's API refusing the prompt (DA-4000) ends the episode: the director rewords instead of a safety stop."""
    err = str(s.get("last_error") or "")
    return err if "refus" in err.lower() else ""


# --- the loop -------------------------------------------------------------------------------------------------------

def run(goal: str, max_attempts: int, max_total_s: float, attempt_s: float, check_every: float) -> None:
    s = status()
    if s.get("state") not in ("idle", "running"):
        raise Stop(f"the station agent is {s.get('state')}, not idle")
    check_station(s)
    history: list[dict] = []
    started = time.monotonic()
    running = s.get("state") == "running"
    for n in range(1, max_attempts + 1):
        d = direct(goal, history, latest_frames())
        log("director", attempt=n, **d)
        if d["action"] != "prompt":
            return
        if running:
            http("POST", f"{API}/prompt", {"prompt": d["prompt"]})  # a fresh episode; the arm goes on from where it is
        else:
            http("POST", f"{API}/start", {"prompt": d["prompt"]})
            running = True
        sent, previous, result = time.monotonic(), "", ""
        notes: list[str] = []
        while not result:
            # Wait check_every, running the code-side checks (arms alive, station error, refusal) every POLL_S.
            wake = time.monotonic() + check_every
            while time.monotonic() < wake and not result:
                time.sleep(min(POLL_S, max(0.0, wake - time.monotonic())) or POLL_S)
                s = status()
                if refusal := refused(s):
                    result = refusal
                    break
                check_station(s)
                if s.get("state") != "running":
                    result = f"the episode ended early (station {s.get('state')})"
            if time.monotonic() - started > max_total_s:
                raise Stop(f"total time cap of {max_total_s:.0f} s reached")
            if result:
                running = s.get("state") == "running"
                break
            frames = latest_frames()
            if not frames:
                continue  # the episode is still ramping up
            t = test(d["prompt"], s, frames, previous)
            log("tester", attempt=n, after_s=round(time.monotonic() - sent), **t)
            previous = f"{t['verdict']}: {t['seen']}"
            notes.append(previous)
            if t["verdict"] == "unsafe":
                raise Stop(f"tester: unsafe: {t['seen']}")
            if t["verdict"] == "done":
                result = "done"
            elif t["verdict"] == "failed":
                result = f"failed: {t['seen']} (tester suggests: {t['suggestion']})"
            elif time.monotonic() - sent >= attempt_s:
                result = f"out of time after {attempt_s:.0f} s while still {t['verdict']}: {t['seen']}"
            # progress / unclear: leave the episode running untouched
        log("attempt_result", attempt=n, result=result)
        history.append({"attempt": n, "prompt": d["prompt"], "result": result, "checks": notes[-3:]})
    raise Stop(f"attempt cap of {max_attempts} reached")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--goal", required=True, help="what the arms should achieve, in plain words")
    p.add_argument("--live", action="store_true", help="send prompts to the arms (without it: plan the first step only)")
    p.add_argument("--attempt-seconds", type=float, default=45, help="longest one prompt may run while making progress")
    p.add_argument("--check-every", type=float, default=4, help="pause between tester checks (each takes ~5 s more)")
    p.add_argument("--max-attempts", type=int, default=8)
    p.add_argument("--max-seconds", type=float, default=300)
    args = p.parse_args()

    if not args.live:
        d = direct(args.goal, [], {})
        log("plan_only", **d)
        print("\nNothing was sent to the robot. Add --live to run it (hand on the emergency stop).")
        return 0

    tunnel = None
    if not ON_STATION:
        tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-N",
                                   "-L", f"{API_PORT}:127.0.0.1:8091", "-L", f"{VIEW_PORT}:127.0.0.1:8093", HOST],
                                  start_new_session=True)  # Ctrl+C must not kill the tunnel the stop goes through
        time.sleep(2)
    code = 0
    try:
        log("start", goal=args.goal, attempt_s=args.attempt_seconds, max_attempts=args.max_attempts,
            max_seconds=args.max_seconds)
        run(args.goal, args.max_attempts, args.max_seconds, args.attempt_seconds, args.check_every)
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
        except Exception as e:  # noqa: BLE001 - whatever failed, try once more straight over SSH, then shout
            ok = not ON_STATION and subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", HOST,
                 "curl -sf -m 60 -X POST 127.0.0.1:8091/stop && curl -sf -m 60 -X POST 127.0.0.1:8091/home"],
                capture_output=True, timeout=130).returncode == 0
            if ok:
                log("arms_home", via="ssh fallback", first_error=str(e))
            else:
                log("stop_failed", error=str(e), action="THE ARMS MAY STILL BE RUNNING: hit the emergency stop now")
        if tunnel:
            tunnel.terminate()
    return code


if __name__ == "__main__":
    sys.exit(main())
