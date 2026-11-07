"""Web app for the Robot Fortune Teller: serves the page and hands out LiveKit room tokens.

Each visit to the page gets a fresh room with the `fortune-teller` agent dispatched into it.
The LiveKit API secret stays on this machine: the browser only receives a short-lived join token.

Run (from agent/, with the agent running in another terminal: uv run agent.py dev):
    uv run python web_server.py            # then open http://localhost:8765
"""

import os
import secrets
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv
from livekit import api

load_dotenv()
HERE = Path(__file__).resolve().parent
URL, KEY, SECRET = os.environ["LIVEKIT_URL"], os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]
AGENT_NAME = os.environ.get("AGENT_NAME", "fortune-teller")
PORT = int(os.environ.get("WEB_PORT", "8765"))


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
        .with_ttl(__import__("datetime").timedelta(hours=1))
        .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
        .to_jwt()
    )
    return web.json_response({"url": URL, "token": jwt, "room": room})


def main() -> None:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/token", token)
    print(f"Fortune Teller web app on http://localhost:{PORT}  (agent '{AGENT_NAME}' must be running)")
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
