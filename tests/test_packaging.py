"""Packaging guards: dependency files, the Python pin, the service and CI definitions.

These are file-level checks. They exist so the pieces that make clefline
reproducible and runnable as a service cannot quietly drift apart.
"""

import plistlib
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

import preflight

REPO = Path(__file__).resolve().parents[1]


def _requirements(name: str) -> list[str]:
    lines = (REPO / name).read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith(("#", "-"))]


def _package(requirement: str) -> str:
    return re.split(r"[=<>!~\[ ;]", requirement, maxsplit=1)[0].lower().replace("_", "-")


# ------------------------------------------------------------------ python pin

def test_python_version_is_pinned_consistently():
    wanted = ".".join(str(n) for n in preflight.REQUIRED_PYTHON)
    assert (REPO / ".python-version").read_text().strip() == wanted
    project = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
    assert project["requires-python"] == f"=={wanted}.*"
    ruff = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["ruff"]
    assert ruff["target-version"] == "py" + wanted.replace(".", "")


# ---------------------------------------------------------------- requirements

def test_runtime_requirements_carry_no_dev_tools():
    runtime = {_package(r) for r in _requirements("requirements.txt")}
    assert not runtime & {"pytest", "ruff"}


def test_dev_requirements_add_the_test_and_lint_tools_on_top_of_runtime():
    dev_text = (REPO / "requirements-dev.txt").read_text()
    assert "-r requirements.txt" in dev_text
    dev = {_package(r) for r in _requirements("requirements-dev.txt")}
    assert {"pytest", "ruff"} <= dev


def test_yt_dlp_stays_unpinned_because_youtube_breaks_old_builds():
    spec = next(r for r in _requirements("requirements.txt") if _package(r) == "yt-dlp")
    assert spec.lower() == "yt-dlp"


def test_the_resampy_override_exists_and_is_explained():
    text = (REPO / "overrides.txt").read_text()
    assert "resampy>=0.4.3" in text and "pkg_resources" in text


# --------------------------------------------------------------------- lockfile

def test_the_lockfile_pins_every_dependency_exactly():
    pins = {}
    for line in (REPO / "requirements.lock").read_text().splitlines():
        match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", line.strip())
        if match:
            pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    assert len(pins) > 40, "the lock looks empty"
    for requirement in _requirements("requirements-dev.txt"):
        assert _package(requirement) in pins, f"{requirement} is not pinned in requirements.lock"


def test_the_lock_resolves_resampy_past_the_basic_pitch_cap():
    text = (REPO / "requirements.lock").read_text()
    version = re.search(r"^resampy==(\d+)\.(\d+)\.(\d+)", text, re.M)
    assert version and tuple(int(n) for n in version.groups()) >= (0, 4, 3)


# -------------------------------------------------------------------- launchd

def _template(name: str) -> dict:
    return plistlib.loads((REPO / "deploy" / name).read_bytes())


def test_the_server_agent_restarts_after_a_crash_and_can_find_ffmpeg():
    agent = _template("com.clefline.server.plist")
    assert agent["Label"] == "com.clefline.server"
    assert agent["RunAtLoad"] is True
    # Restart after a crash or OOM kill, but not after a clean exit; throttled so a
    # preflight failure (exit 1) cannot spin.
    assert agent["KeepAlive"] == {"SuccessfulExit": False}
    assert agent["ThrottleInterval"] >= 10
    # launchd's default PATH has no /opt/homebrew/bin, so ffmpeg would "not be found".
    assert "/opt/homebrew/bin" in agent["EnvironmentVariables"]["PATH"].split(":")
    assert agent["ProgramArguments"][0].endswith("/.venv/bin/python")
    assert agent["ProgramArguments"][1].endswith("/src/server.py")


def test_the_cleanup_agent_runs_weekly_and_really_deletes():
    agent = _template("com.clefline.cleanup.plist")
    assert agent["Label"] == "com.clefline.cleanup"
    assert "--yes" in agent["ProgramArguments"]
    assert "StartCalendarInterval" in agent and "KeepAlive" not in agent


@pytest.mark.parametrize("name", ["com.clefline.server.plist", "com.clefline.cleanup.plist"])
def test_templates_use_placeholders_not_one_persons_paths(name):
    text = (REPO / "deploy" / name).read_text()
    assert "__REPO__" in text and "__HOME__" in text
    assert "/Users/" not in text


def test_the_install_script_is_valid_shell_and_executable():
    script = REPO / "scripts" / "install-service.sh"
    assert script.stat().st_mode & 0o111, "install-service.sh must be executable"
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0


def test_the_install_script_renders_both_agents_without_touching_launchd(tmp_path):
    dest = tmp_path / "LaunchAgents"
    result = subprocess.run(
        [str(REPO / "scripts" / "install-service.sh"), "--no-load", "--dest", str(dest),
         "--logs", str(tmp_path / "logs")],
        capture_output=True, text=True, cwd=REPO, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    server = plistlib.loads((dest / "com.clefline.server.plist").read_bytes())
    assert server["ProgramArguments"][1] == str(REPO / "src" / "server.py")
    assert server["WorkingDirectory"] == str(REPO)
    assert "__REPO__" not in (dest / "com.clefline.cleanup.plist").read_text()
    assert (tmp_path / "logs").is_dir()


def test_the_install_script_refuses_an_icloud_synced_checkout(tmp_path):
    """Always-on service + iCloud-synced files = the stalls and outages that
    moved franklinwh and gridwise (and this repo) out of Desktop."""
    result = subprocess.run(
        [str(REPO / "scripts" / "install-service.sh"), "--no-load", "--dest", str(tmp_path),
         "--logs", str(tmp_path / "logs"), "--repo", "/Users/x/Library/Mobile Documents/clefline"],
        capture_output=True, text=True, cwd=REPO, timeout=60,
    )
    assert result.returncode != 0
    assert "iCloud" in result.stdout + result.stderr


# ------------------------------------------------------------------------- CI

def test_ci_runs_lint_and_the_offline_tests_on_python_311():
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())
    steps = workflow["jobs"]["test"]["steps"]
    commands = "\n".join(str(s.get("run", "")) for s in steps)
    assert "ruff check src tests scripts" in commands
    assert "pytest" in commands and "--run-network" not in commands
    # Reproducible: exactly the pinned set, no resolving (basic-pitch's stale resampy
    # cap makes resolving from requirements.txt fail without the override).
    assert "-r requirements.lock" in commands and "--no-deps" in commands
    assert str(workflow["jobs"]["test"]["runs-on"]).startswith("macos")
    assert any("3.11" in str(s.get("with", {})) for s in steps)
