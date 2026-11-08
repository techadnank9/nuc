"""Runs ON THE STATION: a live, human-readable view of whatever Dream-1 run is happening (anyone's: run.sh,
dreamcli, the Fortune Teller page). Read-only: it only reads the run's trace and Dream's log.

Every second it prints one line: time into the run, model step (chunk), the right gripper (commanded vs measured),
whether the fingers are touching something, how far the right arm moved, and safety clamps. It also flags each grab
and each slip, and shows Dream's own log lines (model latency, sessions) as they appear.

    python3 station_live_run.py          # Ctrl+C to stop watching (the run itself is not affected)
"""

import csv
import functools
import os
import time

print = functools.partial(print, flush=True)  # noqa: A001 - live output over ssh
TRACE = os.path.expanduser("~/.dreamlabs/last-run.csv")
DREAM_LOG = os.path.expanduser("~/.dream/logs/dream.log")


def rows_since(path: str, start: int) -> tuple[list[dict], int]:
    try:
        with open(path) as f:
            rows = list(csv.DictReader(f))
    except (OSError, csv.Error):
        return [], start
    if len(rows) < start:  # a new run replaced the file
        start = 0
    return rows[start:], len(rows)


def main() -> None:
    seen, t0, run_mtime = 0, None, None
    log_pos = os.path.getsize(DREAM_LOG) if os.path.exists(DREAM_LOG) else 0
    touching, touch_t, last_print = False, 0.0, 0.0
    prev_arm = None
    print("Watching Dream-1 runs on this station (Ctrl+C to stop watching; the run is not affected)\n", flush=True)
    existing, seen = rows_since(TRACE, 0)
    if existing:  # a run already in progress: follow it from now, timed from its start
        t0 = float(existing[0]["t_s"])
        print(f"=== following the current run ({float(existing[-1]['t_s']) - t0:.0f}s in) ===", flush=True)
    while True:
        # Dream's own log: sessions, slow model warnings
        if os.path.exists(DREAM_LOG):
            size = os.path.getsize(DREAM_LOG)
            if size < log_pos:
                log_pos = 0
            if size > log_pos:
                with open(DREAM_LOG) as f:
                    f.seek(log_pos)
                    for line in f.read().splitlines():
                        if "session" in line or "slow" in line.lower() or "ERROR" in line or "refus" in line:
                            print("  [dream] " + line.split(" ", 2)[-1][:150])
                log_pos = size
        new, total = rows_since(TRACE, seen)
        if total < seen or (new and t0 is None):
            t0 = float(new[0]["t_s"]) if new else None
            print("\n=== new run started ===")
        seen = total
        for r in new:
            t = float(r["t_s"]) - (t0 or float(r["t_s"]))
            cmd, meas = float(r["cmd_gripper_r"]), float(r["gripper_r"])
            contact = meas - cmd > 0.04
            if contact and not touching:
                touching, touch_t = True, t
                print(f"  {t:6.1f}s  GRAB: fingers touching something (cmd {cmd:.2f}, held open at {meas:.2f})")
            elif touching and not contact:
                touching = False
                why = "model opened the gripper" if cmd > meas - 0.01 and cmd > 0.5 else "SLIPPED: fingers closed past the object"
                print(f"  {t:6.1f}s  LOST after {t - touch_t:.1f}s: {why}")
            if t - last_print >= 1.0:
                last_print = t
                arm = [float(r[f"measured_r{j}"]) for j in range(6)]
                moved = 0.0 if prev_arm is None else max(abs(a - b) for a, b in zip(arm, prev_arm))
                prev_arm = arm
                print(f"{t:6.1f}s  step {r['chunk_id']:>3}  R grip cmd {cmd:.2f} meas {meas:.2f}"
                      f"{'  <touching>' if touching else ''}  R arm moved {moved:.2f} rad/s"
                      f"{'  CLAMPED' if r['clamped'] == '1' else ''}")
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
