"""Web app for the Robot Fortune Teller: the page, LiveKit room tokens, and the robot's cameras.

- Each visit gets a fresh LiveKit room with the `fortune-teller` agent dispatched into it. The LiveKit API
  secret stays on this machine: the browser only receives a short-lived join token.
- On start, it launches the camera relay on the station (station_cam_relay.py) and tunnels it here, so the
  page can show the arms' three cameras live; during a draw the relay shows what Dream-1 sees.
  On stop, it ends the relay so other programs (run.sh, the teleop dashboard) can use the cameras again.

Run (from agent/, with the agent running too: see start_web.sh):
    uv run python web_server.py            # then open http://127.0.0.1:8765
Env: ROBOT_HOST (default pah@10.103.0.133), ROBOT_CAMERAS=0 to skip the station cameras.
"""

import asyncio
import datetime
import os
import secrets
from pathlib import Path

import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from livekit import api

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
    room = f"fortune-{secrets.token_hex(4)}"
    guest = f"guest-{secrets.token_hex(3)}"
    async with api.LiveKitAPI(URL.replace("wss://", "https://"), KEY, SECRET) as lk:
        await lk.room.create_room(api.CreateRoomRequest(name=room, empty_timeout=120))
        await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=room))
    jwt = (
        api.AccessToken(KEY, SECRET)
        .with_identity(guest)
        .with_name("Guest")
        .with_ttl(datetime.timedelta(hours=1))
        .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
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
    app.on_startup.append(start_cameras)
    app.on_cleanup.append(stop_cameras)
    print(f"Fortune Teller web app on http://127.0.0.1:{PORT}  (agent '{AGENT_NAME}' must be running)")
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
