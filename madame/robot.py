"""robot.py: send one task to the Dream-1 robot station and wait for it to finish.

The voice agent calls run_task("pick up the red card and hold it up"). It SSHes to the
station and runs `dreamcli robot run --task ...` for a fixed number of seconds, then stops
it with SIGINT (the same as Ctrl+C): the arms go home, switch off and go limp.

Safety: runs are DRY (the arms do not move) unless ROBOT_LIVE=1 is set in .env.

Try it from the terminal:
    python robot.py "pick up the red card and hold it up"            # dry run
    ROBOT_LIVE=1 python robot.py "pick up the red card and hold it up"  # arms move
"""

import asyncio
import os
import shlex
import stat
import sys
import tempfile

HOST = os.getenv("ROBOT_HOST", "pah@10.103.0.133")
SECONDS = int(os.getenv("ROBOT_SECONDS", "45"))  # how long the model drives the arms per task

_lock = asyncio.Lock()  # one robot run at a time
_askpass_path = None


def is_live() -> bool:
    return os.getenv("ROBOT_LIVE", "0") == "1"


def _askpass() -> str:
    """A tiny helper script that hands ssh the password from $ROBOT_PASSWORD (never written to disk)."""
    global _askpass_path
    if _askpass_path is None:
        fd, path = tempfile.mkstemp(prefix="robot-askpass-", suffix=".sh")
        with os.fdopen(fd, "w") as f:
            f.write('#!/bin/sh\necho "$ROBOT_PASSWORD"\n')
        os.chmod(path, stat.S_IRWXU)
        _askpass_path = path
    return _askpass_path


def _remote_command(task: str, seconds: int, live: bool) -> str:
    args = ["dreamcli", "robot", "run", "--task", task]
    if not live:
        args.append("--dry-run")
    dream = " ".join(shlex.quote(a) for a in args)
    return (
        # refuse if something already drives the arms
        "if pgrep -af 'bin/(dreamcli|dhk-collect)'; then echo 'ARMS_BUSY'; exit 3; fi; "
        "source ~/dream-env/bin/activate && cd ~/physical-ai-hacks && "
        # SIGINT after `seconds` = a clean Ctrl+C; hard kill 30 s later if it hangs
        f"timeout -s INT -k 30 {int(seconds)} {dream}"
    )


async def run_task(task: str, seconds: int = SECONDS) -> str:
    """Run one task on the arms. Returns a short plain-English result for the agent."""
    if _lock.locked():
        return "The robot is already busy with another task."
    async with _lock:
        live = is_live()
        env = dict(os.environ, SSH_ASKPASS=_askpass(), SSH_ASKPASS_REQUIRE="force", DISPLAY=":0")
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new",
            HOST, _remote_command(task, seconds, live),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        out_bytes, _ = await proc.communicate()
        out = out_bytes.decode(errors="replace")
        code = proc.returncode
        mode = "live" if live else "dry run"

        if "ARMS_BUSY" in out:
            return "Another program is using the arms. Someone needs to stop it first."
        if code in (0, 124):  # 124 = our time limit ended the run, the normal case
            return f"Finished the task ({mode}): {task}"
        last = "\n".join(line for line in out.strip().splitlines()[-3:])
        return f"The robot hit a problem ({mode}, exit {code}): {last}"


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    if len(sys.argv) < 2:
        sys.exit('usage: python robot.py "task text" [seconds]')
    secs = int(sys.argv[2]) if len(sys.argv) > 2 else SECONDS
    print("LIVE: the arms will move" if is_live() else "dry run: the arms will not move")
    print(asyncio.run(run_task(sys.argv[1], secs)))
