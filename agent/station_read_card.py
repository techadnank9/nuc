"""Runs ON THE STATION (NUCK-8): read which card is in the reveal stand from the overhead camera.

Prints one JSON line: {"ok": true, "ids": [19], "frame": "/tmp/fortune_last.jpg"} or {"ok": false, "error": "..."}.
Never touches the arms. Only works while no other program holds the cameras (dreamcli / dhk-collect stopped).

Run with the kit's environment, which has pyrealsense2 and OpenCV with ArUco:
    ~/physical-ai-hacks/.venv/bin/python station_read_card.py [--serial 261922273680] [--frames 20]
"""

import argparse
import json
import sys
import time

import cv2
import numpy as np
import pyrealsense2 as rs

OVERHEAD_SERIAL = "261922273680"  # from ~/.dreamlabs/station.yaml (camera "top")
DICT = cv2.aruco.DICT_4X4_50


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default=OVERHEAD_SERIAL)
    ap.add_argument("--frames", type=int, default=20, help="frames to scan after warm-up")
    ap.add_argument("--save", default="/tmp/fortune_last.jpg")
    args = ap.parse_args()

    pipe, cfg = rs.pipeline(), rs.config()
    cfg.enable_device(args.serial)
    cfg.enable_stream(rs.stream.color, 1280, 720, rs.format.bgr8, 30)
    try:
        pipe.start(cfg)
    except RuntimeError as e:
        print(json.dumps({"ok": False, "error": f"camera {args.serial} would not open: {e}"}))
        return 1

    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(DICT), cv2.aruco.DetectorParameters())
    seen: dict[int, int] = {}
    frame = None
    try:
        for _ in range(15):  # auto-exposure warm-up
            pipe.wait_for_frames(2000)
        for _ in range(args.frames):
            frame = np.asanyarray(pipe.wait_for_frames(2000).get_color_frame().get_data())
            _, ids, _ = detector.detectMarkers(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
            for i in [] if ids is None else ids.flatten().tolist():
                seen[i] = seen.get(i, 0) + 1
    finally:
        pipe.stop()

    if frame is not None:
        cv2.imwrite(args.save, frame)
    # A tag must show in at least a quarter of the frames to count, so one noisy frame can't name a card.
    ids = sorted((i for i, n in seen.items() if n >= max(1, args.frames // 4)), key=lambda i: -seen[i])
    print(json.dumps({"ok": bool(ids), "ids": ids, "counts": seen, "frame": args.save, "t": time.time()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
