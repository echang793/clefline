"""Run a child process that can be timed out, cancelled, and always cleaned up.

demucs and yt-dlp run for minutes. `subprocess.run(timeout=...)` can only kill the
direct child (demucs spawns workers that keep running), cannot be cancelled by the
user, and leaves orphans when this server is stopped. `run` fixes all three: the
child gets its own process group, which is killed as a whole, and a cancel check is
polled while it runs.

Callers do not thread a cancel flag through their signatures. The pipeline opens a
`scope(check)` around a job, and every `run` inside it honours that check.
"""

import contextlib
import contextvars
import json
import logging
import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import paths

log = logging.getLogger("clefline.procs")

POLL_SECONDS = 0.25
KILL_GRACE_SECONDS = 3.0


class Cancelled(Exception):
    """The job was cancelled, or the server is shutting down."""


class TimedOut(Exception):
    """The child ran past its time limit and was killed."""


_scope: contextvars.ContextVar[Callable[[], bool] | None] = contextvars.ContextVar(
    "cancel_scope", default=None
)


@contextlib.contextmanager
def scope(check: Callable[[], bool]) -> Iterator[None]:
    """Every run() inside this block is cancelled when `check()` turns true."""
    token = _scope.set(check)
    try:
        yield
    finally:
        _scope.reset(token)


def check_cancelled() -> None:
    """Raise Cancelled if the current scope asks for it. Cheap; call between stages."""
    check = _scope.get()
    if check is not None and check():
        raise Cancelled()


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGTERM the whole process group, then SIGKILL whatever ignores it."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=KILL_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue


def run(
    cmd: list[str], *, timeout: float, cancel: Callable[[], bool] | None = None
) -> subprocess.CompletedProcess:
    """Run `cmd`, capturing text output. Raises TimedOut or Cancelled (after killing
    the child's whole process group); a missing executable raises FileNotFoundError."""
    check = cancel or _scope.get()
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    deadline = None if timeout is None else time.monotonic() + timeout
    _register(proc, cmd)
    try:
        while True:
            try:
                # communicate() keeps draining both pipes between polls, so a chatty
                # child (demucs logs progress to stderr) never blocks on a full pipe.
                stdout, stderr = proc.communicate(timeout=POLL_SECONDS)
                return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                pass
            if check is not None and check():
                raise Cancelled()
            if deadline is not None and time.monotonic() > deadline:
                raise TimedOut(f"ran longer than {timeout:g}s")
    finally:
        if proc.poll() is None:
            _kill_group(proc)
            with contextlib.suppress(Exception):
                proc.communicate(timeout=KILL_GRACE_SECONDS)
        _forget(proc)


# ---------------------------------------------------------------- orphan reaping
#
# A graceful stop kills the running child (see run()). A `kill -9`, a crash, or a
# macOS out-of-memory kill does not run any of that, so demucs would keep running
# after the server died -- and the resumed job would start a second one in the
# same directory. So each live child is recorded on disk, and the next start kills
# any whose server is gone.

def _registry() -> Path:
    return paths.DATA / "run"


def _register(proc: subprocess.Popen, cmd: list[str]) -> None:
    try:
        _registry().mkdir(parents=True, exist_ok=True)
        (_registry() / f"{proc.pid}.json").write_text(json.dumps(
            {"parent": os.getpid(), "cmd": " ".join(cmd)[:200], "started": time.time()}
        ))
    except OSError:
        log.warning("could not record child process %s for orphan reaping", proc.pid)


def _forget(proc: subprocess.Popen) -> None:
    with contextlib.suppress(OSError):
        (_registry() / f"{proc.pid}.json").unlink()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _command_of(pid: int) -> str:
    result = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                            capture_output=True, text=True)
    return result.stdout.strip()


def _arguments(command: str) -> list[str]:
    """The first two arguments after the program (`-m demucs`, `-m yt_dlp`, `-c ...`):
    what identifies a job, whatever path the interpreter was started through."""
    return command.split()[1:3]


def reap_orphans() -> int:
    """Kill children left running by a server that died; return how many."""
    registry = _registry()
    if not registry.is_dir():
        return 0
    reaped = 0
    for entry in sorted(registry.glob("*.json")):
        try:
            record = json.loads(entry.read_text())
            child, parent = int(entry.stem), int(record["parent"])
            recorded = str(record["cmd"])
        except (ValueError, KeyError, OSError):
            entry.unlink(missing_ok=True)
            continue
        if _pid_alive(parent):
            continue   # that server is still running; the child is its business
        if _pid_alive(child):
            running = _command_of(child)
            # The pid may have been reused by something unrelated: only kill it if
            # it is still running what we started. Compare the arguments, never
            # argv[0]: `ps` shows a framework/Homebrew Python's real binary path,
            # not the venv's .venv/bin/python that sys.executable (and so the
            # recorded command) names -- CI caught orphans surviving because of it.
            wanted = _arguments(recorded)
            if wanted and wanted == _arguments(running):
                log.warning("killing orphaned child %s left by a dead server: %s", child, running)
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(child, signal.SIGKILL)
                reaped += 1
            else:
                log.info("child %s is no longer ours (pid reused); leaving it", child)
                continue
        entry.unlink(missing_ok=True)
    return reaped

