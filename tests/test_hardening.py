"""Timeouts, retries, the turn deadline, logging and API hardening. Everything here uses fakes: no Azure, no real sleeping."""
import copy
import dataclasses
import json
import logging
import sys
import types
from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import resilience as R
from app.agent.runner import AgentResult, FoundryAgent, OfflineAgent
from app.config import settings
from app.observability import JsonFormatter
from app.resilience import TurnAbort, call_with_retry, classify, turn_scope

SAMPLES = json.load(open(settings.data_dir / "sample_claims.json", encoding="utf-8"))


# ---------------------------------------------------------------- helpers
class Err(Exception):
    def __init__(self, status=None, headers=None):
        super().__init__(f"status {status}")
        self.status_code = status
        self.response = NS(headers=headers or {})


class APITimeoutError(Exception):          # same class name as openai's read timeout
    pass


class ServiceRequestTimeoutError(Exception):   # azure-core's connect timeout: nothing was sent, safe to retry
    pass


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """No real sleeping; jitter picks the top of its range so delays are predictable; small, known retry settings."""
    slept = []
    monkeypatch.setattr(R, "_sleep", slept.append)
    monkeypatch.setattr(R, "_jitter", lambda lo, hi: hi)
    monkeypatch.setattr(R, "settings", dataclasses.replace(settings, max_retries=3, backoff_base_s=0.5, backoff_cap_s=8, turn_deadline_s=60))
    return slept


def flaky(*failures, result="ok"):
    """A callable that raises each failure in turn, then returns result. .calls records the timeout it was given."""
    calls = []

    def fn(t):
        calls.append(t)
        if len(calls) <= len(failures):
            raise failures[len(calls) - 1]
        return result
    fn.calls = calls
    return fn


# ---------------------------------------------------------------- classification
def test_classify_separates_transient_timeout_and_real_errors():
    assert classify(Err(429)) == "transient" and classify(Err(503)) == "transient" and classify(Err(500)) == "transient"
    assert classify(Err(400)) is None and classify(Err(401)) is None and classify(Err(404)) is None
    assert classify(APITimeoutError()) == "timeout"
    assert classify(ServiceRequestTimeoutError()) == "transient"      # a connect timeout: nothing was sent, safe to retry
    assert classify(ValueError("bug")) is None


def test_a_connect_timeout_is_transient_but_a_read_timeout_is_not_retried_blindly():
    class ConnectTimeout(Exception):
        pass
    assert classify(ConnectTimeout()) == "transient"
    assert classify(APITimeoutError()) == "timeout"


# ---------------------------------------------------------------- retry with backoff
def test_429_is_retried_with_exponential_backoff_then_succeeds(fast):
    fn = flaky(Err(429), Err(429))
    assert call_with_retry(fn, label="model", timeout=10) == "ok"
    assert len(fn.calls) == 3 and fast == [0.5, 1.0]          # base * 2^attempt


def test_retries_are_capped_and_then_the_turn_aborts_as_unavailable(fast):
    fn = flaky(*[Err(503)] * 10)
    with pytest.raises(TurnAbort) as e:
        call_with_retry(fn, label="search", timeout=10)
    assert e.value.kind == "unavailable" and e.value.where == "search"
    assert len(fn.calls) == 4 and len(fast) == 3                 # 1 try + max_retries(3), never more


def test_backoff_never_exceeds_the_cap(fast):
    assert R.backoff_delay(20) == 8


def test_retry_after_header_is_honoured_but_capped(fast):
    call_with_retry(flaky(Err(429, {"retry-after": "3"})), label="model", timeout=10)
    assert fast == [3.0]
    fast.clear()
    call_with_retry(flaky(Err(429, {"retry-after": "120"})), label="model", timeout=10)
    assert fast == [8.0]


def test_real_errors_are_not_retried(fast):
    fn = flaky(Err(400), result="never")
    with pytest.raises(Err):
        call_with_retry(fn, label="model", timeout=10)
    assert len(fn.calls) == 1 and fast == []


def test_model_read_timeout_is_not_retried_but_a_search_read_timeout_is(fast):
    fn = flaky(APITimeoutError())
    with pytest.raises(TurnAbort) as e:
        call_with_retry(fn, label="model", timeout=10, retry_timeouts=False)
    assert e.value.kind == "timeout" and len(fn.calls) == 1
    assert call_with_retry(flaky(APITimeoutError()), label="search", timeout=10) == "ok"


# ---------------------------------------------------------------- the turn deadline
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_call_timeout_is_capped_by_what_is_left_of_the_deadline():
    clock = Clock()
    fn = flaky()
    with turn_scope(60, clock):
        clock.t = 55
        call_with_retry(fn, label="model", timeout=40)
    assert fn.calls == [5.0]


def test_no_call_is_started_when_the_deadline_is_gone():
    clock, fn = Clock(), flaky()
    with turn_scope(60, clock):
        clock.t = 59.5
        with pytest.raises(TurnAbort) as e:
            call_with_retry(fn, label="model", timeout=40)
    assert e.value.kind == "timeout" and fn.calls == []


def test_no_retry_when_the_backoff_would_run_past_the_deadline(fast):
    clock = Clock()
    with turn_scope(60, clock):
        clock.t = 57                                   # 3 s left: early retries fit, but the 2 s backoff plus the 1 s reserve does not
        fn = flaky(Err(429), Err(429), Err(429))
        with pytest.raises(TurnAbort) as e:
            call_with_retry(fn, label="model", timeout=40)
    assert e.value.kind == "unavailable" and len(fn.calls) < 4


def test_retries_are_counted_per_turn():
    with turn_scope(60, Clock()) as scope:
        call_with_retry(flaky(Err(429), Err(502)), label="model", timeout=10)
    assert scope.retries == {"model": 2}


# ---------------------------------------------------------------- tool dispatcher
def test_a_turn_abort_inside_a_tool_stops_the_turn_but_ordinary_tool_errors_go_back_to_the_model():
    from app.tools.registry import TurnContext, call_tool

    class Boom:
        def __init__(self, exc): self.exc = exc
        def search(self, *a, **k): raise self.exc

    ctx = TurnContext(session={}, retriever=Boom(TurnAbort("unavailable", "search")))
    with pytest.raises(TurnAbort):
        call_tool("search_policy", {"query": "x"}, ctx)
    ctx = TurnContext(session={}, retriever=Boom(ValueError("bad")))
    out = call_tool("search_policy", {"query": "x"}, ctx)
    assert "error" in out and ctx.trace[-1]["ok"] is False and "ms" in ctx.trace[-1]


# ---------------------------------------------------------------- Azure Search and embeddings
def test_search_is_retried_and_gets_a_per_request_timeout(fast):
    from app.retrieval.azure_search import AzureSearchRetriever

    seen, attempts = [], []

    class FakeClient:
        def search(self, **kw):
            seen.append(kw)
            attempts.append(1)
            if len(attempts) == 1:
                raise Err(429)
            return iter([{"chunk_key": "d:C1-b", "chunk_id": "C1-b", "doc_id": "d", "uin": "u", "clause": "C.1.b", "title": "t",
                          "citation": "Policy C.1.b", "page_start": 1, "text": "x", "@search.reranker_score": 2.5}])

    r = object.__new__(AzureSearchRetriever)
    r.client = FakeClient()
    out = r._run(search_text="*", top=1)
    assert len(attempts) == 2 and fast == [0.5]
    assert 0 < seen[-1]["read_timeout"] <= settings.search_timeout_s and seen[-1]["connection_timeout"] <= 5 and out[0]["chunk_id"] == "C1-b"


def test_embeddings_are_retried_and_use_no_sdk_retries(monkeypatch, fast):
    created, calls = {}, []

    class FakeEmb:
        def create(self, **kw):
            calls.append(kw)
            if len(calls) == 1:
                raise Err(429)
            return types.SimpleNamespace(data=[types.SimpleNamespace(embedding=[0.0] * kw["dimensions"])])

    class FakeAzureOpenAI:
        def __init__(self, **kw):
            created.update(kw)
            self.embeddings = FakeEmb()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AzureOpenAI=FakeAzureOpenAI))
    from app.retrieval.azure_search import embed
    assert len(embed(["x"])[0]) == 1536 and len(calls) == 2
    assert created["max_retries"] == 0 and 0 < calls[-1]["timeout"] <= settings.embed_timeout_s


# ---------------------------------------------------------------- the Foundry agent
class ScriptedOpenAI:
    """Model service that fails as told, then plays assess_claim -> final_answer."""

    def __init__(self, failures=()):
        self.failures, self.calls, self.deleted, self.timeouts = list(failures), 0, False, []
        self.conversations = NS(create=lambda **_: NS(id="conv_1"), delete=self._delete)
        self.responses = NS(create=self._create)
        self.step = 0

    def _delete(self, conversation_id, timeout=None):
        self.deleted = True

    def _create(self, input, conversation, extra_body, timeout=None):
        self.calls += 1
        self.timeouts.append(timeout)
        if self.failures:
            raise self.failures.pop(0)
        self.step += 1
        text = "Your claim looks likely to be paid ₹1,22,125, of which ₹1,01,625 is confirmed today."
        return NS(output=[NS(type="message", content=[NS(text=text)])], output_text=text)


def agent_with(fake):
    a = object.__new__(FoundryAgent)
    a.openai, a.ref = fake, {"agent_reference": {"name": "t", "type": "agent_reference"}}
    return a


def session():
    return {"id": "s1", "claim": copy.deepcopy(SAMPLES["TC07"]["claim"]), "uin": settings.default_uin, "history": []}


def test_agent_survives_a_429_from_the_model(fast):
    fake = ScriptedOpenAI([Err(429)])
    res = agent_with(fake).ask(session(), "How much will be paid?")
    assert res.status == "ok" and "₹1,22,125" in res.reply and fast == [0.5]
    assert all(0 < t <= settings.model_timeout_s for t in fake.timeouts)


def test_a_model_timeout_gives_a_try_again_answer_not_a_hang_or_an_exception():
    fake = ScriptedOpenAI([APITimeoutError()])
    res = agent_with(fake).ask(session(), "How much will be paid?")
    assert res.status == "timeout" and fake.calls == 1      # not retried: the request may still be running
    assert "try again" in res.reply.lower() and "₹" not in res.reply         # no numbers invented on the failure path
    assert fake.deleted                                                            # the conversation is still cleaned up


def test_persistent_429_ends_as_unavailable_after_a_bounded_number_of_tries(fast):
    fake = ScriptedOpenAI([Err(429)] * 20)
    res = agent_with(fake).ask(session(), "How much will be paid?")
    assert res.status == "unavailable" and fake.calls == settings.max_retries + 1 == 4 and len(fast) == 3
    assert "try again" in res.reply.lower()


def test_an_exhausted_deadline_answers_at_once_without_calling_the_model(monkeypatch):
    monkeypatch.setattr(R, "settings", dataclasses.replace(R.settings, turn_deadline_s=0.5))
    fake = ScriptedOpenAI()
    res = agent_with(fake).ask(session(), "How much will be paid?")
    assert res.status == "timeout" and fake.calls == 0 and "try again" in res.reply.lower()


def test_a_real_error_still_raises_so_the_api_can_answer_500(fast):
    fake = ScriptedOpenAI([Err(400)])
    with pytest.raises(Err):
        agent_with(fake).ask(session(), "How much will be paid?")
    assert fake.calls == 1 and fake.deleted


# ---------------------------------------------------------------- logging
def _capture():
    records = []

    class H(logging.Handler):
        def emit(self, record):
            records.append(json.loads(JsonFormatter().format(record)))
    h = H()
    logging.getLogger("claims").addHandler(h)
    return records, h


def test_each_turn_logs_tools_latency_and_answer_type_and_no_claim_data():
    from app.observability import configure_logging
    configure_logging()
    records, h = _capture()
    try:
        s = session()
        s["claim"]["insured_name"] = s["claim"].get("insured_name") or "Rohan Verma"
        marker = "zebra-crossing-question-text"
        OfflineAgent().ask(s, f"Please assess this claim {marker}")
        agent_with(ScriptedOpenAI()).ask(s, f"Assess this claim {marker}")
    finally:
        logging.getLogger("claims").removeHandler(h)
    turns = [r for r in records if r["event"] == "turn"]
    assert len(turns) == 2 and {t["agent"] for t in turns} == {"offline", "foundry"}
    t = turns[-1]
    assert t["answer_type"] == "chat" and t["status"] == "ok" and t["latency_ms"] >= 0 and t["session"] == "s1"
    assert [x["tool"] for x in t["tools"]] == ["assess_claim"] and all("ms" in x for x in t["tools"]) and t["model_calls"] == 2      # the assessment ran in code; one conversation and one response
    blob = json.dumps(turns)
    for private in (marker, s["claim"]["insured_name"], str(s["claim"].get("diagnosis")), "1,22,125", "122125"):
        assert private not in blob, f"{private!r} leaked into the log"


def test_a_timeout_is_logged_with_its_status_and_where_it_happened():
    records, h = _capture()
    try:
        agent_with(ScriptedOpenAI([APITimeoutError()])).ask(session(), "How much will be paid?")
    finally:
        logging.getLogger("claims").removeHandler(h)
    t = [r for r in records if r["event"] == "turn"][-1]
    assert t["status"] == "timeout" and t["error"] == "model:APITimeoutError" and t["model_calls"] == 2


# ---------------------------------------------------------------- API
@pytest.fixture
def api():
    from app import main
    return TestClient(main.app, raise_server_exceptions=False), main


def _new_session(client):
    sid = client.post("/sessions").json()["session_id"]
    client.post(f"/sessions/{sid}/claim", json={"sample_id": "TC07"})
    return sid


def test_unexpected_errors_are_a_clean_500_without_a_stack_trace_or_the_exception_text(api, monkeypatch):
    client, main = api
    monkeypatch.setattr(main, "get_agent", lambda: NS(ask=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret-internal-detail /Users/x/file.py"))))
    r = client.post(f"/sessions/{_new_session(client)}/chat", json={"message": "hi"})
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert "secret-internal-detail" not in r.text and "Traceback" not in r.text and ".py" not in r.text


def test_a_timed_out_turn_returns_the_try_again_answer_with_a_status_and_is_not_added_to_history(api, monkeypatch):
    client, main = api
    stub = AgentResult("This is taking longer than expected. Please try again.", [], [], "timeout")
    monkeypatch.setattr(main, "get_agent", lambda: NS(ask=lambda *a, **k: stub))
    sid = _new_session(client)
    r = client.post(f"/sessions/{sid}/chat", json={"message": "assess"})
    assert r.status_code == 200 and r.json()["status"] == "timeout" and "try again" in r.json()["reply"].lower()
    assert main.SESSIONS[sid]["history"] == []


def test_oversized_requests_get_413(api):
    client, main = api
    sid = _new_session(client)
    big = {"claim": {"claim_id": "x", "pad": "a" * (settings.max_request_bytes + 10)}}
    r = client.post(f"/sessions/{sid}/claim", json=big)
    assert r.status_code == 413 and r.json()["error"]["code"] == "request_too_large"
    # without a Content-Length header (chunked upload) the limit still applies
    r = client.post(f"/sessions/{sid}/claim", content=iter([b"a" * 100_000] * 4), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_validation_errors_are_clean_and_do_not_echo_what_was_sent(api):
    client, main = api
    sid = _new_session(client)
    r = client.post(f"/sessions/{sid}/chat", json={"message": "patient-name-Rohan " * 200})    # over the 2000 character limit
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request" and "Rohan" not in r.text
    assert client.post(f"/sessions/{sid}/chat", json={}).status_code == 422
    assert client.post("/assess", json={"claim": {"claim_id": "x"}}).status_code == 422       # was a 500 before
    assert client.post("/assess", json={"sample_id": "TC99"}).status_code == 404
    assert client.get("/nope").json()["error"]["code"] == "http_404"


def test_cors_comes_from_the_environment_setting_and_is_off_by_default():
    from app.main import harden

    def build(origins):
        a = FastAPI()
        harden(a, cors_origins=origins, max_bytes=1000)
        a.get("/x")(lambda: {"ok": 1})
        return TestClient(a)

    hdr = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"}
    allowed = build(("http://localhost:5173",)).options("/x", headers=hdr)
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in build(("https://other.example",)).options("/x", headers=hdr).headers
    assert "access-control-allow-origin" not in build(()).get("/x", headers={"Origin": "http://localhost:5173"}).headers
    assert settings.cors_origins == ()      # nothing in the test environment enables it
