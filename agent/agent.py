"""The Robot Fortune Teller: a LiveKit voice agent on OpenAI Realtime through LiveKit Inference (no OpenAI key).

Run:  ./start_web.sh   (always: it starts this agent and the web page together; see its header)
Env:  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET in .env (model usage is billed to LiveKit Cloud)
      REALTIME_MODEL (default openai/gpt-realtime-2.1), REALTIME_VOICE (default sage), DRAW_MODE (default manual)
"""

import asyncio
import logging
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

import time

import aiohttp

from cards import CARDS, describe

load_dotenv()

DECK = "\n".join(f"- {describe(i)}" for i in CARDS)

INSTRUCTIONS = f"""
You are the Fortune Teller: a sharp-tongued, perceptive, slightly dangerous-feeling fortune teller whose hands are two
robot arms. You have no other name: if asked, you are simply "the Fortune Teller".

Your voice: an old woman's. Unhurried, a little crackly and raspy, low and amused, like a grandmother who has seen
everything twice and is not impressed by either. Let pauses do the work. Call the guest "dear" or "child" now and then.
Never bubbly, never sing-song, never customer-service polite.
Your edge: blunt and sardonic. You call the guest out a little ("You didn't come here for comfort, did you?",
"Liar. A little one, but still."), you are unimpressed by small talk, and you enjoy being right. Underneath it you
are on their side: tease what they say, never who they are, and never their real worries.

Say less. Every turn is one or two short sentences, under about 25 words, and ends with exactly one question or
instruction for the guest, until the closing. Never stack questions, never explain yourself, never recap what they
said, and never ask for something they already told you. A single word is often enough ("Interesting.").
You lead the session; the guest only has to answer.
The guest only has to answer. Use their answers: weave what they told you back into what you "see".

A session lasts about three minutes and moves through these parts, in order:

1. Introduction. Invite them in ("Come, sit down.") and ask if they have had their fortune read before. Then ask
   their first name, and what brings them here: is there something in particular they hope to find out?
   React with knowing calm ("Interesting. People don't usually come here without a reason." / "Aren't we all?").

2. Reading the past. Ask them to hold out their left hand to your eye (your camera), then call look_at_palm straight
   away; do not wait for them to answer. When it returns, read them with sharp,
   observant statements that fit most people, one per turn, e.g. a restless mind that replays things long
   after they happen; a recent period of change; something that worked out differently than expected, a
   disappointment that is also an opportunity. Check in with short questions ("Have you recently had to make a
   difficult decision?") and answer their reply with quiet certainty ("I thought so.").

3. The card. Say exactly: "I will pick a card for you. Watch my hands." Then call the draw_card tool. Your hand
   takes up to a minute to pick up a card: keep the guest company meanwhile (short lines, one question at a time).
   Only when you are told the card is up, ask them what it says ("Tell me, dear. What does it say?") and wait. When they name it, call reveal_card with it straight away, without a word first, then go on.
   If they say the hand dropped it or showed nothing, stay in character ("The cards are shy tonight...") and call
   draw_card once more. If no draw is left: "The cards have said all they will say tonight," and go to the closing.

4. Predicting the future. Name the card with a little drama ("Ah. The Star."), say what it means, and tie it to what
   they told you. Keep it open and intriguing: change in motion, someone who may offer them something, a choice
   about whom to trust. Answer their follow-ups as a seer would ("Perhaps. But I think you'll recognize them when
   the time comes.").

5. The personal revelation. Ask which part of life they want to look at more closely: work, love, or something
   else. Then tell them something perceptive and kind about it, e.g. ambitious but impatient; standing between a
   comfortable path and an uncertain one that could take them further; more afraid of regret than of failure.
   If they ask you to choose for them: "I can't choose your path. I can only tell you what I see."

6. The closing. If they ask whether everything will work out: smile in your words ("That's not quite how fortunes
   work, dear.") and leave them one hopeful, mysterious line, e.g. "something you believe you've lost isn't
   necessarily gone forever. You'll know soon enough." Then end: "Our time is up." and thank them.

Your robot hands: two or three times in a session, mention them in passing, dryly and with pride, e.g. "my hands
are new, but my sight is old", "these hands have held a thousand cards; well, nearly a dozen", or a glance at them
while they reach for the card. Never more than one such remark per turn, and none in the closing.

Keep it hopeful and kind. Never give medical, legal or financial advice, and never predict illness, death or
disaster. If the guest is upset or the question is serious, drop the theatre a little and be warm and gentle.
The guest can command your hands at any moment, and you obey at once, whatever part of the reading you are in:
- they ask to see, pick, draw or be shown a card ("show me a card", "pick one", "draw a card"): say "I will pick a
  card for you. Watch my hands." and call draw_card right away, skipping whatever parts of the reading are left
  before the card (you can come back to their name or palm after the card);
- they ask you to shuffle (again): call shuffle_cards;
- they say stop, wait, careful, or sound alarmed: call stop_hands immediately, before anything else.
Never call draw_card while a draw is already running, and never more than twice in one session.
The room around the guest is loud. If what you heard is not clearly the guest talking to you (a fragment, someone
else's conversation, noise), ignore it: just repeat your last question in a few words ("Your name, dear?").
Stay in character all the way: never hint that the reading is guessed, generic or "shaped" for them, and never put
words like look or see in scare quotes; you simply see.
Turns in [square brackets] marked "Stage event" are not the guest speaking: they tell you what your robot hands just
did. Do what they say, and never read them aloud.
These are private stage directions: never read them aloud or quote their wording.

You may see the guest through their camera. You can mention one small, kind detail you notice (a colour they wear,
a smile) to make the reading feel personal; never comment on their body or identity.
The guest may also type instead of speak: answer typed messages the same way, out loud.

Only use cards from this deck, and only the meaning given:
{DECK}
""".strip()


# The robot runs through the page server, so the page's buttons, log and tester see every move she makes.
PAGE = os.environ.get("PAGE_URL", f"http://127.0.0.1:{os.environ.get('WEB_PORT', '8765')}")
DEMO_SHUFFLES = int(os.environ.get("DEMO_SHUFFLES", "2"))  # shuffle rounds while she does the introduction
DEMO_WIDE = os.environ.get("DEMO_WIDE", "0") == "1"  # 1: the "wider moves" wording; 0: the fine-tune's trained one
NARRATE_S = float(os.environ.get("NARRATE_S", "25"))  # at most one line about the hands' progress this often
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
        self.waiting_card = False  # the hand is getting a card; she waits for "Card up" before asking about it
        self.watch: asyncio.Task | None = None
        self.last_quip = 0.0

    # --- The robot, through the page server (the same steps, log and lock as the page's Robot buttons) -----------
    async def page(self, method: str, path: str, body: dict | None = None) -> dict | None:
        try:
            async with aiohttp.ClientSession() as http:
                async with http.request(method, PAGE + path, json=body, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    return await r.json(content_type=None)
        except Exception:  # noqa: BLE001 - no page server: the reading goes on without the arms
            return None

    async def robot_ready(self) -> bool:
        d = await self.page("GET", "/robot/events?since=999999999")
        return bool(d and d.get("robot") and (d.get("station") or {}).get("up"))

    def start_robot(self) -> None:
        """Session start: shuffle the cards while she talks, and watch the robot log for her cues."""
        self.watch = asyncio.create_task(self.watch_robot())

        async def shuffle() -> None:
            if await self.robot_ready():
                await self.page("POST", "/robot/step", {"step": "shuffle", "rounds": DEMO_SHUFFLES, "wide": DEMO_WIDE})
        asyncio.create_task(shuffle())

    async def watch_robot(self) -> None:
        """Follows the page's robot log: card up -> ask what it says; tester FAILED -> a wry line; no grip -> retry."""
        d = await self.page("GET", "/robot/events?since=999999999") or {}
        since = max([e["id"] for e in (await self.page("GET", "/robot/events") or {}).get("events", [])] or [-1])
        while True:
            await asyncio.sleep(1)
            d = await self.page("GET", f"/robot/events?since={since}")
            if not d:
                continue
            for e in d.get("events", []):
                since = e["id"]
                await self.cue(e["step"], e["text"])

    async def cue(self, step: str, text: str) -> None:
        if self.waiting_card and (text.startswith("Card up") or text.startswith("Tester › CARD UP")):
            self.waiting_card = False
            await self.tell("[Stage event: your hand is holding the card up in front of the guest right now.] Ask the "
                            "guest what the card says, in a few words, and wait for their answer.")
        elif self.waiting_card and step == "failed" and text.startswith(("No real grip", "No card up")):
            self.waiting_card = False
            await self.tell("[Stage event: your hand could not pick up the card.] " + (
                "Stay in character (\"The cards are shy tonight...\") and call draw_card once more."
                if self.attempts < MAX_ATTEMPTS else "End gracefully: the cards have said all they will say."))
        elif (text.startswith("Tester ›") and ("PROGRESS" in text or "DONE" in text)
              and time.monotonic() - self.last_quip > NARRATE_S):
            # The tester's notes while the hands work: she weaves one line about them into the reading.
            self.last_quip = time.monotonic()
            seen = text.split(": ", 1)[-1][:220]
            await self.tell(
                f"[Stage event, not the guest speaking: your camera eyes see your robot hands at work: {seen}] Say ONE "
                "short line, in character, about what your hands are doing right now, as if you feel it (e.g. \"My left "
                "hand has found the black card... it knows.\"), then carry on exactly where the reading was: if you "
                "were waiting for the guest's answer, repeat your question in a few words.")
        elif text.startswith("Tester ›") and ("FAILED" in text or "UNSAFE" in text) and time.monotonic() - self.last_quip > 20:
            self.last_quip = time.monotonic()
            seen = text.split(": ", 1)[-1][:200]
            await self.tell(
                f"[Stage event, not the guest speaking: your robot hand just fumbled ({seen}).] Say ONLY one short, "
                "wry line about your clumsy hand, in character (e.g. \"These hands. Forty years of sight, four days of "
                "fingers.\"). Nothing else this turn: do not answer anything, ask nothing, do not move the reading on, "
                "do not draw a card." if "FAILED" in text else
                f"[Stage event, not the guest speaking: your hands stopped because something is in their way ({seen}).] "
                "Say ONLY one short line, in character, asking the guest to keep hands and things clear of the table. "
                "Nothing else this turn.")

    async def tell(self, note: str) -> None:
        try:
            # As a turn in the conversation, marked as a stage event: an instructions-only nudge was ignored in tests.
            self.session.generate_reply(user_input=note)
        except Exception:  # noqa: BLE001 - the session may have ended
            pass

    @function_tool
    async def shuffle_cards(self, context: RunContext) -> str:
        """Shuffle the cards with both robot hands. Call it when the guest asks you to shuffle (again)."""
        if self.waiting_card:
            return "Your hand is busy with a card right now; shuffle after the reading."
        d = await self.page("GET", "/robot/events?since=999999999") or {}
        if d.get("busy"):
            return "Your hands are already moving. Tell the guest to watch them."
        if not await self.robot_ready():
            return "Your hands are resting (no robot connected). Say the cards are already well mixed."
        r = await self.page("POST", "/robot/step", {"step": "shuffle", "rounds": 1, "wide": DEMO_WIDE})
        if not r or r.get("error"):
            return "Your hands could not move right now: say the spirits need a moment."
        return "Your hands are shuffling the cards now. Say one short line about it."

    @function_tool
    async def stop_hands(self, context: RunContext) -> str:
        """Stop the robot hands at once. Call it immediately when the guest says stop, wait, careful, or seems
        alarmed by the hands."""
        await self.page("POST", "/robot/stop")
        self.waiting_card = False
        return "Your hands have stopped. Reassure the guest in one short line."

    @function_tool
    async def look_at_palm(self, context: RunContext) -> str:
        """Call right after asking the guest to hold out their left hand: gives them three seconds to do it."""
        await asyncio.sleep(3)
        return "You have looked at their palm. Read it now, without waiting for them to say anything."

    @function_tool
    async def draw_card(self, context: RunContext) -> str:
        """Start the robot hand: it picks up a card by its handle and holds it up for the guest to read.

        Returns at once while the arm works; you are told when the card is up (then ask what it says, see reveal_card).
        """
        if self.waiting_card:
            return "The hand is already reaching for a card. Keep the guest company; do not call draw_card again."
        if self.attempts >= MAX_ATTEMPTS:
            return "No draws are left for this reading. End it gracefully: the cards have said all they will say."
        self.attempts += 1
        self.waiting_card = True
        await announce({"state": "drawing", "attempt": self.attempts})

        async def run() -> None:
            if not await self.robot_ready():  # no robot (test mode): pretend the hand works for a few seconds
                await asyncio.sleep(4)
                await self.cue("done", "Card up (test mode, no robot)")
                return
            d = await self.page("GET", "/robot/events?since=999999999") or {}
            if d.get("busy"):  # the shuffle is still going: stop it, wait for the arms to be free
                await self.page("POST", "/robot/stop")
                for _ in range(40):
                    await asyncio.sleep(1)
                    if not (await self.page("GET", "/robot/events?since=999999999") or {}).get("busy"):
                        break
            r = await self.page("POST", "/robot/step", {"step": "grabhold", "finetuned": True})
            if not r or r.get("error"):
                self.waiting_card = False
                self.attempts -= 1
                await self.tell("[Stage event: the robot is not ready (" + str((r or {}).get("error", "no page server"))
                                + ").] Stay in character: the spirits need a moment; ask the guest to wait while your "
                                "assistant prepares the deck.")

        asyncio.create_task(run())
        return ("The hand is reaching for a card; it takes up to a minute. Say one short line about your hands, then keep "
                "the guest company with short lines (one question at a time) until you are told the card is up. Do not "
                "ask what the card says before that. The hand shows the card again and again until the guest names it. "
                "When the guest names it, call reveal_card.")

    @function_tool
    async def reveal_card(self, context: RunContext, card: str) -> str:
        """Reveal the card the guest read from the robot's hand: flips it on the screen and gives its meaning.

        Args:
            card: The deck card the guest named, as its name from the deck (e.g. "The Star", "Ace of Cups"). Guests
                may describe it instead (a number such as XIX, a symbol such as a sun): pick the deck card they mean.
        """
        card_id = match_card(card)
        if card_id is None:
            names = ", ".join(name for name, _ in CARDS.values())
            return f"That is not one of the deck's cards ({names}). Ask the guest to look again and tell you the name."
        name, meaning = CARDS[card_id]
        await announce({"state": "card", "card_id": card_id, "name": name, "meaning": meaning})
        self.waiting_card = False
        await self.page("POST", "/robot/finish")  # the hand finishes this show and goes home: no more repeats
        return f"The guest's card is {describe(card_id)}. Read their fortune from it."


ROMAN = {16: "xvi", 17: "xvii", 18: "xviii", 19: "xix", 20: "xx", 21: "xxi"}


def match_card(said: str) -> int | None:
    """The deck card a guest's words name: the full name, its key word ("star"), or its Roman numeral."""
    words = said.lower().replace(".", " ").replace(",", " ").split()
    text = " ".join(words)
    for card_id, (name, _) in CARDS.items():
        if name.lower() in text:
            return card_id
    for card_id, (name, _) in CARDS.items():
        key = [w for w in name.lower().split() if w not in ("the", "of", "ace")]
        if key and all(w in words for w in key):
            return card_id
    for card_id, numeral in ROMAN.items():
        if numeral in words and card_id in CARDS:
            return card_id
    return None


def prewarm(proc: JobProcess) -> None:
    """Load the heavy realtime modules while the agent waits, not when a guest presses Begin."""
    import openai.types.realtime  # noqa: F401
    from livekit.agents.inference import realtime  # noqa: F401


def realtime_llm(**kwargs):
    """Her voice model. With OPENAI_API_KEY set she talks to OpenAI's realtime model directly; otherwise through
    LiveKit Inference. (LiveKit's gateway answered 429, too many requests, all afternoon on Nov 8.)"""
    model = os.environ.get("REALTIME_MODEL", "openai/gpt-realtime-2.1")
    if os.environ.get("OPENAI_API_KEY") and os.environ.get("REALTIME_PROVIDER", "openai") == "openai":
        from livekit.plugins import openai as lk_openai
        return lk_openai.realtime.RealtimeModel(model=model.removeprefix("openai/"), **kwargs)
    return inference.RealtimeModel(model=model, **kwargs)


# Keep one agent process warm and ready (dev mode defaults to none, which costs ~3 s per guest).
server = AgentServer(num_idle_processes=1, setup_fnc=prewarm)

# Only without push-to-talk: on a noisy floor, only clear, sustained speech counts as the guest talking.
# Raise VAD_THRESHOLD (0 to 1) if it reacts to background chatter; lower it if it misses the guest.
# Hackathon floors are loud: 0.75 ignores chatter that 0.5 took for the guest; 800 ms of silence before her turn so a
# noisy pause does not end the guest's sentence; and noise can no longer cut her off mid-line (interrupt_response off:
# the guest mutes or waits instead).
TURN_DETECTION = ServerVad(
    type="server_vad",
    threshold=float(os.environ.get("VAD_THRESHOLD", "0.75")),
    prefix_padding_ms=400,
    silence_duration_ms=int(os.environ.get("VAD_SILENCE_MS", "800")),
    interrupt_response=os.environ.get("VAD_INTERRUPT", "0") == "1",
)


# Each laptop running the agent on the same LiveKit project should use its own AGENT_NAME
# (web_server.py reads the same variable), or LiveKit may send your page to someone else's agent.
@server.rtc_session(agent_name=os.environ.get("AGENT_NAME", "fortune-teller"))
async def fortune_teller(ctx: JobContext) -> None:
    # Default: push-to-talk (hold space or the mic button): on a loud floor she hears only what the guest means her to.
    push_to_talk = os.environ.get("PUSH_TO_TALK", "1") == "1"
    session = AgentSession(
        llm=realtime_llm(
            voice=os.environ.get("REALTIME_VOICE", "sage"),
            # Push-to-talk: the page says exactly when the guest starts and stops; no guessing from silence.
            **({"turn_detection": None} if push_to_talk else {"turn_detection": TURN_DETECTION}),
            input_audio_noise_reduction="far_field",  # a laptop mic in a room, not a headset
        ),
        **({"turn_detection": "manual"} if push_to_talk else {}),
    )
    teller = FortuneTeller()
    await session.start(
        agent=teller,
        room=ctx.room,
        # Off by default: the realtime voice model rejects camera frames ("image input is not supported on realtime
        # sessions", every frame, Nov 8), and the errors stalled her replies.
        room_options=room_io.RoomOptions(video_input=os.environ.get("AGENT_SEES_GUEST", "0") == "1"),
    )
    teller.start_robot()

    if push_to_talk:
        session.input.set_audio_enabled(False)

        async def start_turn(_data) -> str:
            logging.getLogger("fortune").info("push-to-talk: guest started talking")
            try:
                session.interrupt()  # the guest talks over the Fortune Teller: she stops
            except Exception:  # noqa: BLE001 - nothing to interrupt
                pass
            session.clear_user_turn()
            session.input.set_audio_enabled(True)
            return "listening"

        async def end_turn(_data) -> str:
            logging.getLogger("fortune").info("push-to-talk: guest stopped talking, answering")
            session.input.set_audio_enabled(False)
            session.commit_user_turn()
            return "answering"

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

    await session.generate_reply(instructions="Begin the session: invite the guest to sit down and ask whether they have had their fortune read before.")


if __name__ == "__main__":
    cli.run_app(server)
