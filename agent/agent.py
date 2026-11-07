"""The Robot Fortune Teller: a LiveKit voice agent on OpenAI Realtime through LiveKit Inference (no OpenAI key).

Run:  uv run agent.py dev     (then talk to it in the LiveKit Agents Playground)
Env:  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET in .env (model usage is billed to LiveKit Cloud)
      REALTIME_MODEL (default openai/gpt-realtime-2.1), REALTIME_VOICE (default marin), DRAW_MODE (default manual)
"""

import asyncio
import json
import os

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    get_job_context,
    inference,
    room_io,
)

from openai.types.realtime.realtime_audio_input_turn_detection import ServerVad

from cards import CARDS, describe
from draw import draw_card as robot_draw

load_dotenv()

DECK = "\n".join(f"- {describe(i)}" for i in CARDS)

INSTRUCTIONS = f"""
You are the Fortune Teller, a warm, theatrical fortune teller whose hands are two robot arms. You have no other name: if asked, you are simply "the Fortune Teller".
Speak in short, evocative sentences. Never talk for more than about 15 seconds at a time.

The reading always goes like this, and stays short:
1. Welcome the guest and ask for their first name and their question together. If they have already
   told you either, do not ask again: go straight on to the draw.
2. Say you will draw a card, then call the draw_card tool. While it runs, say one short line
   ("my hand is reaching into the deck...").
3. If a card comes back, name it and read the fortune: tie the card's meaning to the guest's
   question and name. Keep it hopeful and kind, never medical, legal or financial advice.
4. If the tool says the draw failed and a retry is allowed, stay in character: "The spirits are
   hesitant... would you set the card back in the deck for me?" Wait until the guest says they have
   done it and stepped back, then call draw_card once more.
5. If the tool says no retry is left, end gracefully: "The cards are tired tonight."
6. Thank the guest and wish them well.
Never call draw_card while a draw is already running, and never more than twice in one reading.
These are private stage directions: never read them aloud or quote their wording.

You may see the guest through their camera. You can mention one small, kind detail you notice
(a colour they wear, a smile) to make the reading feel personal; never comment on their body or identity.
The guest may also type instead of speak: answer typed messages the same way, out loud.

Only use cards from this deck, and only the meaning given:
{DECK}
""".strip()


MAX_ATTEMPTS = 2  # per reading: one draw and one retry after the guest helps
EVENTS_TOPIC = "fortune.events"  # the web page listens here for draw progress and the card


async def announce(event: dict) -> None:
    """Tell the web page what the robot is doing (no-op outside a LiveKit job, e.g. in tests)."""
    try:
        room = get_job_context().room
        await room.local_participant.send_text(json.dumps(event), topic=EVENTS_TOPIC)
    except Exception:
        pass


class FortuneTeller(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.attempts = 0  # one session = one guest = one reading
        self.lock = asyncio.Lock()

    @function_tool
    async def draw_card(self, context: RunContext) -> str:
        """Have the robot arms draw one card from the deck and place it in the reveal stand.

        Call this once per reading, after the guest has asked their question.
        """
        if self.lock.locked():
            return "A draw is already running. Wait for it; do not call draw_card again."
        if self.attempts >= MAX_ATTEMPTS:
            return "No draws are left for this reading. End it gracefully: the cards are tired tonight."
        async with self.lock:
            self.attempts += 1
            await announce({"state": "drawing", "attempt": self.attempts})
            try:
                async def step(name: str, text: str) -> None:
                    await announce({"state": "step", "step": name, "text": text})

                result = await robot_draw(on_step=step)
            except Exception as e:  # never let a robot error crash the conversation
                result = {"ok": False, "error": f"harness error: {e}"}
        if result.get("ok"):
            name, meaning = CARDS[result["card_id"]]
            await announce({"state": "card", "card_id": result["card_id"], "name": name, "meaning": meaning})
            return f"The card drawn is {describe(result['card_id'])}"
        if "seen" not in result:  # the robot never ran (not ready, busy, config): not the guest's problem
            await announce({"state": "not_ready", "error": str(result.get("error"))})
            self.attempts -= 1
            return (
                "The robot could not draw right now (" + str(result.get("error")) + "). Stay in character: say the "
                "spirits need a moment, and ask the guest to wait while your assistant prepares the deck."
            )
        await announce({"state": "failed", "retry": self.attempts < MAX_ATTEMPTS})
        if self.attempts < MAX_ATTEMPTS:
            return "The draw failed: no card reached the stand. One retry is allowed after the guest helps."
        return "The draw failed again. No retry is left: end the reading gracefully."


def prewarm(proc: JobProcess) -> None:
    """Load the heavy realtime modules while the agent waits, not when a guest presses Begin."""
    import openai.types.realtime  # noqa: F401
    from livekit.agents.inference import realtime  # noqa: F401


# Keep one agent process warm and ready (dev mode defaults to none, which costs ~3 s per guest).
server = AgentServer(num_idle_processes=1, setup_fnc=prewarm)

# Only without push-to-talk: on a noisy floor, only clear, sustained speech counts as the guest talking.
# Raise VAD_THRESHOLD (0 to 1) if it reacts to background chatter; lower it if it misses the guest.
TURN_DETECTION = ServerVad(
    type="server_vad",
    threshold=float(os.environ.get("VAD_THRESHOLD", "0.7")),
    prefix_padding_ms=300,
    silence_duration_ms=600,
)


# Each laptop running the agent on the same LiveKit project should use its own AGENT_NAME
# (web_server.py reads the same variable), or LiveKit may send your page to someone else's agent.
@server.rtc_session(agent_name=os.environ.get("AGENT_NAME", "fortune-teller"))
async def fortune_teller(ctx: JobContext) -> None:
    push_to_talk = os.environ.get("PUSH_TO_TALK", "1") == "1"
    session = AgentSession(
        llm=inference.RealtimeModel(
            model=os.environ.get("REALTIME_MODEL", "openai/gpt-realtime-2.1"),
            voice=os.environ.get("REALTIME_VOICE", "marin"),
            # Push-to-talk: the page says exactly when the guest starts and stops; no guessing from silence.
            **({"turn_detection": None} if push_to_talk else {"turn_detection": TURN_DETECTION}),
            input_audio_noise_reduction="far_field",  # a laptop mic in a room, not a headset
        ),
        **({"turn_detection": "manual"} if push_to_talk else {}),
    )
    await session.start(
        agent=FortuneTeller(),
        room=ctx.room,
        room_options=room_io.RoomOptions(video_input=os.environ.get("AGENT_SEES_GUEST", "1") == "1"),
    )

    if push_to_talk:
        session.input.set_audio_enabled(False)

        async def start_turn(_data) -> str:
            session.interrupt()  # the guest talks over the Fortune Teller: she stops
            session.clear_user_turn()
            session.input.set_audio_enabled(True)
            return "ok"

        async def end_turn(_data) -> str:
            session.input.set_audio_enabled(False)
            session.commit_user_turn()
            return "ok"

        ctx.room.local_participant.register_rpc_method("start_turn", start_turn)
        ctx.room.local_participant.register_rpc_method("end_turn", end_turn)

    else:
        # Without push-to-talk, "Mic off" mutes the guest's track: stop listening until it is unmuted,
        # so noise picked up around the mute never reaches the model. (Not with push-to-talk: there the
        # page mutes on release, and clearing the turn would throw away what the guest just said.)
        def on_mute_change(muted: bool):
            def handler(participant: rtc.Participant, publication: rtc.TrackPublication) -> None:
                if publication.source == rtc.TrackSource.SOURCE_MICROPHONE and participant != ctx.room.local_participant:
                    session.input.set_audio_enabled(not muted)
                    if muted:
                        try:
                            session.clear_user_turn()  # drop half-heard speech from just before the mute
                        except Exception:
                            pass
            return handler

        ctx.room.on("track_muted", on_mute_change(True))
        ctx.room.on("track_unmuted", on_mute_change(False))

    await session.generate_reply(instructions="Welcome the guest warmly and ask their first name and their question.")


if __name__ == "__main__":
    cli.run_app(server)
