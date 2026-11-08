"""Runs ON THE STATION (NUCK-8): keeps the robot's three cameras viewable by the web page at all times.

Two modes, so it never fights Dream-1 for the cameras (only one program can open a RealSense at a time):
- own:   the relay holds the cameras and serves their latest frames.
- dream: the relay has released the cameras for a Dream-1 run and serves the frames Dream-1 itself sees,
         read from dreamcli's own view page (dreamcli robot run --view, on 127.0.0.1:8093), plus each
         round trip's number and timing for the robot action log.

HTTP on 127.0.0.1:8097 (reach it through an SSH tunnel):
  GET  /cam/<overhead|left|right>.jpg   latest frame (204 when none yet)
  GET  /status                           {"mode", "cameras", "trip": {"n", "rtt_ms", "error"}}
  POST /release                          stop the cameras (before a Dream-1 run)
  POST /resume                           take the cameras back (after the run and the card read)

Run with the kit's environment (pyrealsense2 + OpenCV):
    ~/physical-ai-hacks/.venv/bin/python station_cam_relay.py
Never touches the arms.
"""

import base64
import json
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import pyrealsense2 as rs

SERIALS = {"overhead": "261922273680", "left": "262122273310", "right": "260322272283"}  # ~/.dreamlabs/station.yaml
DREAM_VIEW = "http://127.0.0.1:8093"
PORT = 8097

lock = threading.Lock()
frames: dict[str, bytes] = {}
state = {"mode": "own", "cameras": {}, "trip": None, "holding": False}
want = {"mode": "own"}


def role_of(key: str) -> str | None:
    k = key.lower()
    if "top" in k or "overhead" in k or "scene" in k:
        return "overhead"
    if "left" in k or "wrist_l" in k:
        return "left"
    if "right" in k or "wrist_r" in k:
        return "right"
    return None


def other_program() -> str:
    """The teleop recorder, a Dream-1 run, or the station agent (dreamcli robot up): they need the cameras, so the relay
    keeps out and shows the frames Dream-1 sees from the view page instead."""
    out = subprocess.run(["pgrep", "-af", "bin/(dhk-collect|dreamcli robot)|robots[.]station"],
                         capture_output=True, text=True).stdout
    return "" if not out.strip() else ("teleop recording" if "dhk-collect" in out else "a Dream-1 run")


def own_loop() -> None:
    """Hold the cameras while want == own and no other program needs them; release them when want == dream."""
    while True:
        if want["mode"] != "own":
            time.sleep(0.2)
            continue
        if who := other_program():
            state["mode"], state["holding"] = "busy", False
            state["cameras"] = {r: f"in use by {who}" for r in SERIALS}
            with lock:
                frames.clear()
            time.sleep(2)
            continue
        pipes = {}
        for role, serial in SERIALS.items():
            pipe, cfg = rs.pipeline(), rs.config()
            cfg.enable_device(serial)
            cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 15)
            try:
                pipe.start(cfg)
                pipes[role] = pipe
                state["cameras"][role] = "ok"
            except RuntimeError as e:
                state["cameras"][role] = f"unavailable: {e}"
        state["mode"] = "own" if pipes else "unavailable"
        state["holding"] = bool(pipes)
        try:
            last_check = 0.0
            while want["mode"] == "own" and pipes:
                # Often enough to let go while dreamcli is still importing: it opens the cameras ~1-2 s after it
                # starts, and a 2 s check lost that race (top camera "VIDIOC_S_FMT ... Input/output error").
                if time.time() - last_check > 0.2:
                    last_check = time.time()
                    if other_program():
                        break
                for role, pipe in pipes.items():
                    fs = pipe.poll_for_frames()
                    if fs:
                        img = np.asanyarray(fs.get_color_frame().get_data())
                        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
                        if ok:
                            with lock:
                                frames[role] = jpg.tobytes()
                time.sleep(0.02)
        finally:
            for pipe in pipes.values():
                try:
                    pipe.stop()
                except RuntimeError:
                    pass
            state["holding"] = False
        if not pipes:
            time.sleep(2)  # cameras busy (another program): retry


def dream_loop() -> None:
    """While Dream-1 runs, mirror the frames it sees from dreamcli's view page."""
    last = -1
    while True:
        if want["mode"] != "dream":
            time.sleep(0.2)
            continue
        state["mode"] = "dream"
        try:
            trips = json.load(urllib.request.urlopen(f"{DREAM_VIEW}/trips", timeout=1))
            if trips and trips[-1]["n"] != last:
                last = trips[-1]["n"]
                trip = json.load(urllib.request.urlopen(f"{DREAM_VIEW}/trip/{last}", timeout=2))
                for key, window in (trip.get("frames") or {}).items():
                    role = role_of(key)
                    if role and window:
                        with lock:
                            frames[role] = base64.b64decode(window[-1])
                state["trip"] = {"n": last, "rtt_ms": trips[-1].get("rtt_ms"), "error": trips[-1].get("error")}
        except Exception:
            pass  # the view page is not up yet (homing) or already gone (stopping)
        time.sleep(0.25)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes = b"", kind: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/cam/"):
            role = self.path[5:].split(".")[0].split("?")[0]
            with lock:
                jpg = frames.get(role)
            self._send(200, jpg, "image/jpeg") if jpg else self._send(204)
        elif self.path.startswith("/status"):
            self._send(200, json.dumps(state).encode())
        else:
            self._send(404, b'{"error": "not found"}')

    def do_POST(self) -> None:
        if self.path.startswith("/release"):
            want["mode"] = "dream"
            state["trip"] = None
            for _ in range(50):  # wait until the cameras are really closed
                if not state["holding"]:
                    break
                time.sleep(0.1)
            time.sleep(0.5)
            self._send(200, json.dumps({"mode": "dream"}).encode())
        elif self.path.startswith("/resume"):
            want["mode"] = "own"
            self._send(200, json.dumps({"mode": "own"}).encode())
        else:
            self._send(404, b'{"error": "not found"}')

    def log_message(self, *_: object) -> None:
        pass


if __name__ == "__main__":
    threading.Thread(target=own_loop, daemon=True).start()
    threading.Thread(target=dream_loop, daemon=True).start()
    print(f"camera relay on 127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
