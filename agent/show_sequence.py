"""Runs one Fortune Teller show on the arms as a timed sequence of Dream-1 prompts: shuffle the cards 4-5 times,
pick one card, show it, put it down, go home.

It drives the station agent (`dreamcli robot up`) through its local HTTP API (station port 8091: /start, /prompt,
/stop, /home, /status), tunnelled over SSH. The agent keeps the arms, cameras and IK open, so each new prompt only
costs a fresh episode and its ~2 s ramp instead of the ~20 s start-up of `dreamcli robot run`.

Before running, the person at the emergency stop starts the agent on the station:
    ssh pah@10.103.0.133
    source ~/dream-env/bin/activate && dreamcli robot up --view      # 'dreamcli robot down' when done
(Stop the Fortune Teller page and any recording first: only one program can hold the arms.)

Then, from agent/:
    python3 show_sequence.py --print      # show the plan only, nothing is sent
    python3 show_sequence.py              # run it; Ctrl+C stops the arms (they hold) and sends them home
    python3 show_sequence.py --rounds 5   # number of shuffle rounds (default 4)

Prompts follow Dream's guidance (one short concrete sentence, name the object by colour and shape, name the
result) and the wording that worked on NUCK-8 today ("pick up the grey card from the handle and hold it up high so
its back is completely visible", "can you shuffle these blocks using both arms").
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HOST = os.environ.get("ROBOT_HOST", "pah@10.103.0.133")
LOCAL_PORT = 8092
API = f"http://127.0.0.1:{LOCAL_PORT}"

SHUFFLE = "shuffle the grey cards on the table using both arms"
PICK_AND_SHOW = "pick up the grey card from the handle and hold it up high so its back is completely visible"
PUT_DOWN = "put the grey card back down on the table"


def plan(rounds: int) -> list[dict]:
    """Each step: what the Fortune Teller says (for the narrator or voice agent), the prompt, and how long it runs."""
    steps = []
    for i in range(rounds):
        steps.append({"say": "The cards must forget their order..." if i == 0 else "Again...",
                      "prompt": SHUFFLE, "seconds": 15})
    steps.append({"say": "Now the cards are ready. My hands will choose the one that is yours.",
                  "prompt": PICK_AND_SHOW, "seconds": 30})
    steps.append({"say": "(Reveal: the overhead camera reads the card while it is held up.)",
                  "prompt": None, "seconds": 6})
    steps.append({"say": "The card has spoken. I return it to the table.",
                  "prompt": PUT_DOWN, "seconds": 20})
    return steps


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=70) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise SystemExit(f"{method} {path} refused ({e.code}): {detail}") from None


def wait(seconds: float) -> None:
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        print(f"\r    {left:4.0f} s left ", end="", flush=True)
        time.sleep(min(1.0, left))
    print("\r" + " " * 20 + "\r", end="")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--rounds", type=int, default=4, help="shuffle rounds (default 4)")
    p.add_argument("--print", action="store_true", help="print the plan and send nothing")
    args = p.parse_args()
    steps = plan(max(1, args.rounds))

    total = sum(s["seconds"] for s in steps)
    print(f"Show: {len(steps)} steps, about {total} s of arm time\n")
    for n, s in enumerate(steps, 1):
        print(f"{n:2}. {s['seconds']:3} s  {s['prompt'] or '(arms hold still)'}\n         says: {s['say']}")
    if args.print:
        return

    tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-N",
                               "-L", f"{LOCAL_PORT}:127.0.0.1:8091", HOST])
    time.sleep(2)
    try:
        try:
            status = call("GET", "/status")
        except urllib.error.URLError:
            raise SystemExit("No station agent answers on the station's port 8091. The person at the emergency stop "
                             "starts it with: dreamcli robot up --view") from None
        print(f"\nStation agent: {status.get('state')}. Starting in 3 s; Ctrl+C stops the arms.\n")
        time.sleep(3)
        running = False
        for n, s in enumerate(steps, 1):
            print(f"[{n}/{len(steps)}] {s['prompt'] or 'hold still'}")
            if s["prompt"]:
                if running:
                    call("POST", "/prompt", {"prompt": s["prompt"]})   # fresh episode with this prompt
                else:
                    call("POST", "/start", {"prompt": s["prompt"]})
                    running = True
            wait(s["seconds"])
        print("Show finished: stopping and going home.")
    except KeyboardInterrupt:
        print("\nCtrl+C: stopping the arms.")
    finally:
        try:
            call("POST", "/stop")
            call("POST", "/home")
        except (SystemExit, urllib.error.URLError) as e:
            print(f"Could not stop or home through the agent ({e}). Use the emergency stop or 'dreamcli robot down'.")
        tunnel.terminate()


if __name__ == "__main__":
    sys.exit(main())
