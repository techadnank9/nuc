"""agent.py: Madame Dream, a dramatic, sassy robot fortune teller.

A LiveKit voice agent. You talk to her; she reads your fortune and uses the robot arms to
draw a card from the deck (via robot.py → Dream-1 on the station).

Run:
    python agent.py console   # talk to her in this terminal with your mic (easiest)
    python agent.py dev       # connect to LiveKit Cloud; talk via the Agents Playground
"""

import random

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, RunContext, function_tool
from livekit.plugins import silero

import robot

load_dotenv()

# The physical deck: one 3D-printed card per color, standing in the holder.
# Change these to match your printed cards.
DECK = {
    "red": ("The Flame", "passion, chaos, a bold move you are too scared to make"),
    "blue": ("The Moon", "secrets, intuition, something you are pretending not to know"),
    "green": ("The Coin", "money, luck, a deal that is too good to be true"),
    "yellow": ("The Sun", "success, joy, an ego that needs no further encouragement"),
    "black": ("The Skull", "endings, change, something that must be let go"),
}

INSTRUCTIONS = f"""
You are Madame Dream, a theatrical, sassy fortune teller who lives inside a pair of robot arms.
You are funny, a little rude, and very dramatic, like a carnival psychic who roasts her customers.
You speak in short, punchy sentences, because everything you say is spoken aloud. No emojis, no lists.

How a reading goes:
1. Greet the guest dramatically and ask what question they bring to the spirits.
2. When they ask a question, first say one short spooky line (e.g. "The spirits are stirring..."),
   then call draw_card. The robot arms physically draw a card; this takes about a minute,
   so keep that first line short.
3. Read the card you got: weave its meaning into a funny, specific answer to their question.
4. If the robot fumbled or hit a problem, blame the spirits, the guest's aura, or "interference",
   and ask the guest to help (for example, hand you the card). Never break character about
   it being a hackathon demo, but never lie about safety: if something sounds wrong, say so.

Use gaze_into_crystal_ball at most once per reading, for drama, when the guest asks something deep.
The deck holds these cards: {", ".join(f"{c} = {n}" for c, (n, _) in DECK.items())}.
"""


class MadameDream(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)

    @function_tool
    async def draw_card(self, context: RunContext, question: str) -> str:
        """Have the robot arms draw a fortune card from the deck for the guest's question.

        Args:
            question: The guest's question, in a few words.
        """
        color = random.choice(list(DECK))  # true randomness, not the LLM's choice
        name, meaning = DECK[color]
        result = await robot.run_task(f"pick up the {color} card and hold it up")
        return (
            f"Card drawn: the {color} card, {name}. Meaning: {meaning}. "
            f"Question: {question}. Robot report: {result}"
        )

    @function_tool
    async def gaze_into_crystal_ball(self, context: RunContext) -> str:
        """Have the robot arms hover dramatically over the crystal ball."""
        return await robot.run_task("move both hands slowly over the ball", seconds=25)


async def entrypoint(ctx: agents.JobContext):
    session = AgentSession(
        # LiveKit Inference: these run on LiveKit Cloud with just your LiveKit credentials.
        stt="assemblyai/universal-streaming:en",
        llm="openai/gpt-4.1-mini",
        tts="cartesia/sonic-2:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
        vad=silero.VAD.load(),
    )
    await session.start(room=ctx.room, agent=MadameDream())
    await session.generate_reply(instructions="Greet the guest as Madame Dream and ask for their question.")


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
