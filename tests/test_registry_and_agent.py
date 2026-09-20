import json
from types import SimpleNamespace as NS

from app.agent.runner import FoundryAgent, _parse_what_if
from app.config import settings
from app.retrieval.azure_search import get_retriever
from app.tools.registry import TurnContext, call_tool, render_final

SAMPLES = json.load(open(settings.data_dir / "sample_claims.json"))


def ctx(sample=None):
    s = {"claim": SAMPLES[sample]["claim"] if sample else None, "uin": settings.default_uin, "history": []}
    return TurnContext(session=s, retriever=get_retriever())


def test_search_and_get_clause_return_citable_chunks():
    c = ctx()
    r = call_tool("search_policy", {"query": "waiting period cholecystectomy"}, c)
    assert r["results"] and all(x["chunk_key"] in c.seen for x in r["results"])
    g = call_tool("get_clause", {"clause_ref": "B.1.1.1 Note iii"}, c)
    assert "Proportionate deduction" in g["clauses"][0]["excerpt"]


def test_check_waiting_period_tool_is_deterministic():
    c = ctx()
    r = call_tool("check_waiting_period", {"first_policy_inception": "2024-11-01", "admission_date": "2025-09-20", "procedure": "Laparoscopic cholecystectomy"}, c)
    assert r["elapsed_months"] == 10
    assert any(k["code"] == "Excl02" and k["status"] == "violated" for k in r["checks"])


def test_final_answer_rejects_invented_citation_then_accepts_after_fix():
    c = ctx()
    hits = call_tool("search_policy", {"query": "maternity"}, c)["results"]
    bad = call_tool("final_answer", {"answer_type": "coverage_answer", "headline": "Not covered.", "verdict": "not_covered",
                                     "points": [{"label": "Maternity", "status": "problem", "detail": "Excluded.", "citations": ["made-up:key"]}]}, c)
    assert "error" in bad and c.final is None
    good = call_tool("final_answer", {"answer_type": "coverage_answer", "headline": "Maternity is excluded.", "verdict": "not_covered",
                                      "points": [{"label": "Maternity", "status": "problem", "detail": "Childbirth expenses are excluded.", "citations": [hits[0]["chunk_key"]]}],
                                      "citations": [hits[0]["chunk_key"]]}, c)
    assert good == {"status": "accepted"}
    md = render_final(c.final, c).markdown
    assert md.startswith("## 🔴 Not covered") and "Evidence from the policy wording" not in md and "Evidence (why the AI said this)" in md


def test_third_bad_attempt_is_accepted_with_citations_stripped():
    c = ctx()
    args = {"answer_type": "definition_answer", "headline": "x", "citations": ["nope"], "points": [{"label": "a", "status": "info", "detail": "b"}]}
    call_tool("final_answer", args, c); call_tool("final_answer", args, c)
    assert call_tool("final_answer", args, c) == {"status": "accepted"}
    assert c.final["citations"] == [] and any("could not be verified" in x for x in c.final["caveats"])


def test_assess_claim_requires_a_loaded_claim():
    assert "error" in call_tool("assess_claim", {}, ctx())


# ------------------------------------------------------------------ scripted fake of the Foundry Responses client
class FakeOpenAI:
    """Plays the model: assess_claim -> (bad final_answer) -> good final_answer."""
    def __init__(self, bad_first=False):
        self.step, self.bad_first, self.deleted, self.rid = 0, bad_first, False, None
        self.conversations = NS(create=lambda: NS(id="conv_1"), delete=self._delete)
        self.responses = NS(create=self._create)

    def _delete(self, conversation_id): self.deleted = True

    def _create(self, input, conversation, extra_body):
        self.step += 1
        fc = lambda name, args, cid: NS(type="function_call", name=name, arguments=json.dumps(args), call_id=cid)
        if self.step == 1:
            return NS(output=[fc("assess_claim", {}, "c1")], output_text="")
        if self.step == 2:
            self.rid = json.loads(input[0]["output"])["result_id"]
            if self.bad_first:
                return NS(output=[fc("final_answer", {"answer_type": "claim_assessment", "headline": "x", "result_id": "claim-nope"}, "c2")], output_text="")
            return NS(output=[fc("final_answer", {"answer_type": "claim_assessment", "headline": "x", "result_id": self.rid}, "c2")], output_text="")
        self.rid = json.loads(input[0]["output"]).get("problems") and self.rid
        return NS(output=[fc("final_answer", {"answer_type": "claim_assessment", "headline": "x", "result_id": self.rid}, "c3")], output_text="")


def _agent(fake):
    a = object.__new__(FoundryAgent)
    a.openai, a.ref = fake, {"agent_reference": {"name": "t", "type": "agent_reference"}}
    return a


def test_foundry_loop_runs_tools_then_renders_and_cleans_up():
    fake = FakeOpenAI()
    session = {"claim": SAMPLES["TC07"]["claim"], "uin": settings.default_uin, "history": []}
    res = _agent(fake).ask(session, "Assess this claim")
    assert res.answer_type == "claim_assessment" and "₹1,22,125" in res.markdown and "₹1,01,625" in res.markdown
    assert [t["tool"] for t in res.trace] == ["assess_claim", "final_answer"] and fake.deleted


def test_foundry_loop_recovers_from_a_rejected_final_answer():
    fake = FakeOpenAI(bad_first=True)
    session = {"claim": SAMPLES["TC07"]["claim"], "uin": settings.default_uin, "history": []}
    res = _agent(fake).ask(session, "Assess this claim")
    assert "₹1,22,125" in res.markdown and [t["ok"] for t in res.trace][-2:] == [False, True]


def test_what_if_parser():
    assert _parse_what_if("what if the room rent was rs. 5,000")["room_rate_per_day"] == 5000


def test_embed_passes_configured_dimensions(monkeypatch):
    import sys, types
    seen = {}

    class FakeEmb:
        def create(self, **kw):
            seen.update(kw)
            return types.SimpleNamespace(data=[types.SimpleNamespace(embedding=[0.0] * kw["dimensions"]) for _ in kw["input"]])

    class FakeAzureOpenAI:
        def __init__(self, **kw): self.embeddings = FakeEmb()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AzureOpenAI=FakeAzureOpenAI))
    from app.retrieval.azure_search import embed
    out = embed(["x", "y"])
    assert seen["dimensions"] == settings.embedding_dimensions == 1536 and len(out[0]) == 1536
