"""P1-2: developer detail (tool_trace, tool names, tool arguments, chunk keys) is returned only when the SERVER runs with DEBUG_TRACE=1."""
import dataclasses
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import main
from app.tools.registry import TOOL_NAMES

client = TestClient(main.app, raise_server_exceptions=False)
CHUNK_KEY = re.compile(r"[a-z0-9][a-z0-9-]*:[A-Z][\w.\-]*")            # doc_id:chunk_id, e.g. optima-secure-v062425:C1-b
QUESTIONS = ["Please assess this claim", "Assess it, what if the room rent was 5,000 per day", "Why was the room rent deducted?",
             "What documents are missing?", "What does room rent mean?", "Is knee replacement covered and what is the waiting period?", "hello"]


def set_debug(monkeypatch, on):
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, debug_trace=on))


def chat(message, audience="customer", query=""):
    sid = client.post("/sessions", json={"audience": audience}).json()["session_id"]
    client.post(f"/sessions/{sid}/claim", json={"sample_id": "TC07"})
    r = client.post(f"/sessions/{sid}/chat{query}", json={"message": message})
    assert r.status_code == 200
    return r.json()


def test_debug_trace_is_off_by_default():
    assert main.settings.debug_trace is False                                        # the test environment sets nothing


@pytest.mark.parametrize("audience", ["customer", "officer"])
@pytest.mark.parametrize("q", QUESTIONS)
def test_a_normal_chat_response_has_no_trace_tool_names_or_chunk_keys(monkeypatch, q, audience):
    set_debug(monkeypatch, False)
    body = chat(q, audience)
    assert "tool_trace" not in body and "trace_summary" not in body
    raw = json.dumps(body, ensure_ascii=False)
    for name in TOOL_NAMES:
        assert name not in raw, name
    assert "chunk_key" not in raw and "what_if" not in raw and "room_rate_per_day" not in raw and not CHUNK_KEY.search(raw), CHUNK_KEY.findall(raw)[:3]
    assert all("chunk_key" not in c for c in body["citations"])
    assert body["answer_type"] and body["summary_markdown"]                          # the answer itself is untouched


def test_the_public_citations_keep_what_a_reference_popup_needs(monkeypatch):
    set_debug(monkeypatch, False)
    cits = chat("What does room rent mean?")["citations"]
    assert cits and all({"label", "clause", "citation", "excerpt"} <= set(c) for c in cits)


@pytest.mark.parametrize("query", ["?dev=1", "?dev=1&debug=1&debug_trace=1&trace=1", "?DEBUG_TRACE=1"])
def test_nothing_the_caller_sends_switches_the_trace_on(monkeypatch, query):
    set_debug(monkeypatch, False)
    body = chat("Please assess this claim", query=query)
    assert "tool_trace" not in body and "trace_summary" not in body and "chunk_key" not in json.dumps(body)


def test_the_stateless_assess_route_hides_chunk_keys_too(monkeypatch):
    set_debug(monkeypatch, False)
    body = client.post("/assess", json={"sample_id": "TC07"}).json()
    assert body["citations"] and "chunk_key" not in json.dumps(body)


def test_with_debug_trace_on_the_developer_detail_is_returned(monkeypatch):
    set_debug(monkeypatch, True)
    body = chat("Assess it, what if the room rent was 5,000 per day")
    assert [t["tool"] for t in body["trace_summary"]][0] == "assess_claim" and all(set(t) == {"tool", "ok", "ms"} for t in body["trace_summary"])
    assert "room_rate_per_day" in json.dumps(body["tool_trace"])                    # the raw trace carries the arguments, for developers only
    assert all("chunk_key" in c for c in body["citations"])
    assert client.post("/assess", json={"sample_id": "TC07"}).json()["citations"][0].get("chunk_key")


def test_debug_trace_reads_only_the_server_environment():
    import inspect
    from app import config
    src = inspect.getsource(config)
    assert 'os.getenv("DEBUG_TRACE", "0") == "1"' in src
    assert "dev" not in inspect.getsource(main.chat).lower().replace("developer", "")   # the chat route does not look at a dev parameter
