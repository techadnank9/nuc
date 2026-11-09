"""Web app for the Robot Fortune Teller: the page, LiveKit room tokens, and the robot's cameras.

- Each visit gets a fresh LiveKit room with the `fortune-teller` agent dispatched into it. The LiveKit API
  secret stays on this machine: the browser only receives a short-lived join token.
- On start, it launches the camera relay on the station (station_cam_relay.py) and tunnels it here, so the
  page can show the arms' three cameras live; during a draw the relay shows what Dream-1 sees.
  On stop, it ends the relay so other programs (run.sh, the teleop dashboard) can use the cameras again.

Run: always with ./start_web.sh (from agent/), which starts this server and the agent together,
connected to the robot (the arms move on every command and draw); then open http://127.0.0.1:8765.
Env: ROBOT_HOST (default pah@10.103.0.133), ROBOT_CAMERAS=0 to skip the station cameras.
"""

import asyncio
import base64
import datetime
import json
import os
import secrets
from pathlib import Path

import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from livekit import api

load_dotenv()  # before harness: it reads DRAW_CHECKPOINT (our fine-tune) from agent/.env when imported

import harness  # noqa: E402
HERE = Path(__file__).resolve().parent
URL, KEY, SECRET = os.environ["LIVEKIT_URL"], os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]
AGENT_NAME = os.environ.get("AGENT_NAME", "fortune-teller")
PORT = int(os.environ.get("WEB_PORT", "8765"))
HOST = os.environ.get("ROBOT_HOST", "pah@10.103.0.133")
CAMERAS = os.environ.get("ROBOT_CAMERAS", "1") == "1"
RELAY = "http://127.0.0.1:8097"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6"]


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(HERE / "web" / "index.html")


async def token(_: web.Request) -> web.Response:
    """A fresh room + join token. The agent dispatch rides inside the token, so LiveKit sends the agent the
    moment the guest joins: no API calls here, the response is instant (saves ~0.7 s per reading)."""
    room = f"fortune-{secrets.token_hex(4)}"
    guest = f"guest-{secrets.token_hex(3)}"
    jwt = (
        api.AccessToken(KEY, SECRET)
        .with_identity(guest)
        .with_name("Guest")
        .with_ttl(datetime.timedelta(hours=1))
        .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
        .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=AGENT_NAME)]))
        .to_jwt()
    )
    return web.json_response({"url": URL, "token": jwt, "room": room,
                              "ptt": os.environ.get("PUSH_TO_TALK", "1") == "1"})


async def cam(request: web.Request) -> web.Response:
    role = request.match_info["role"]
    if role not in ("overhead", "left", "right"):
        raise web.HTTPNotFound()
    try:
        async with request.app["http"].get(f"{RELAY}/cam/{role}.jpg", timeout=aiohttp.ClientTimeout(total=2)) as r:
            if r.status != 200:
                return web.Response(status=204)
            return web.Response(body=await r.read(), content_type="image/jpeg", headers={"Cache-Control": "no-store"})
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return web.Response(status=503)


async def robot_status(request: web.Request) -> web.Response:
    try:
        async with request.app["http"].get(f"{RELAY}/status", timeout=aiohttp.ClientTimeout(total=2)) as r:
            data = await r.json()
            data["online"] = True
            return web.json_response(data)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return web.json_response({"online": False, "enabled": CAMERAS})


# ---- Direct commands to Dream-1 from the page (operator box) ----
EVENTS: list[dict] = []
COMMAND_LOCK = asyncio.Lock()


async def push_event(step: str, text: str) -> None:
    """Adds one line to the page's robot log (the page polls /robot/events)."""
    EVENTS.append({"id": EVENTS[-1]["id"] + 1 if EVENTS else 0, "step": step, "text": text})
    del EVENTS[:-200]  # keep the latest 200; ids keep counting, so the page's ?since= stays correct


def mode_info() -> dict:
    mode, live = harness.current_mode()
    return {"mode": mode, "live": live, "robot": mode == "robot", "model": harness.CHECKPOINT}


async def robot_command(request: web.Request) -> web.Response:
    body = await request.json()
    task = str(body.get("task", "")).strip()[:200]
    if not task:
        return web.json_response({"error": "Write what the arms should do first."}, status=400)
    if COMMAND_LOCK.locked():
        status = await harness.agent_status()
        if status is None or status.get("state") not in ("running", "starting"):
            return web.json_response({"error": "A command is already running. Stop it or wait for it to finish."},
                                     status=409)
        # Station agent running: switch to the new task right now (~2 s); the earlier command sees the new prompt
        # and finishes, then this one takes over the lock and keeps watching.
        await harness._agent("POST", "/prompt", {"prompt": task})
        await push_event("check", f'Switched to: "{task}"')

    async def run() -> None:
        async with COMMAND_LOCK:
            await harness.run_command(task, int(body.get("seconds", 30)), on_step=push_event)

    asyncio.create_task(run())
    return web.json_response({"started": True, **mode_info()})


async def robot_act(request: web.Request) -> web.Response:
    """One press: "full" (shuffle three rounds, then show one card), "shuffle" or "show" (harness.ACTS)."""
    try:
        body = await request.json()
        name, rounds = str(body.get("act", "full")), int(body.get("rounds", 1))
        card = str(body.get("card", "right"))
    except (ValueError, AttributeError, TypeError):
        name, rounds, card = "full", 1, "right"
    if COMMAND_LOCK.locked():
        return web.json_response({"error": "A command is running. Press Stop arms first, or wait."}, status=409)

    async def run() -> None:
        async with COMMAND_LOCK:
            await harness.run_act(on_step=push_event, name=name, rounds=rounds, card=card)

    asyncio.create_task(run())
    return web.json_response({"started": True, **mode_info()})


async def robot_staged(request: web.Request) -> web.Response:
    """Grab & place / Grab & show: confirmed grip first, then the move (harness.run_staged)."""
    if COMMAND_LOCK.locked():
        return web.json_response({"error": "A command is running. Press Stop arms first, or wait."}, status=409)
    try:
        body = await request.json()
    except ValueError:
        body = {}

    async def run() -> None:
        async with COMMAND_LOCK:
            await harness.run_staged(str(body.get("kind", "place")), str(body.get("color", "orange")),
                                     on_step=push_event)

    asyncio.create_task(run())
    return web.json_response({"started": True})


async def robot_orchestrate(request: web.Request) -> web.Response:
    """Run Ramsha's orchestrator on the station for a plain-words goal (harness.run_orchestrator)."""
    if COMMAND_LOCK.locked():
        return web.json_response({"error": "A command is running. Press Stop arms first, or wait."}, status=409)
    try:
        goal = str((await request.json()).get("goal", ""))
    except (ValueError, AttributeError):
        goal = ""

    async def run() -> None:
        async with COMMAND_LOCK:
            await harness.run_orchestrator(goal, on_step=push_event)

    asyncio.create_task(run())
    return web.json_response({"started": True})


async def robot_step(request: web.Request) -> web.Response:
    """The Robot panel's three steps: shuffle (fine-tune), grab (base Dream-1, white-taped handle), hold (lift, hold
    still, put down, home)."""
    if COMMAND_LOCK.locked():
        return web.json_response({"error": "The arms are busy. Wait, or press Stop arms."}, status=409)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    step, card, rounds = str(body.get("step", "")), str(body.get("card", "right")), int(body.get("rounds", 1) or 1)
    wide = bool(body.get("wide", True))
    finetuned = bool(body.get("finetuned", True))
    if step not in ("shuffle", "grabhold", "grab", "hold"):
        return web.json_response({"error": f"unknown step {step!r}"}, status=400)

    async def run() -> None:
        global CURRENT_STEP
        async with COMMAND_LOCK:
            CURRENT_STEP = step
            await push_event("check", {"shuffle": "Step 1: Shuffle" + (" (wider moves)" if wide else ""),
                                       "grabhold": "Step 2: Grab & hold up" + (" (fine-tune)" if finetuned else " (base Dream-1)"),
                                       "grab": "Grab",
                                       "hold": "Hold up"}[step])
            try:
                if step == "shuffle":
                    await harness.run_act(on_step=push_event, name="shuffle", rounds=rounds, wide=wide)
                elif step == "grabhold" and finetuned:
                    # The fine-tune's whole trained show, repeated until the guest names the card (or SHOW_REPEATS).
                    await harness.run_show_repeat(on_step=push_event)
                elif step == "grabhold":
                    await harness.run_grab_hold(card, on_step=push_event)
                elif step == "grab":
                    await harness.run_grab(card, on_step=push_event)
                elif step == "hold":
                    await harness.run_hold_up(card, on_step=push_event)
            finally:
                CURRENT_STEP = ""

    asyncio.create_task(run())
    return web.json_response({"started": True})


CURRENT_STEP = ""  # the Robot step running now (shuffle / grabhold / ...), for the tester's extra checks
CARD_UP_SYSTEM = """You look at a robot's camera frames (overhead and the two wrist cameras). Answer only from what you see.
card_up: true ONLY when a robot gripper holds a card clearly lifted well above the table (a hand's height or more),
held up so a person in front could read its face. A gripper closing on a card that still touches or nearly touches the
table is NOT card_up.
card_back_down: true when no gripper holds a card and a card that was moved now lies flat on the table again.
which: if card_up, the card's colour or what is printed on it, in a few words; else empty."""
CARD_UP_SCHEMA = {
    "type": "object",
    "properties": {"card_up": {"type": "boolean"}, "card_back_down": {"type": "boolean"}, "which": {"type": "string"}},
    "required": ["card_up", "card_back_down", "which"],
    "additionalProperties": False,
}


async def tester_watch(app: web.Application) -> None:
    """While the arms work, the tester looks at the cameras every few seconds and says what it sees, word for word,
    in the page's log. It only comments: it never moves or stops the arms."""
    import orchestrator  # the same tester the orchestrator uses (Fable, plus Astra with an OpenAI key)
    who = "Fable + Astra" if orchestrator.DIRECTOR == "astra" else "Fable"
    previous = ""
    while True:
        await asyncio.sleep(1)
        if not COMMAND_LOCK.locked() or not CAMERAS:
            previous = ""
            continue
        frames = {}
        for role in ("overhead", "left", "right"):
            try:
                async with app["http"].get(f"{RELAY}/cam/{role}.jpg", timeout=aiohttp.ClientTimeout(total=2)) as r:
                    if r.status == 200:
                        frames[role] = base64.b64encode(await r.read()).decode()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
        status = await harness.agent_status() or {}
        if not frames or status.get("state") not in ("running", "starting"):
            await asyncio.sleep(2)
            continue
        try:
            t = await asyncio.to_thread(orchestrator.test, status.get("prompt") or "(no prompt)", status, frames, previous)
        except Exception as e:  # noqa: BLE001 - a tester hiccup is shown, never raised
            await push_event("policy", f"Tester › (could not check: {str(e)[:120]})")
            await asyncio.sleep(5)
            continue
        previous = f"{t.get('verdict')}: {t.get('seen')}"
        if CURRENT_STEP == "grabhold":
            try:
                c = await asyncio.to_thread(orchestrator.ask, CARD_UP_SYSTEM, CARD_UP_SCHEMA, "low",
                                            "Is a gripper holding a card up right now?", frames)
            except Exception:  # noqa: BLE001 - the regular check above still runs
                c = {}
            if c.get("card_up") and not harness.TESTER_CARD_UP:
                harness.TESTER_CARD_UP = True
                await push_event("done", f"Tester › CARD UP: {c.get('which') or 'a card'} is held up for the guest")
            elif c.get("card_back_down") and harness.TESTER_CARD_UP:
                harness.TESTER_SHOW_DONE = True
        step = {"done": "done", "failed": "failed", "unsafe": "failed"}.get(t.get("verdict"), "policy")
        await push_event(step, f"Tester › ({who}) {str(t.get('verdict')).upper()}: {t.get('seen')}"
                               + (f" Suggests: {t['suggestion']}" if t.get("verdict") == "failed" and t.get("suggestion") else ""))
        await asyncio.sleep(1 if CURRENT_STEP == "grabhold" else 4)  # watch closely while a card may go up


STATION: dict = {"up": None, "state": None}  # the station agent, polled every 3 s for the page's buttons


async def station_watch() -> None:
    while True:
        st = await harness.agent_status()
        if st is not None and not STATION.get("up"):
            await harness.relay("release")  # the station agent owns the cameras: show what Dream-1 sees
        STATION.update(up=st is not None, state=(st or {}).get("state"))
        await asyncio.sleep(3)


async def start_watch(app: web.Application) -> None:
    async def hook(text: str) -> None:
        await push_event("policy", text)
    harness.PROMPT_HOOK = hook
    app["watch"] = asyncio.create_task(tester_watch(app))
    app["station"] = asyncio.create_task(station_watch())


async def robot_station(request: web.Request) -> web.Response:
    """Start (or restart) the station agent on the robot from the page."""
    if COMMAND_LOCK.locked():
        return web.json_response({"error": "The arms are busy. Wait, or press Stop arms."}, status=409)
    try:
        restart = bool((await request.json()).get("restart"))
    except (ValueError, AttributeError):
        restart = False

    async def run() -> None:
        async with COMMAND_LOCK:
            await harness.station_up(restart, on_step=push_event)
            st = await harness.agent_status()
            STATION.update(up=st is not None, state=(st or {}).get("state"))

    asyncio.create_task(run())
    return web.json_response({"started": True})


async def robot_finish(_: web.Request) -> web.Response:
    """The guest named the card: Grab & hold up stops after the current show (the arms finish it and go home)."""
    harness.finish_show()
    await push_event("check", "The guest named the card: this is the last show")
    return web.json_response({"ok": True})


async def robot_home(_: web.Request) -> web.Response:
    """Reset & home: stop anything running, reopen faulted arms, move both arms to the start position."""
    await push_event("check", "Reset & home pressed")
    asyncio.create_task(harness.reset_home(on_step=push_event))
    return web.json_response({"started": True})


async def robot_stop(_: web.Request) -> web.Response:
    await push_event("stop", "Stop pressed: stopping Dream-1 now")
    await harness.stop_robot()
    return web.json_response({"stopped": True})


async def robot_events(request: web.Request) -> web.Response:
    since = int(request.query.get("since", "-1"))
    return web.json_response({"events": [e for e in EVENTS if e["id"] > since], "busy": COMMAND_LOCK.locked(),
                              "station": STATION, **mode_info()})


async def start_cameras(app: web.Application) -> None:
    app["http"] = aiohttp.ClientSession()
    app["tunnel"] = None
    if not CAMERAS:
        return
    copy = await asyncio.create_subprocess_exec(
        "scp", "-q", "-o", "BatchMode=yes", str(HERE / "station_cam_relay.py"), f"{HOST}:/tmp/station_cam_relay.py")
    if await copy.wait() != 0:
        print("robot cameras: station unreachable, the page will show them as offline")
        return
    start = await asyncio.create_subprocess_exec(*SSH, HOST, (
        "tmux has-session -t relay 2>/dev/null || "
        "tmux new -d -s relay '~/physical-ai-hacks/.venv/bin/python /tmp/station_cam_relay.py'"))
    await start.wait()
    app["tunnel"] = await asyncio.create_subprocess_exec(
        *SSH, "-o", "ExitOnForwardFailure=yes", "-N", "-L", "8097:127.0.0.1:8097", HOST)
    print("robot cameras: relay started on the station and tunnelled to 127.0.0.1:8097")


async def stop_cameras(app: web.Application) -> None:
    await app["http"].close()
    if app.get("tunnel"):
        app["tunnel"].terminate()
    if CAMERAS:
        stop = await asyncio.create_subprocess_exec(*SSH, HOST, "tmux kill-session -t relay 2>/dev/null; true")
        await stop.wait()
        print("robot cameras: relay stopped, the cameras are free for other programs")


def main() -> None:
    # Browsers send every cookie set for localhost (often large auth tokens from other local projects):
    # accept big headers instead of failing with "Got more than 8190 bytes".
    app = web.Application(handler_args={"max_field_size": 65536, "max_line_size": 65536})
    app.router.add_get("/", index)
    app.router.add_get("/token", token)
    app.router.add_get("/cam/{role}.jpg", cam)
    app.router.add_get("/robot/status", robot_status)
    app.router.add_post("/robot/command", robot_command)
    app.router.add_post("/robot/stop", robot_stop)
    app.router.add_post("/robot/act", robot_act)
    app.router.add_post("/robot/home", robot_home)
    app.router.add_post("/robot/step", robot_step)
    app.router.add_post("/robot/finish", robot_finish)
    app.router.add_post("/robot/station", robot_station)
    app.router.add_post("/robot/staged", robot_staged)
    app.router.add_post("/robot/orchestrate", robot_orchestrate)
    app.router.add_get("/robot/events", robot_events)
    app.on_startup.append(start_cameras)
    app.on_startup.append(start_watch)
    app.on_cleanup.append(stop_cameras)
    print(f"Fortune Teller web app on http://127.0.0.1:{PORT}  (agent '{AGENT_NAME}' must be running)")
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
