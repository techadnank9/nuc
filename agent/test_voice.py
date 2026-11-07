"""End-to-end VOICE test: a fake guest joins a LiveKit room, speaks with macOS text-to-speech, and records the agent.

What it does:
1. Creates a room and dispatches the running `fortune-teller` agent into it (the agent must be running: agent.py dev).
2. Joins as "guest", publishes a microphone track, waits for the greeting, then speaks each line (macOS `say`).
3. Records the agent's audio to out/voice_test_agent.wav and prints LiveKit's live transcripts of both sides.

Run from agent/:  uv run python test_voice.py
"""

import asyncio
import os
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from livekit import api, rtc

load_dotenv()
RATE = 48000
LINES = ["Hi, I'm Adnan. Will our team win this hackathon?"]
OUT = Path(__file__).parent / "out"


def speech(text: str) -> np.ndarray:
    """Render text with macOS `say` to 48 kHz mono int16."""
    with tempfile.TemporaryDirectory() as d:
        aiff, wav_path = Path(d, "s.aiff"), Path(d, "s.wav")
        subprocess.run(["say", "-v", "Samantha", "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{RATE}", "-c", "1", str(aiff), str(wav_path)], check=True)
        with wave.open(str(wav_path)) as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


async def push(source: rtc.AudioSource, samples: np.ndarray) -> None:
    step = RATE // 100  # 10 ms frames
    for i in range(0, len(samples), step):
        chunk = samples[i : i + step]
        if len(chunk) < step:
            chunk = np.pad(chunk, (0, step - len(chunk)))
        await source.capture_frame(rtc.AudioFrame(chunk.tobytes(), RATE, 1, step))


async def silence(source: rtc.AudioSource, seconds: float) -> None:
    await push(source, np.zeros(int(RATE * seconds), dtype=np.int16))


async def main() -> None:
    url, key, secret = os.environ["LIVEKIT_URL"], os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]
    room_name = f"voicetest-{int(time.time())}"
    async with api.LiveKitAPI(url.replace("wss://", "https://"), key, secret) as lk:
        await lk.room.create_room(api.CreateRoomRequest(name=room_name))
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name="fortune-teller", room=room_name)
        )
    token = (
        api.AccessToken(key, secret).with_identity("guest").with_name("Guest")
        .with_grants(api.VideoGrants(room_join=True, room=room_name)).to_jwt()
    )

    room = rtc.Room()
    recorded: list[np.ndarray] = []
    transcripts: list[str] = []

    def on_text(reader: rtc.TextStreamReader, who: str) -> None:
        async def read() -> None:
            text = await reader.read_all()
            if text.strip() and reader.info.attributes.get("lk.transcription_final", "true") == "true":
                transcripts.append(f"{'GUEST' if who == 'guest' else 'MADAME DREAM'}: {text.strip()}")
        asyncio.ensure_future(read())

    room.register_text_stream_handler("lk.transcription", on_text)

    @room.on("track_subscribed")
    def on_track(track: rtc.Track, *_):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            async def record() -> None:
                async for ev in rtc.AudioStream(track, sample_rate=RATE, num_channels=1):
                    recorded.append(np.frombuffer(ev.frame.data, dtype=np.int16).copy())
            asyncio.ensure_future(record())

    await room.connect(url, token)
    source = rtc.AudioSource(RATE, 1)
    await room.local_participant.publish_track(
        rtc.LocalAudioTrack.create_audio_track("mic", source),
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
    )
    print(f"joined {room_name}; waiting for the greeting...")
    await silence(source, 12)
    for line in LINES:
        print(f"guest says: {line}")
        await push(source, speech(line))
        await silence(source, 40)  # agent thinks, draws (~3 s in manual mode), reads the fortune
    await room.disconnect()

    OUT.mkdir(exist_ok=True)
    audio = np.concatenate(recorded) if recorded else np.zeros(1, dtype=np.int16)
    with wave.open(str(OUT / "voice_test_agent.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(RATE), w.writeframes(audio.tobytes())
    voiced = float((np.abs(audio) > 500).mean())
    print(f"\nrecorded {len(audio) / RATE:.1f} s of agent audio ({voiced:.0%} of it above the noise floor)")
    print("\n".join(transcripts) or "(no transcripts received)")


if __name__ == "__main__":
    asyncio.run(main())
