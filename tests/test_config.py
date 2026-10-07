"""config + paths: every setting comes from a CLEFLINE_* variable, with sane defaults.

Settings are read at import, so these run a fresh interpreter per case instead of
reloading modules inside the test process.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _probe(env: dict, code: str):
    """Run `code` in a fresh interpreter with only `env` as CLEFLINE_* config."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("CLEFLINE_")}
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
        env={**clean, **env, "PYTHONPATH": str(REPO / "src")},
    )


def test_defaults_suit_one_person_on_their_own_machine():
    out = _probe({}, "import config, json; print(json.dumps([config.HOST, config.PORT, "
                     "config.LOG_LEVEL, config.MAX_DURATION_SECONDS, config.MIN_FREE_GB]))")
    assert json.loads(out.stdout) == ["127.0.0.1", 8104, "INFO", 900, 3]


def test_every_setting_can_be_overridden_from_the_environment():
    env = {"CLEFLINE_HOST": "0.0.0.0", "CLEFLINE_PORT": "9000", "CLEFLINE_LOG_LEVEL": "debug",
           "CLEFLINE_MAX_DURATION_SECONDS": "60", "CLEFLINE_MIN_FREE_GB": "7"}
    out = _probe(env, "import config, json; print(json.dumps([config.HOST, config.PORT, "
                      "config.LOG_LEVEL, config.MAX_DURATION_SECONDS, config.MIN_FREE_GB]))")
    assert json.loads(out.stdout) == ["0.0.0.0", 9000, "DEBUG", 60, 7]


def test_a_non_numeric_setting_stops_startup_with_a_clear_message():
    out = _probe({"CLEFLINE_PORT": "eighty"}, "import config")
    assert out.returncode != 0
    assert "CLEFLINE_PORT" in out.stderr and "eighty" in out.stderr


def test_a_port_out_of_range_is_refused():
    out = _probe({"CLEFLINE_PORT": "70000"}, "import config")
    assert out.returncode != 0
    assert "CLEFLINE_PORT" in out.stderr


def test_an_unknown_log_level_is_refused():
    out = _probe({"CLEFLINE_LOG_LEVEL": "LOUD"}, "import config")
    assert out.returncode != 0
    assert "CLEFLINE_LOG_LEVEL" in out.stderr


def test_the_data_directory_defaults_to_data_in_the_repo():
    out = _probe({}, "import paths; print(paths.DATA)")
    assert Path(out.stdout.strip()) == REPO / "data"


def test_the_data_directory_can_be_moved(tmp_path):
    target = tmp_path / "elsewhere"
    out = _probe({"CLEFLINE_DATA_DIR": str(target)},
                 "import paths; print(paths.DATA); print(paths.SOURCES); print(paths.JOBS)")
    data, sources, jobs = (Path(line) for line in out.stdout.split())
    assert data == target.resolve()
    assert sources == data / "sources" and jobs == data / "jobs"


def test_a_tilde_in_the_data_directory_is_expanded():
    out = _probe({"CLEFLINE_DATA_DIR": "~/clefline-data"}, "import paths; print(paths.DATA)")
    assert Path(out.stdout.strip()) == Path.home() / "clefline-data"


def test_env_example_lists_every_setting():
    """.env.example is the one place a person looks to see what can be set."""
    example = (REPO / ".env.example").read_text()
    config_source = (REPO / "src" / "config.py").read_text()
    for name in sorted({w for w in config_source.replace('"', " ").split()
                        if w.startswith("CLEFLINE_")}):
        assert name in example, f"{name} is read by config.py but missing from .env.example"


def test_readme_documents_every_setting():
    readme = (REPO / "README.md").read_text()
    config_source = (REPO / "src" / "config.py").read_text()
    names = {w for w in config_source.replace('"', " ").split() if w.startswith("CLEFLINE_")}
    for name in sorted(names):
        assert name in readme, f"{name} is read by config.py but not documented in README.md"
