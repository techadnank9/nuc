"""Text-only run of the fortune teller: same instructions and draw_card tool, no microphone.

Run from agent/:  uv run python test_conversation.py
Uses the realtime model through LiveKit Inference in text mode, with the keys in .env.
"""

import asyncio
import os

from dotenv import load_dotenv
from livekit.agents import AgentSession, inference

load_dotenv()
import agent  # noqa: E402

TURNS = [
    "Hi, I'm Priya. Will my robotics startup take off this year?",
]
FAIL_TURNS = [
    "Hi, I'm Sam. Should I move to San Francisco?",
    "Okay, I put the card back in the deck and stepped back.",
]


async def run(turns: list[str], fail_first: bool) -> None:
    if fail_first:
        outcomes = iter([{"ok": False, "card_id": None, "seen": []}])

        async def flaky():
            try:
                return next(outcomes)
            except StopIteration:
                return {"ok": True, "card_id": 17}

        agent.robot_draw = flaky
    model = os.environ.get("REALTIME_MODEL", "openai/gpt-realtime-2.1")
    async with AgentSession(llm=inference.RealtimeModel(model=model, modalities=["text"])) as session:
        await session.start(agent.FortuneTeller())
        for text in turns:
            print(f"\nGUEST: {text}")
            result = await session.run(user_input=text)
            for ev in result.events:
                item = getattr(ev, "item", None)
                kind = type(item).__name__ if item else type(ev).__name__
                if kind == "FunctionCall":
                    print(f"  [tool call] {item.name}")
                elif kind == "FunctionCallOutput":
                    print(f"  [tool result] {item.output}")
                elif kind == "ChatMessage" and item.role == "assistant":
                    print(f"FORTUNE TELLER: {item.text_content}")


async def main() -> None:
    print("=== happy path ===")
    await run(TURNS, fail_first=False)
    print("\n=== first draw fails, guest helps ===")
    await run(FAIL_TURNS, fail_first=True)


if __name__ == "__main__":
    asyncio.run(main())
