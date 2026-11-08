"""Has one card show finished? Reads the station agent's live run log (~/.dreamlabs/last-run.csv); arms untouched.

    show_done.py count          -> number of rows logged so far (the baseline when a show starts)
    show_done.py check <rows>   -> JSON {"held": bool, "done": bool, "arm": "l"|"r"|null} for rows after <rows>
    show_done.py grip <rows>    -> JSON {"grip": bool, "arm": ...}: a handle steady between the fingers for 0.8 s

held: an arm closed (jaw < 0.12) for >= 3 s while its shoulder lifted >= 0.4 rad (holding the card up).
done: after a hold, that arm opened again (jaw > 0.8) for >= 1 s: the card was put back down.
"""
import csv
import json
import os
import sys

LOG = os.path.expanduser("~/.dreamlabs/last-run.csv")


def rows() -> list[dict]:
    try:
        with open(LOG) as f:
            return list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []


def check(r: list[dict]) -> dict:
    """A show = one arm closed (jaw < 0.12) for >= 3 s while its shoulder lifts >= 0.4 rad, then that arm open
    again (jaw > 0.8) for >= 1 s. Quick close-opens on nothing last under 2 s and never lift, so they don't count."""
    if len(r) < 2:
        return {"held": False, "done": False, "arm": None}
    t = [float(x["t_s"]) for x in r]
    result = {"held": False, "done": False, "arm": None}
    for arm in ("l", "r"):
        jaw = [float(x[f"gripper_{arm}"]) for x in r]
        sh = [float(x[f"measured_{arm}1"]) for x in r]
        held, closed_at, low_sh, open_at = False, None, 0.0, None
        for i in range(len(r)):
            if not held:
                if jaw[i] < 0.12:
                    if closed_at is None:
                        closed_at, low_sh = t[i], sh[i]
                    low_sh = min(low_sh, sh[i])
                    if t[i] - closed_at >= 3.0 and sh[i] - low_sh >= 0.4:
                        held = True
                        result = {"held": True, "done": False, "arm": arm}
                else:
                    closed_at = None
            elif jaw[i] > 0.8:
                open_at = t[i] if open_at is None else open_at
                if t[i] - open_at >= 1.0:
                    return {"held": True, "done": True, "arm": arm, "done_at_s": round(t[i] - t[0], 1)}
            else:
                open_at = None
    return result


def grip(r: list[dict]) -> dict:
    """An arm's jaw steady between 0.06 and 0.35 for >= 0.8 s: a handle (not nothing, not open) between the fingers."""
    for arm in ("l", "r"):
        start = None
        for x in r:
            j, t = float(x[f"gripper_{arm}"]), float(x["t_s"])
            if 0.06 <= j <= 0.35:
                start = t if start is None else start
                if t - start >= 0.8:
                    return {"grip": True, "arm": arm}
            else:
                start = None
    return {"grip": False, "arm": None}


if __name__ == "__main__":
    all_rows = rows()
    if sys.argv[1] == "count":
        print(len(all_rows))
    else:
        start = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        part = all_rows[start:] if start <= len(all_rows) else all_rows
        print(json.dumps(grip(part) if sys.argv[1] == "grip" else check(part)))
