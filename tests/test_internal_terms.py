"""No result id, chunk key, tool name or tool field name ever reaches the officer: a guard sends it back once, the renderer removes what is left."""
import json
from types import SimpleNamespace as NS

import pytest

from app.agent.runner import FoundryAgent
from app.config import settings
from app.rendering.scrub import find, officer_texts, scrub, scrub_final
from app.retrieval.azure_search import get_retriever
from app.tools.registry import TurnContext, call_tool, render_final, validate_final

SAMPLES = json.load(open(settings.data_dir / "sample_claims.json", encoding="utf-8"))
R = get_retriever()
KEY = "optima-secure-v062425:C1-b"

LEAKS = ["Refer to the waiting-period check result (result_id) for the exact eligible_from date to advise the claimant.",
         "See result_id claim-1a2b3c4d for the numbers.", "The result ID is waiting-9f8e7d6c.", "Cited as chunk_key optima-secure-v062425:C1-b.",
         "I called assess_claim and then check_waiting_period.", "Use search_policy again.", "get_clause returned the text.", "lookup_non_medical_item says so.",
         "Then final_answer was called.", "get_claim_summary shows the plan.", "Per chunk_id C1-b.", "optima-secure-v062425:A1.2-Def5 defines it."]
CLEAN = ["Check the waiting-period result for the exact eligible date.", "Confirm whether the claim arose from an accident.", "Assess this claim once the prescription arrives.",
         "The final answer rests on the Policy Schedule.", "Policy C.1.b applies (Excl02).", "Waiting-period check: 16 months of 24.", "Arrival at 10:30 on 15 Jul 2026.",
         "Note: the claim-related documents are missing.", "Search the policy wording for the clause.", "Ask for a chunk of the bill to be itemised.",
         "The pre-existing disease (PED) waiting period is 36 months.", "Day-care: not applicable for this procedure."]


# ---------------------------------------------------------------- detection
@pytest.mark.parametrize("text", LEAKS)
def test_internal_terms_are_found(text):
    assert find(text), text


@pytest.mark.parametrize("text", CLEAN)
def test_ordinary_officer_language_is_not_flagged(text):
    assert find(text) == [] and scrub(text) == text


# ---------------------------------------------------------------- scrub
@pytest.mark.parametrize("text", LEAKS)
def test_scrub_removes_every_internal_term_and_leaves_readable_text(text):
    out = scrub(text)
    assert find(out) == [] and out and "  " not in out and " ," not in out and " ." not in out and "()" not in out


def test_the_leak_seen_in_the_demo_reads_well_after_scrubbing():
    assert scrub(LEAKS[0]) == "Refer to the waiting-period check result for the exact eligible-from date to advise the claimant."


def test_tool_names_become_plain_words():
    assert scrub("Run check_waiting_period with the dates.") == "Run the waiting-period check with the dates."
    assert scrub("Use assess_claim first.") == "Use the claim assessment first."


def test_scrub_final_cleans_every_officer_field_and_leaves_citations_alone():
    final = dict(answer_type="coverage_answer", verdict="depends", headline="Per get_clause it depends.",
                 points=[dict(label="See result_id", status="info", detail=f"Taken from {KEY}.", citations=[KEY]), dict(label="chunk_key", status="info", detail="claim-1a2b3c4d", citations=[])],
                 next_steps=["claim-1a2b3c4d", "Check the plan with assess_claim."], caveats=["result_id only."], citations=[KEY])
    out = scrub_final(final)
    assert all(find(t) == [] for _, t in officer_texts(out))
    assert out["points"][0]["citations"] == [KEY] and out["citations"] == [KEY]         # citations are legitimate chunk keys
    assert out["next_steps"] == ["Check the plan with the claim assessment."]           # a step that was only an id is dropped
    assert len(out["points"]) == 2 and final["headline"] == "Per get_clause it depends."   # the input is not modified


# ---------------------------------------------------------------- guard in the validator
def _ctx():
    return TurnContext(session={}, retriever=R)


def _final(**kw):
    return dict(answer_type="general_answer", headline="Hello.", **kw)


@pytest.mark.parametrize("field,value", [("headline", "It is in result_id claim-1a2b3c4d."), ("next_steps", [LEAKS[0]]), ("caveats", ["Ran assess_claim."]),
                                         ("points", [dict(label="Clause", status="info", detail="From chunk_key C1-b.")]),
                                         ("points", [dict(label="get_clause result", status="info", detail="Fine.")])])
def test_every_officer_field_is_checked(field, value):
    a = _final()
    a[field] = value
    errs = validate_final(a, _ctx())
    assert len(errs) == 1 and errs[0].startswith("Text the claims officer reads") and "Rephrase" not in errs[0] and "Remove or rephrase" in errs[0]


def test_citations_may_contain_chunk_keys_without_tripping_the_guard():
    ctx = _ctx()
    ctx.seen[KEY] = R.get_by_key(KEY)
    ok = _final(points=[dict(label="Rule", status="info", detail="Waiting period applies.", citations=[KEY])], citations=[KEY])
    assert validate_final(ok, ctx) == []


def test_the_guard_names_where_and_what_and_fires_once():
    ctx = _ctx()
    leaky = _final(next_steps=[LEAKS[0]], caveats=["Ran assess_claim."])
    first = call_tool("final_answer", leaky, ctx)
    assert "error" in first and "next_steps[1]: 'result_id'" in first["problems"][0] and "caveats[1]: 'assess_claim'" in first["problems"][0]
    assert call_tool("final_answer", leaky, ctx) == {"status": "accepted"}                 # rejected once, not twice


def test_the_guard_is_independent_of_the_other_once_only_guards():
    ctx = _ctx()
    both = _final(next_steps=["Reject the claim.", "See result_id."])
    problems = call_tool("final_answer", both, ctx)["problems"]
    assert len(problems) == 2 and ctx.decision_wording_rejected and ctx.internal_terms_rejected


# ---------------------------------------------------------------- the renderer's last line of defence
def test_a_second_attempt_that_still_leaks_is_scrubbed_in_the_rendered_answer():
    ctx = _ctx()
    ctx.seen[KEY] = R.get_by_key(KEY)
    leaky = dict(answer_type="coverage_answer", verdict="depends", headline="Per get_clause the waiting period depends.",
                 points=[dict(label="Waiting period", status="warning", detail=f"Taken from chunk_key {KEY} via search_policy.", citations=[KEY])],
                 next_steps=[LEAKS[0], "Confirm the accident (see claim-1a2b3c4d)."], caveats=["Ran assess_claim; result_id claim-1a2b3c4d."], citations=[KEY])
    assert "error" in call_tool("final_answer", leaky, ctx)
    assert call_tool("final_answer", leaky, ctx) == {"status": "accepted"}
    md = render_final(ctx.final, ctx).markdown
    body = md.split("### Evidence")[0]
    assert find(body) == [], body
    assert "Policy C.1.b" in md and "waiting-period check result" in md                # citations and content survive


def test_deterministic_answers_are_scrubbed_too_including_the_assessment_note():
    ctx = TurnContext(session={"claim": SAMPLES["TC07"]["claim"], "uin": settings.default_uin, "history": []}, retriever=R)
    rid = call_tool("assess_claim", {}, ctx)["result_id"]
    ctx.internal_terms_rejected = True                                                  # as if the one rephrase was already used
    for final in (dict(answer_type="claim_assessment", headline="x", result_id=rid, caveats=[f"Numbers come from {rid} via assess_claim."]),
                  dict(answer_type="documents_answer", headline="Missing: see result_id.", result_id=rid, next_steps=["Ask for the prescription (assess_claim says so)."]),
                  dict(answer_type="deduction_explanation", headline="Deduction per assess_claim.", result_id=rid, focus="room")):
        assert call_tool("final_answer", final, ctx) == {"status": "accepted"}
        md = render_final(ctx.final, ctx).markdown
        assert find(md.split("### Evidence")[0]) == [], md
        assert ("prescription" in md.lower()) if final["answer_type"] == "documents_answer" else ("₹" in md)   # the content is still there


def test_a_waiting_period_answer_is_scrubbed():
    ctx = _ctx()
    rid = call_tool("check_waiting_period", dict(first_policy_inception="2025-03-01", admission_date="2026-07-15", diagnosis="cataract"), ctx)["result_id"]
    ctx.internal_terms_rejected = True
    final = dict(answer_type="waiting_period_answer", headline="Not served; see check_waiting_period.", result_id=rid, next_steps=[LEAKS[0]])
    assert call_tool("final_answer", final, ctx) == {"status": "accepted"}
    md = render_final(ctx.final, ctx).markdown
    assert find(md.split("### Evidence")[0]) == [] and "1 Mar 2027" in md


# ---------------------------------------------------------------- through the agent loop, with a model that keeps leaking
class LeakyModel:
    def __init__(self):
        self.step, self.rid = 0, None
        self.conversations = NS(create=lambda **_: NS(id="c"), delete=lambda conversation_id, timeout=None: None)
        self.responses = NS(create=self._create)

    def _create(self, input, conversation, extra_body, timeout=None):
        self.step += 1
        fc = lambda name, args: NS(type="function_call", name=name, arguments=json.dumps(args), call_id=f"c{self.step}")   # noqa: E731
        if self.step == 1:
            return NS(output=[fc("assess_claim", {})], output_text="")
        self.rid = self.rid or json.loads(input[0]["output"])["result_id"]
        return NS(output=[fc("final_answer", dict(answer_type="documents_answer", headline="Prescription missing (result_id).", result_id=self.rid,
                                                  next_steps=[LEAKS[0]], caveats=["Based on assess_claim output."]))], output_text="")


def test_the_full_loop_rejects_once_then_delivers_a_clean_answer():
    a = object.__new__(FoundryAgent)
    a.openai, a.ref = LeakyModel(), {"agent_reference": {"name": "t", "type": "agent_reference"}}
    res = a.ask({"id": "s", "claim": SAMPLES["TC07"]["claim"], "uin": settings.default_uin, "history": []}, "Which documents are missing?")
    assert res.status == "ok" and [t["ok"] for t in res.trace if t["tool"] == "final_answer"] == [False, True]
    assert find(res.markdown.split("### Evidence")[0]) == [] and "prescription" in res.markdown.lower()
