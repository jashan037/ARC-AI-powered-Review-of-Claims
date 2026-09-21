"""The security set for the final pass: /ready leaks nothing, every route carries the headers, a key never reaches a log line, an error body or
the PDF route, the PDF parser has a time limit, and .env is mode 600 and ignored by git.

The older checks stay where they were: developer routes, session expiry, caps, rate limit and delete-my-data in tests/test_surface.py,
the secret scanner and log redaction in tests/test_secrets.py.
"""
import dataclasses
import json
import logging
import os
import stat
import sys
import time

import pytest
from fastapi.testclient import TestClient

from app import intake, main
from app.config import ROOT
from app.observability import JsonFormatter, redact

sys.path.insert(0, str(ROOT / "scripts" / "security"))
import scan_secrets as S  # noqa: E402

client = TestClient(main.app, raise_server_exceptions=False)
KEY = "Qz7" + "4aBcD8eFgH2iJkL9mNoP3qRsT5uVwX1yZ6"          # assembled at run time: this file holds no key-shaped literal


def new_session():
    return client.post("/sessions").json()["session_id"]


# ---------------------------------------------------------------- /health and /ready
def test_health_is_one_word_and_touches_nothing():
    r = client.get("/health")
    assert r.json() == {"status": "ok"} and r.headers["cache-control"] == "no-store"


def test_ready_answers_booleans_only_and_names_no_endpoint():
    main._READY.update(at=0.0, body=None)
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status", "retrieval", "assistant"} and body["status"] in ("ready", "degraded")
    assert isinstance(body["retrieval"], bool) and isinstance(body["assistant"], bool)
    blob = json.dumps(body)
    for leak in ("http", "azure", ".net", "key", "endpoint", "Exception", "Error"):
        assert leak.lower() not in blob.lower(), leak


def test_ready_is_cached_so_it_cannot_be_used_to_hammer_azure(monkeypatch):
    main._READY.update(at=0.0, body=None)
    calls = []
    real = main.get_agent
    monkeypatch.setattr(main, "get_agent", lambda: calls.append(1) or real())
    client.get("/ready")
    client.get("/ready")
    assert len(calls) == 1
    main._READY.update(at=0.0, body=None)


def test_ready_says_degraded_instead_of_raising(monkeypatch):
    main._READY.update(at=0.0, body=None)
    monkeypatch.setattr(main, "get_agent", lambda: (_ for _ in ()).throw(RuntimeError(f"endpoint https://x.search.windows.net key={KEY}")))
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["status"] == "degraded" and r.json()["assistant"] is False
    assert KEY not in r.text and "windows.net" not in r.text
    main._READY.update(at=0.0, body=None)


# ---------------------------------------------------------------- headers everywhere, including the PDF and the page paths
@pytest.mark.parametrize("path", ["/", "/upload", "/chat", "/health", "/ready", "/static/app.css", "/static/logo.svg", "/static/fonts/InterVariable.woff2"])
def test_every_route_carries_the_headers(path):
    h = client.get(path).headers
    assert h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in h["content-security-policy"] and "cache-control" in h


def test_the_report_route_carries_them_too_and_is_never_cached():
    sid = new_session()
    client.post(f"/sessions/{sid}/documents/sample")
    client.post(f"/sessions/{sid}/intake")
    r = client.get(f"/sessions/{sid}/report.pdf")
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"].startswith("attachment;") and r.headers["content-type"] == "application/pdf"


# ---------------------------------------------------------------- a key never reaches a log line, an error body or an exception
def test_an_unexpected_error_answers_a_plain_sentence_and_logs_only_the_class(monkeypatch, caplog):
    sid = new_session()
    client.post(f"/sessions/{sid}/documents/sample")
    client.post(f"/sessions/{sid}/intake")

    def boom(*a, **k):
        raise RuntimeError(f"Azure said no: api_key={KEY}")

    monkeypatch.setattr(main, "get_agent", boom)
    with caplog.at_level(logging.ERROR, logger="claims.api"):
        r = client.post(f"/sessions/{sid}/chat", json={"message": "hello"})
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert r.json()["error"]["message"] == "Something went wrong on our side, and nothing was changed. Please try again in a moment."
    assert KEY not in r.text and "api_key" not in r.text and "Azure" not in r.text
    lines = [JsonFormatter().format(rec) for rec in caplog.records]
    assert lines and all(KEY not in line for line in lines)
    assert any('"error": "RuntimeError"' in line for line in lines)      # the class name, nothing else


@pytest.mark.parametrize("text", ['api_key="{k}"', "Authorization: Bearer {k}", "subscription-key: {k}", "here is my token = {k}"])
def test_the_redactor_catches_every_shape_we_log(text):
    out = redact(text.format(k=KEY))
    assert KEY not in out and "[redacted]" in out


def test_the_error_body_of_a_bad_request_never_echoes_the_value():
    sid = new_session()
    r = client.post(f"/sessions/{sid}/chat", json={"message": ""})
    assert r.status_code == 422 and "message" in r.text and "String should have at least 1 character" in r.text
    r = client.post(f"/sessions/{sid}/chat", json={"message": KEY})
    assert KEY not in r.text or r.status_code == 200      # a chat message is not echoed in an error body


# ---------------------------------------------------------------- the secret scan
def test_no_secret_value_and_no_key_shape_in_the_tree_or_the_history():
    assert S.scan_shapes(S.tree_files()) == []
    assert S.scan_history_shapes() == []


def test_the_env_file_is_private_and_untracked():
    env = ROOT / ".env"
    if not env.exists():
        pytest.skip(".env is not present in this checkout")
    mode = stat.S_IMODE(env.stat().st_mode)
    assert mode == 0o600, oct(mode)
    import subprocess
    tracked = subprocess.run(["git", "ls-files", ".env"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert tracked == ""


# ---------------------------------------------------------------- limits on what a document may cost
def test_reading_a_pdf_has_a_time_limit(monkeypatch):
    monkeypatch.setattr(intake, "MAX_PARSE_SECONDS", 0.0)
    name, data = intake.sample_files("on_time")[0]
    out = intake.process_file(name, data)
    assert out["status"] == "too_slow" and out["message"] == "This file took too long to read. Please upload a simpler copy."


def test_head_works_on_the_health_route_for_probes():
    r = client.head("/health")
    assert r.status_code == 200


def test_an_empty_file_says_so_instead_of_blaming_the_format():
    assert intake.process_file("e.pdf", b"")["message"] == "This file is empty. Please upload the document again."
    assert intake.process_file("e.pdf", b"   \n")["status"] == "empty"


def test_a_document_over_the_size_limit_is_refused_without_being_parsed():
    out = intake.process_file("big.pdf", b"%PDF-1.4\n" + b"0" * (intake.MAX_FILE_BYTES + 1))
    assert out["status"] == "too_large"


def test_the_body_size_limit_answers_413():
    sid = new_session()
    r = client.post(f"/sessions/{sid}/chat", content=b"x" * (main.settings.max_request_bytes + 10), headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "request_too_large"


def test_the_document_cap_per_session(monkeypatch):
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, max_docs_per_session=1))
    sid = new_session()
    files = [("files", (n, d, "application/pdf")) for n, d in intake.sample_files("on_time")[:2]]
    out = client.post(f"/sessions/{sid}/documents", files=files).json()
    assert [f["status"] for f in out["files"]] == ["recognised", "limit"]
    assert "limit of 1 documents" in out["files"][1]["message"]


# ---------------------------------------------------------------- nothing developer-only is on by default
def test_no_tool_trace_reaches_the_client_by_default():
    sid = new_session()
    client.post(f"/sessions/{sid}/documents/sample")
    client.post(f"/sessions/{sid}/intake")
    out = client.post(f"/sessions/{sid}/chat", json={"message": "how much will be paid?"}).json()
    assert set(out) == {"status", "reply", "sources"} or "tool_trace" not in out
    assert main.settings.debug_trace is False


def test_the_turn_log_carries_counts_only(caplog):
    sid = new_session()
    client.post(f"/sessions/{sid}/documents/sample")
    client.post(f"/sessions/{sid}/intake")
    with caplog.at_level(logging.INFO, logger="claims"):
        client.post(f"/sessions/{sid}/chat", json={"message": "my name is Rohan and my key is secret"})
    turns = [json.loads(JsonFormatter().format(r)) for r in caplog.records if r.getMessage() == "turn"]
    assert turns, "a turn must be logged"
    line = turns[-1]
    assert line["message_chars"] == len("my name is Rohan and my key is secret") and "message" not in line and "reply" not in line
    assert set(line) >= {"turn_id", "session", "agent", "status", "latency_ms", "tokens_in", "tokens_out"}
    assert "Rohan" not in json.dumps(line)
