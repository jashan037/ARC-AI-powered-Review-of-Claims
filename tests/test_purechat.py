"""The pure chat path: the model writes the reply, the code guards it. Guards on free-form text, one model call per question, rewrite once then repair, sources, tool outputs."""
import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import main
from app.agent.runner import FoundryAgent, sources_for
from app.retrieval.azure_search import get_retriever
from app.tools.guards import MAX_WORDS, check_reply, fix_reply
from app.tools.registry import SCHEMAS, TOOL_NAMES, TurnContext, call_tool, precompute
from tests.test_hardening_stage4 import customer_session

client = TestClient(main.app, raise_server_exceptions=False)
GOOD = "Your claim looks likely to be paid ₹1,22,125 once your documents are complete. ₹1,01,625 is confirmed today and ₹20,500 is held until you send the prescription."


class Chat:
    """A model that returns the scripted steps: ("text", reply) or ("call", tool, args). Records every input it was sent."""

    def __init__(self, *steps):
        self.steps, self.inputs, self.n = list(steps), [], 0
        self.conversations = NS(create=lambda **_: NS(id="c"), delete=lambda conversation_id, timeout=None: None)
        self.responses = NS(create=self.create)

    def create(self, input, conversation, extra_body, timeout=None, **kw):
        self.inputs.append(input)
        step = self.steps.pop(0)
        self.n += 1
        if step[0] == "call":
            return NS(output=[NS(type="function_call", name=step[1], arguments=json.dumps(step[2]), call_id=f"c{self.n}")], output_text="")
        return NS(output=[NS(type="message", content=[NS(text=step[1])])], output_text=step[1])


def ask(model, message, session=None):
    a = object.__new__(FoundryAgent)
    a.openai, a.ref = model, {}
    return a.ask(session or customer_session(), message)


def ctx_for(question="How much will be paid?"):
    ctx = TurnContext(session=customer_session(), retriever=get_retriever(), question=question)
    precompute(ctx)
    return ctx


def kinds(text, question="How much will be paid?"):
    return [k for k, _ in check_reply(text, ctx_for(question))]


# ---------------------------------------------------------------- guard a: numbers
@pytest.mark.parametrize("text", ["Your estimated payment is ₹1,22,125 and ₹1,01,625 is confirmed today.", "Your room pays at 62.5% of the room charges.", "You were admitted on 10 September 2025 at 14:30.",
                                  "12 items are not payable, together ₹12,500.", "You stayed 4 days.", "Admitted on 10/09/2025.", "Room limit is ₹5,000 a day; you were billed ₹8,000 a day.",
                                  "If it had been 5000 a day you would get more.", "Nothing numeric here."])
def test_numbers_from_the_tools_or_the_claim_pass_in_any_format(text):
    assert "numbers" not in kinds(text, "What if the room rent was 5000?")


@pytest.mark.parametrize("text", ["Your payment is ₹9,999.", "About 13 items are not payable.", "You get 63% of it.", "Admitted on 11 Sep 2025.", "It takes 45 days."])
def test_numbers_no_tool_returned_are_a_problem(text):
    assert "numbers" in kinds(text)


# ---------------------------------------------------------------- guard b: internal terms
@pytest.mark.parametrize("text", ["I called assess_claim and got this.", "See optima-secure-v062425:C1-b for details.", "This is the Excl03 rule.", "Listed in Annexure B.", "Under C.1.b the wait is 24 months.",
                                  "The result_id is claim-1a2b3c4d.", "Audience: customer."])
def test_internal_terms_are_a_problem(text):
    assert "internal" in kinds(text.replace("24 months", "a set time"))


# ---------------------------------------------------------------- guard c: decisions
@pytest.mark.parametrize("bad", ["Your claim is approved.", "I approve this.", "We will pay you today.", "The claim has been rejected.", "You should approve it.", "I will settle your claim."])
def test_decisions_are_a_problem(bad):
    assert "decision" in kinds(bad)


@pytest.mark.parametrize("good", ["Your claim looks likely to be paid; a claims officer decides.", "I can't approve or reject a claim.", "It appears eligible and is flagged for the officer to confirm.",
                                  "A claims officer will decide whether to pay."])
def test_likely_appears_and_refusals_are_fine(good):
    assert "decision" not in kinds(good)


# ---------------------------------------------------------------- guard d: voice
@pytest.mark.parametrize("bad", ["The insured was admitted in September.", "The claimant should send the bills.", "The policyholder has a waiting period."])
def test_third_person_is_a_problem(bad):
    assert "voice" in kinds(bad)


def test_you_and_your_claim_are_fine():
    assert "voice" not in kinds("Your claim needs your prescription.")


# ---------------------------------------------------------------- guard e: soft length
def test_a_long_reply_is_a_problem_unless_the_customer_asked_for_detail():
    long = " ".join(["word"] * (MAX_WORDS + 20))
    assert "length" in kinds(long) and "length" not in kinds(long, "Explain in detail why it was reduced") and "length" not in kinds(long, "Give me the full list of items")
    assert "length" not in kinds(" ".join(["word"] * 100))


# ---------------------------------------------------------------- the repairs in code
def test_the_repair_drops_the_sentence_with_an_unverified_number_and_keeps_the_rest():
    ctx = ctx_for()
    fixed = fix_reply("Your estimated payment is ₹1,22,125. The bill was ₹9,999 in total. Please send the prescription.", ctx)
    assert fixed == "Your estimated payment is ₹1,22,125. Please send the prescription."
    assert fix_reply("It is ₹9,999.", ctx) == "Your estimated payment is ₹1,22,125, of which ₹1,01,625 is confirmed today."    # nothing left: a sentence built from the assessment


def test_the_repair_writes_internal_terms_in_plain_words_and_drops_decisions_and_third_person():
    ctx = ctx_for()
    text = fix_reply("The Excl03 rule and Annexure B apply. Your claim is approved. The insured should wait. Please send the prescription.", ctx)
    assert "Excl03" not in text and "Annexure" not in text and "approved" not in text and "insured" not in text
    assert "Please send the prescription." in text and "non-medical items" in text
    assert "assess_claim" not in fix_reply("I ran assess_claim for you.", ctx)


def test_length_is_soft_the_repair_leaves_it():
    ctx = ctx_for()
    long = " ".join(["word"] * 200)
    assert fix_reply(long, ctx) == long


# ---------------------------------------------------------------- the reply path: one model call, rewrite once, then repair
def test_a_claim_question_needs_one_model_call_and_the_input_carries_the_assessment():
    model = Chat(("text", GOOD))
    res = ask(model, "How much will be paid?")
    assert res.status == "ok" and res.reply == GOOD and res.sources == [] and model.n == 1
    assert res.guards["model_calls"] == 2 and res.guards["prerun"] is True and [t["tool"] for t in res.trace] == ["assess_claim"]       # the conversation and one response
    block = model.inputs[0]
    assert "Assessment already run" in block and "estimated_payment_once_documents_arrive" in block and "share_paid_percent" in block and "Claim facts" in block
    assert not any(w in block for w in ("result_id", "chunk_key", "customer_facts", "final_answer", "Excl0", "Annexure"))


def test_a_bad_reply_is_sent_back_once_with_the_problems_and_the_rewrite_is_used():
    model = Chat(("text", "The insured will get ₹9,999."), ("text", GOOD))
    res = ask(model, "How much will be paid?")
    assert res.reply == GOOD and res.guards["rewritten"] is True and res.guards["fixed"] is False and model.n == 2
    assert "Your reply has problems" in model.inputs[1] and "₹9,999" in model.inputs[1] or "9,999" in model.inputs[1]
    assert "the insured" in model.inputs[1].lower()


def test_if_the_rewrite_still_fails_the_text_is_repaired_in_code_never_shown_as_it_was():
    bad = "Your estimated payment is ₹1,22,125. The bill was ₹9,999. The Excl03 rule applies."
    res = ask(Chat(("text", bad), ("text", bad)), "How much will be paid?")
    assert res.reply.startswith("Your estimated payment is ₹1,22,125.") and "9,999" not in res.reply and "Excl03" not in res.reply and res.guards["fixed"] is True


def test_a_reply_with_no_problem_is_not_rewritten():
    res = ask(Chat(("text", "Your name on this claim is Rohan Verma.")), "what's my name")
    assert res.guards["rewritten"] is False and res.reply == "Your name on this claim is Rohan Verma."


def test_the_tool_loop_still_works_and_the_sources_are_the_policy_sections_retrieved():
    model = Chat(("call", "search_policy", {"query": "waiting period for cataract surgery"}), ("text", "Cataract surgery has a waiting period, except after an accident."))
    res = ask(model, "is cataract surgery covered")
    assert res.status == "ok" and res.guards["model_calls"] == 3 and [t["tool"] for t in res.trace] == ["assess_claim", "search_policy"]
    assert 1 <= len(res.sources) <= 3 and all(set(x) == {"title", "page"} and x["title"] for x in res.sources)
    assert not any(c in json.dumps(res.sources) for c in ("Excl0", "Annexure", "C.1.", "chunk", "optima-secure"))
    out = json.loads(model.inputs[1][0]["output"])
    assert set(out) == {"passages", "note"} and all(set(p) == {"section", "page", "text"} for p in out["passages"])


def test_no_policy_tool_means_no_sources():
    ctx = ctx_for()
    assert sources_for(ctx) == []
    call_tool("search_policy", {"query": "room rent"}, ctx)
    assert 1 <= len(sources_for(ctx)) <= 3


def test_at_most_three_distinct_sources():
    ctx = ctx_for()
    call_tool("search_policy", {"query": "waiting period exclusions room rent", "top_k": 8}, ctx)
    names = [s["title"] for s in sources_for(ctx)]
    assert len(names) <= 3 and len(set(names)) == len(names)


# ---------------------------------------------------------------- tools: clear JSON, plain field names, no final_answer
def test_the_tools_are_the_six_and_there_is_no_final_answer():
    assert TOOL_NAMES == {"search_policy", "get_clause", "check_waiting_period", "lookup_non_medical_item", "get_claim_summary", "assess_claim"} == {s["name"] for s in SCHEMAS}


def test_the_assessment_is_plain_json_with_the_reasons_behind_the_amounts():
    out = call_tool("assess_claim", {}, ctx_for())
    assert out["estimated_payment_once_documents_arrive"] == 122125 and out["payment_confirmed_today"] == 101625 and out["held_until_documents_arrive"] == 20500
    assert out["taken_off"] == {"room_rent": 12000, "doctor_and_other_associated_fees": 37875, "non_medical_items": 12500}
    assert out["room_rent"]["plan_limit_per_day"] == 5000 and out["room_rent"]["billed_per_day"] == 8000 and out["room_rent"]["share_paid_percent"] == 62.5
    assert out["non_medical_items"]["count"] == 12 and out["documents_missing"] and out["waiting_periods"] and "result_id" not in out and "evidence" not in out
    assert not any(w in json.dumps(out) for w in ("Excl0", "Annexure", "C.1.", "chunk_key"))


def test_a_what_if_shows_the_change_and_what_it_was_before():
    out = call_tool("assess_claim", {"what_if": {"room_rate_per_day": 5000}}, ctx_for())
    assert out["estimated_payment_once_documents_arrive"] == 172000 and out["before_the_change"]["estimated_payment"] == 122125 and out["what_if_changes"] == {"room_rate_per_day": 5000}


def test_the_other_tools_speak_plain_language_too():
    ctx = ctx_for()
    w = call_tool("check_waiting_period", {"first_policy_inception": "2025-03-01", "admission_date": "2026-07-15", "procedure": "cataract surgery"}, ctx)
    assert w["months_since_the_policy_started"] == 16 and any(r.get("can_be_claimed_from") == "2027-03-01" for r in w["rules"]) and "Excl0" not in json.dumps(w)
    n = call_tool("lookup_non_medical_item", {"item": "surgical gloves"}, ctx)
    assert n["on_the_policys_non_medical_list"] is True and "Annexure" not in json.dumps(n)
    f = call_tool("get_claim_summary", {}, ctx)
    assert f["insured"] == "Rohan Verma" and f["days_in_hospital"] == 4


# ---------------------------------------------------------------- the API shape
def test_assess_is_json_only():
    body = client.post("/assess", json={"sample_id": "TC07"}).json()
    assert set(body) == {"recommendation", "amounts", "assessment"} and body["amounts"]["estimated_payable_if_docs_supplied"] == 122125
    assert not any(k in json.dumps(body) for k in ("markdown", "sections", "citations", "Show more"))
