"""procs.run: a subprocess runner that can be timed out, cancelled, and cleaned up.

These run real child processes -- the whole point is that the OS-level behaviour
(process groups, pipes) is right, which a mock cannot show.
"""

import os
import sys
import textwrap
import time

import pytest

import procs


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed-but-unreaped zombie still answers kill(0); treat it as dead.
    import subprocess

    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(state.stdout.strip()) and not state.stdout.strip().startswith("Z")


def _wait_dead(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_run_returns_output_and_exit_code():
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    result = procs.run([sys.executable, "-c", code], timeout=30)
    assert result.returncode == 3
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"


def test_a_child_that_writes_a_lot_of_output_does_not_deadlock():
    """demucs prints progress to stderr continuously; an undrained pipe fills
    (~64 KB) and blocks the child forever."""
    code = "import sys; sys.stderr.write('x' * 2_000_000); sys.stdout.write('y' * 2_000_000)"
    started = time.time()
    result = procs.run([sys.executable, "-c", code], timeout=30)
    assert result.returncode == 0
    assert len(result.stderr) == 2_000_000 and len(result.stdout) == 2_000_000
    assert time.time() - started < 20


def test_a_timeout_kills_the_child_and_raises(tmp_path):
    pidfile = tmp_path / "pid"
    code = f"import os, time; open({str(pidfile)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
    started = time.time()
    with pytest.raises(procs.TimedOut):
        procs.run([sys.executable, "-c", code], timeout=1)
    assert time.time() - started < 10
    assert _wait_dead(int(pidfile.read_text()))


def test_cancelling_kills_the_child_promptly(tmp_path):
    pidfile = tmp_path / "pid"
    code = f"import os, time; open({str(pidfile)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
    begun = time.time()
    with pytest.raises(procs.Cancelled):
        procs.run([sys.executable, "-c", code], timeout=60,
                  cancel=lambda: time.time() - begun > 1.0)
    assert time.time() - begun < 10
    assert _wait_dead(int(pidfile.read_text()))


def test_the_whole_process_group_is_killed_not_just_the_parent(tmp_path):
    """demucs spawns worker processes; killing only the parent leaves them running."""
    pidfile = tmp_path / "pids"
    code = textwrap.dedent(f"""
        import os, subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        open({str(pidfile)!r}, "w").write(f"{{os.getpid()}} {{child.pid}}")
        time.sleep(60)
    """)
    with pytest.raises(procs.TimedOut):
        procs.run([sys.executable, "-c", code], timeout=2)
    parent, grandchild = (int(p) for p in pidfile.read_text().split())
    assert _wait_dead(parent)
    assert _wait_dead(grandchild)


def test_a_cancel_check_that_never_fires_changes_nothing():
    result = procs.run([sys.executable, "-c", "print('ok')"], timeout=30, cancel=lambda: False)
    assert result.stdout.strip() == "ok"


def test_a_missing_executable_is_a_clean_error():
    with pytest.raises(FileNotFoundError):
        procs.run(["definitely-not-a-real-binary-xyz"], timeout=5)


def test_a_cancel_scope_applies_to_every_run_inside_it(tmp_path):
    """The pipeline sets one scope per job so fetch/separate need no cancel
    argument threaded through their signatures."""
    begun = time.time()
    with procs.scope(lambda: time.time() - begun > 1.0):
        with pytest.raises(procs.Cancelled):
            procs.run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=60)
    assert time.time() - begun < 10


def test_outside_a_scope_nothing_is_cancelled():
    procs.check_cancelled()  # does not raise
    with procs.scope(lambda: False):
        procs.check_cancelled()


def test_check_cancelled_raises_inside_a_firing_scope():
    with procs.scope(lambda: True), pytest.raises(procs.Cancelled):
        procs.check_cancelled()


# ------------------------------------------------------------- orphan reaping

def _dead_pid() -> int:
    import subprocess

    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return done.pid


def _start_sleeper():
    import subprocess

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True,
    )
    return child


def _register(child, parent, cmd):
    import json

    import paths

    registry = paths.DATA / "run"
    registry.mkdir(parents=True, exist_ok=True)
    (registry / f"{child.pid}.json").write_text(json.dumps(
        {"parent": parent, "cmd": cmd, "started": time.time()}
    ))


def test_run_records_its_child_while_it_runs_and_forgets_it_after(tmp_path):
    import paths

    seen = {}

    def peek():
        registry = paths.DATA / "run"
        files = registry.glob("*.json") if registry.exists() else []
        seen["during"] = sorted(f.name for f in files)
        return False

    procs.run([sys.executable, "-c", "import time; time.sleep(1)"], timeout=30, cancel=peek)
    assert len(seen["during"]) == 1
    assert not list((paths.DATA / "run").glob("*.json"))


def test_a_child_orphaned_by_a_dead_server_is_reaped_at_startup():
    """kill -9 / an OOM kill skips shutdown, leaving demucs running; the resumed
    job would start a second one in the same directory."""
    child = _start_sleeper()
    try:
        _register(child, parent=_dead_pid(), cmd=f"{sys.executable} -c import time")
        assert procs.reap_orphans() == 1
        assert _wait_dead(child.pid)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_a_child_whose_server_is_still_alive_is_left_alone():
    child = _start_sleeper()
    try:
        _register(child, parent=os.getpid(), cmd=f"{sys.executable} -c import time")
        assert procs.reap_orphans() == 0
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()


def test_a_reused_pid_running_something_else_is_not_killed():
    """The recorded pid may now belong to an unrelated process."""
    child = _start_sleeper()
    try:
        _register(child, parent=_dead_pid(), cmd="/usr/bin/some-other-program --flag")
        assert procs.reap_orphans() == 0
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()


def test_stale_registry_entries_are_cleaned_up_even_when_nothing_is_running():
    import json

    import paths

    registry = paths.DATA / "run"
    registry.mkdir(parents=True)
    (registry / f"{_dead_pid()}.json").write_text(json.dumps(
        {"parent": _dead_pid(), "cmd": "python -m demucs", "started": 0}
    ))
    (registry / "garbage.json").write_text("{not json")

    assert procs.reap_orphans() == 0
    assert not list(registry.glob("*.json"))


def test_reaping_with_no_registry_is_a_no_op():
    assert procs.reap_orphans() == 0
