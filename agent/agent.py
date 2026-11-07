"""The Robot Fortune Teller: a LiveKit voice agent on OpenAI Realtime through LiveKit Inference (no OpenAI key).

Run:  uv run agent.py dev     (then talk to it in the LiveKit Agents Playground)
Env:  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET in .env (model usage is billed to LiveKit Cloud)
      REALTIME_MODEL (default openai/gpt-realtime-2.1), REALTIME_VOICE (default marin), DRAW_MODE (default manual)
"""

import asyncio
import os

from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, RunContext, cli, function_tool, inference

from cards import CARDS, describe
from draw import draw_card as robot_draw

load_dotenv()

DECK = "\n".join(f"- {describe(i)}" for i in CARDS)

INSTRUCTIONS = f"""
You are Madame Dream, a warm, theatrical fortune teller whose hands are two robot arms.
Speak in short, evocative sentences. Never talk for more than about 15 seconds at a time.

The reading always goes like this, and stays short:
1. Welcome the guest and ask their first name and what they wish to know, in one breath.
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

Only use cards from this deck, and only the meaning given:
{DECK}
""".strip()


MAX_ATTEMPTS = 2  # per reading: one draw and one retry after the guest helps


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
            try:
                result = await robot_draw()
            except Exception as e:  # never let a robot error crash the conversation
                result = {"ok": False, "error": f"harness error: {e}"}
        if result.get("ok"):
            return f"The card drawn is {describe(result['card_id'])}"
        if "seen" not in result:  # the robot never ran (not ready, busy, config): not the guest's problem
            self.attempts -= 1
            return (
                "The robot could not draw right now (" + str(result.get("error")) + "). Stay in character: say the "
                "spirits need a moment, and ask the guest to wait while your assistant prepares the deck."
            )
        if self.attempts < MAX_ATTEMPTS:
            return "The draw failed: no card reached the stand. One retry is allowed after the guest helps."
        return "The draw failed again. No retry is left: end the reading gracefully."


server = AgentServer()


@server.rtc_session(agent_name="fortune-teller")
async def fortune_teller(ctx: JobContext) -> None:
    session = AgentSession(
        llm=inference.RealtimeModel(
            model=os.environ.get("REALTIME_MODEL", "openai/gpt-realtime-2.1"),
            voice=os.environ.get("REALTIME_VOICE", "marin"),
        ),
    )
    await session.start(agent=FortuneTeller(), room=ctx.room)
    await session.generate_reply(instructions="Welcome the guest warmly and ask their first name.")


if __name__ == "__main__":
    cli.run_app(server)
