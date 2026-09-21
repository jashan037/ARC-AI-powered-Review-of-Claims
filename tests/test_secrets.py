"""Secrets: none in the tree, the detector works, and no key or auth header ever reaches a log line or an error response."""
import json
import logging
import subprocess
import sys

from fastapi.testclient import TestClient

from app import main
from app.config import ROOT
from app.observability import JsonFormatter

sys.path.insert(0, str(ROOT / "scripts" / "security"))
import scan_secrets as S  # noqa: E402

FAKE_KEY = "Zk3" + "9aBcD4eFgH5iJkL6mNoP7qRsT8uVwX0yZ1"        # built at run time so this file holds no key-shaped literal
FAKE_TOKEN = "eyJ" + "hbGciOiJSUzI1NiJ9.abc123def456ghi789jkl012"


def test_no_key_shaped_string_is_in_the_tree():
    assert S.scan_shapes(S.tree_files()) == []


def test_the_detector_finds_a_key_and_ignores_placeholders(tmp_path):
    assert S.shape_hits(f'AZURE_SEARCH_KEY = "{FAKE_KEY}"') == 1 and S.shape_hits(f"api_key: {FAKE_KEY}") == 1
    assert S.shape_hits("AZURE_SEARCH_KEY=<your key>") == 0 and S.shape_hits("api_key = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx") == 0 and S.shape_hits("no secrets here") == 0
    assert S.shape_hits("-----BEGIN " + "PRIVATE KEY-----") == 1


def test_the_env_file_is_ignored_by_git():
    assert subprocess.run(["git", "check-ignore", ".env"], cwd=ROOT, capture_output=True).returncode == 0


def test_the_pre_commit_hook_exists_and_is_the_scan():
    assert "scan_secrets" in (ROOT / "scripts" / "security" / "pre-commit").read_text()


# ---------------------------------------------------------------- nothing secret in logs, errors or exception messages
def test_a_key_in_an_exception_never_reaches_the_response_or_the_logs(caplog, monkeypatch):
    class Boom:
        def ask(self, session, message):
            raise RuntimeError(f"401 from https://x.search.windows.net with api-key: {FAKE_KEY} and Authorization: Bearer {FAKE_TOKEN}")
    monkeypatch.setattr(main, "get_agent", lambda: Boom())
    client = TestClient(main.app, raise_server_exceptions=False)
    sid = client.post("/sessions").json()["session_id"]
    client.post(f"/sessions/{sid}/documents/sample")
    client.post(f"/sessions/{sid}/intake")
    with caplog.at_level(logging.DEBUG):
        r = client.post(f"/sessions/{sid}/chat", json={"message": "how much will be paid"})
    assert r.status_code == 500 and FAKE_KEY not in r.text and FAKE_TOKEN not in r.text and "api-key" not in r.text.lower() and "Bearer" not in r.text
    fmt = JsonFormatter()
    lines = " ".join(fmt.format(rec) for rec in caplog.records) + caplog.text
    assert FAKE_KEY not in lines and FAKE_TOKEN not in lines


def test_the_json_log_formatter_redacts_secrets_in_any_field():
    rec = logging.LogRecord("t", logging.ERROR, __file__, 1, f"failed with key {FAKE_KEY} and Bearer {FAKE_TOKEN}", None, None)
    rec.fields = {"error": f"api-key: {FAKE_KEY}"}
    out = JsonFormatter().format(rec)
    assert FAKE_KEY not in out and FAKE_TOKEN not in out and json.loads(out)
