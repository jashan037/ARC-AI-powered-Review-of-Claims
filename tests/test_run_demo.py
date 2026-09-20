"""scripts/run_demo.sh starts the demo only against the real agent."""
import os
import subprocess

import pytest

from app.config import ROOT

SCRIPT = ROOT / "scripts" / "run_demo.sh"


def run(tmp_path, content=None, env_extra=None, args=("--check",)):
    env_file = tmp_path / "test.env"
    if content is not None:
        env_file.write_text(content)
    env = {k: v for k, v in os.environ.items() if k not in ("RETRIEVER", "AGENT_MODE")}
    env.update({"ENV_FILE": str(env_file)}, **(env_extra or {}))
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=30, cwd=ROOT)


def test_it_is_executable_and_uses_port_8765():
    assert os.access(SCRIPT, os.X_OK) and 'PORT="${PORT:-8765}"' in SCRIPT.read_text()


def test_it_accepts_azure_and_foundry(tmp_path):
    p = run(tmp_path, "RETRIEVER=azure\nAGENT_MODE=foundry\n")
    assert p.returncode == 0 and "Settings OK" in p.stdout


def test_it_accepts_trailing_comments_and_spaces(tmp_path):
    assert run(tmp_path, "RETRIEVER=azure            # local | azure\nAGENT_MODE=foundry   # offline | foundry\n").returncode == 0


@pytest.mark.parametrize("content,what", [("RETRIEVER=local\nAGENT_MODE=offline\n", "RETRIEVER is 'local'"), ("RETRIEVER=azure\nAGENT_MODE=offline\n", "AGENT_MODE is 'offline'"),
                                          ("RETRIEVER=local\nAGENT_MODE=foundry\n", "RETRIEVER is 'local'"), ("SOMETHING=else\n", "RETRIEVER is 'unset'"),
                                          ("RETRIEVER=azure\n", "AGENT_MODE is 'unset'")])
def test_it_refuses_anything_else(tmp_path, content, what):
    p = run(tmp_path, content)
    assert p.returncode == 1 and "REFUSING TO START" in p.stderr and what in p.stderr and "RETRIEVER=azure" in p.stderr and "AGENT_MODE=foundry" in p.stderr
    assert "Settings OK" not in p.stdout


def test_it_refuses_when_the_env_file_is_missing(tmp_path):
    p = run(tmp_path, None)
    assert p.returncode == 1 and "not found" in p.stderr


def test_a_variable_set_in_the_shell_wins_over_the_file_like_in_the_app(tmp_path):
    good = "RETRIEVER=azure\nAGENT_MODE=foundry\n"
    assert run(tmp_path, good, {"RETRIEVER": "local"}).returncode == 1
    assert run(tmp_path, "RETRIEVER=local\nAGENT_MODE=offline\n", {"RETRIEVER": "azure", "AGENT_MODE": "foundry"}).returncode == 0


def test_it_never_starts_a_server_when_refusing(tmp_path):
    p = run(tmp_path, "RETRIEVER=local\nAGENT_MODE=offline\n", args=())                # no --check: it would start uvicorn if it did not refuse
    assert p.returncode == 1 and "Starting ARC" not in p.stdout
