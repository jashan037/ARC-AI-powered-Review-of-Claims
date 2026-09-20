"""Stage 3: in customer mode model-written text is spoken to the customer; the guard sends officer voice back once; the renderer protects when the model insists."""
import pytest

from app.tools.registry import voice_problems
from tests.test_plain_questions import Script, agent, customer_session

BAD = ["The insured was admitted in September.", "Verify the inception date.", "Confirm whether the claim is an accident.", "Open Show more to see the working.",
       "The claimant should send the bills.", "Route to a claims officer.", "For the officer to check.", "Pay the claim in full.", "Please approve this claim.",
       "Check whether cover applies.", "The policyholder has a waiting period.", "Audience: customer.", "Reject the claim.", "See below for details."]
GOOD = ["I can't approve or reject your claim; a claims officer decides.", "A claims officer will decide whether to pay your claim.", "You do not need to pay anything more today.", "Your room rent was reduced by ₹12,000.", "Please send the doctor's prescription.", "You were admitted on 10 Sep 2025.", "A claims officer will decide on your claim.",
        "Please check your policy schedule for the start date.", "Your plan pays for a room up to a limit each day."]


@pytest.mark.parametrize("text", BAD)
def test_officer_voice_is_found_in_any_customer_facing_field(text):
    for final in (dict(answer_type="direct_answer", reply=text), dict(answer_type="coverage_answer", headline=text), dict(answer_type="coverage_answer", headline="ok", next_steps=[text]),
                  dict(answer_type="coverage_answer", headline="ok", points=[dict(label="x", status="info", detail=text)])):
        assert voice_problems(final), (text, final)


@pytest.mark.parametrize("text", GOOD)
def test_customer_voice_passes(text):
    assert voice_problems(dict(answer_type="direct_answer", reply=text)) == []


def test_a_customer_reply_in_officer_voice_is_rejected_once_and_the_rewrite_accepted():
    good = {"answer_type": "direct_answer", "reply": "Your name on this claim is Rohan Verma."}
    bad = {"answer_type": "direct_answer", "reply": "The insured is Rohan Verma."}
    model = Script([[("get_claim_summary", {})], [("final_answer", bad)], [("final_answer", good)]])
    res = agent(model).ask(customer_session(), "what's my name")
    assert [t["ok"] for t in res.trace if t["tool"] == "final_answer"] == [False, True] and res.summary_markdown == good["reply"]
    assert "the insured" in model.outputs[1]["problems"][0].lower() and model.outputs[1]["problems"][0].startswith("Customer wording:")


def test_it_is_asked_only_once():
    bad = {"answer_type": "direct_answer", "reply": "The insured is Rohan Verma."}
    res = agent(Script([[("get_claim_summary", {})], [("final_answer", bad)], [("final_answer", bad)]])).ask(customer_session(), "what's my name")
    assert [t["ok"] for t in res.trace if t["tool"] == "final_answer"] == [False, True]


def test_the_officer_is_not_held_to_the_customer_voice():
    s = dict(customer_session(), audience="officer")
    final = {"answer_type": "coverage_answer", "headline": "The insured is in the 24-month waiting period.", "verdict": "depends",
             "points": [dict(label="Waiting", status="warning", detail="Verify the inception date.")], "next_steps": ["Confirm whether it was an accident."]}
    model = Script([[("search_policy", {"query": "waiting period"})], [("final_answer", lambda m: dict(final, citations=[m.first_key()]))]])
    model.first_key = lambda: model.outputs[0]["results"][0]["chunk_key"]
    res = agent(model).ask(s, "Is cataract surgery covered?")
    assert res.status == "ok" and [t["ok"] for t in res.trace if t["tool"] == "final_answer"] == [True]


def test_customer_section_titles_have_no_officer_wording():
    from app.rendering.customer_labels import customerize
    assert customerize("What to check next") == "What to do next" and customerize("For the claims officer to review") == "A claims officer will look at this"


def test_a_customers_documents_or_deduction_answer_is_sent_back_once_to_become_a_direct_answer():
    for kind, extra in (("documents_answer", {}), ("deduction_explanation", {"focus": "room"})):
        first = lambda m, kind=kind, extra=extra: dict({"answer_type": kind, "headline": "Here it is.", "result_id": m.rid}, **extra)   # noqa: E731
        good = {"answer_type": "direct_answer", "reply": "Your room rent was reduced by ₹12,000.", "details": ["room_working"]}
        model = Script([[("assess_claim", {})], [("final_answer", first)], [("final_answer", good)]])
        res = agent(model).ask(customer_session(), "Why was my room rent reduced?")
        assert res.answer_type == "direct_answer" and [t["ok"] for t in res.trace if t["tool"] == "final_answer"] == [False, True]
        assert model.outputs[1]["problems"][0].startswith("Customer answer type:")
        again = Script([[("assess_claim", {})], [("final_answer", first)], [("final_answer", first)]])
        assert agent(again).ask(customer_session(), "Why was my room rent reduced?").answer_type == kind             # asked once, then the model's choice stands


def test_an_officer_still_gets_documents_and_deduction_answers():
    s = dict(customer_session(), audience="officer")
    model = Script([[("assess_claim", {})], [("final_answer", lambda m: {"answer_type": "deduction_explanation", "headline": "x", "result_id": m.rid, "focus": "room"})]])
    res = agent(model).ask(s, "Why was the room rent deducted?")
    assert res.answer_type == "deduction_explanation" and [t["ok"] for t in res.trace if t["tool"] == "final_answer"] == [True]
