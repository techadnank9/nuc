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
import datetime
import json
import os
import secrets
from pathlib import Path

import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from livekit import api

import harness

load_dotenv()
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
    return web.json_response({"url": URL, "token": jwt, "room": room})


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
    except (ValueError, AttributeError, TypeError):
        name, rounds = "full", 1
    if COMMAND_LOCK.locked():
        return web.json_response({"error": "A command is running. Press Stop arms first, or wait."}, status=409)

    async def run() -> None:
        async with COMMAND_LOCK:
            await harness.run_act(on_step=push_event, name=name, rounds=rounds)

    asyncio.create_task(run())
    return web.json_response({"started": True, **mode_info()})


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
    return web.json_response({"events": [e for e in EVENTS if e["id"] > since], "busy": COMMAND_LOCK.locked(), **mode_info()})


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
    app.router.add_get("/robot/events", robot_events)
    app.on_startup.append(start_cameras)
    app.on_cleanup.append(stop_cameras)
    print(f"Fortune Teller web app on http://127.0.0.1:{PORT}  (agent '{AGENT_NAME}' must be running)")
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
