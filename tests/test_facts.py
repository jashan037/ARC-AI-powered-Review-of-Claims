"""Everything intake read reaches the model: the facts block, its labels, the number guard's allowed set, and the policy-in-force check."""
import copy
import json

import pytest

from app.agent.runner import _claim_block
from app.config import settings
from app.retrieval.azure_search import get_retriever
from app.tools import claims_engine as E
from app.tools import facts
from app.tools.guards import allowed_numbers, check_reply
from app.tools.number_guard import offenders, scan
from app.tools.registry import TurnContext, assessment_view, call_tool, precompute
from tests.test_hardening_stage4 import customer_session
from tests.test_purechat import Chat, ask

SAMPLES = json.load(open(settings.data_dir / "sample_claims.json", encoding="utf-8"))


def ctx_for(question, session=None):
    ctx = TurnContext(session=session or customer_session(), retriever=get_retriever(), question=question)
    precompute(ctx)
    return ctx


def kinds(text, question):
    return [k for k, _ in check_reply(text, ctx_for(question))]


SCHEDULE = ["Policy number: SYN-2805-0000-0001", "Plan: Optima Lite", "Insured person: Rohan Verma", "Age: 28", "Premium tier: Tier 2 (Rest of India)", "Base sum insured: ₹5,00,000",
            "Cumulative bonus: ₹50,000", "Room rent limit: Up to 1% of base sum insured per day = Rs. 5,000 per day", "ICU limit: Up to 2% of base sum insured per day = Rs. 10,000 per day",
            "Pre-hospitalization cover: 30 days", "Post-hospitalization cover: 60 days", "Aggregate deductible: None (Nil)", "Co-payment: None (Nil)",
            "Protect Benefit (non-medical expenses): Not opted", "Plus Benefit: Not opted", "Automatic restore benefit: Unlimited times", "Permanent exclusions: None",
            "Pre-existing diseases declared: None", "PED waiting period: 36 months from first policy inception",
            "Policy start (current policy period): 15 Mar 2025", "Policy expiry (end of current policy period): 14 Mar 2026", "First policy inception", "15 Mar 2024"]
STAY = ["Hospital: Riverside Multispeciality Hospital (DEMO)", "Admission: 10 Sep 2025 at 14:30", "Discharge: 14 Sep 2025 at 11:00", "Days stayed: 4", "Diagnosis: Acute appendicitis",
        "Procedure: Laparoscopic appendectomy", "Amount claimed: ₹1,84,500", "Hospital bill total: ₹1,84,500", "Documents received:", "Documents still missing: Doctor's prescription for the pharmacy bills",
        "Policy in force on the admission date: Your policy was in force on the admission date (10 Sep 2025)"]


def test_the_facts_block_has_every_field_of_every_document():
    text = facts.facts_text(customer_session())
    for line in SCHEDULE + STAY:
        assert line in text, line
    assert "Not found in the documents: nothing required is missing" in text


def test_expiry_and_start_are_labelled_so_every_wording_matches():
    text = facts.facts_text(customer_session())
    assert "Policy expiry (end of current policy period): 14 Mar 2026" in text and "Policy start (current policy period): 15 Mar 2025" in text


def test_a_field_intake_could_not_read_goes_to_the_not_found_list():
    s = customer_session()
    for k in ("premium_tier", "policy_period"):
        s["documents"]["policy_schedule"]["fields"].pop(k, None)
    s["claim"]["policy_period"] = None
    f = facts.build(s)
    assert "Premium tier" in f["not_found"] and "Policy expiry (end of current policy period)" in f["not_found"]
    assert "Not found in the documents: " in facts.facts_text(s) and "Premium tier" in facts.facts_text(s)


def test_the_model_gets_the_block_every_turn_and_get_claim_summary_returns_it():
    s = customer_session()
    m = Chat(("text", "Your policy expires on 14 Mar 2026."), ("text", "Your policy started on 15 Mar 2025."))
    ask(m, "when does my policy expire", s)
    ask(m, "when did my policy start", s)
    for i in m.inputs:
        assert "Policy expiry (end of current policy period): 14 Mar 2026" in i and "Policy number: SYN-2805-0000-0001" in i
    out = call_tool("get_claim_summary", {}, ctx_for("x", s))
    assert out["loaded"] and "Policy expiry (end of current policy period): 14 Mar 2026" in out["claim_facts"] and out["not_found_in_the_documents"] == []


# ---------------------------------------------------------------- the number guard must not strip a correct answer
def test_every_value_in_the_block_is_in_the_number_guards_allowed_set():
    ctx = ctx_for("x")
    allowed = allowed_numbers(ctx)
    for title, rows in facts.build(ctx.session)["sections"]:
        for label, value in rows:
            assert offenders(f"{label}: {value}", allowed) == [], (label, value)
    assert offenders(facts.facts_text(ctx.session), allowed) == []
    assert scan(facts.facts_text(ctx.session))["dates"], "the block has dates, so the check above is not vacuous"


@pytest.mark.parametrize("question,answer", [
    ("when does my policy expire", "Your policy expires on 14 Mar 2026."), ("when does my policy expire", "It is valid till 14/03/2026."), ("when does my policy expire", "Expiry: March 14, 2026."),
    ("when did my policy start", "Your current policy period started on 15 March 2025."), ("what is my sum insured", "Your base sum insured is ₹5,00,000, plus a ₹50,000 bonus."),
    ("what is my room rent limit", "Your room rent limit is ₹5,000 per day (1% of the base sum insured)."), ("what is my icu limit", "ICU is limited to ₹10,000 per day."),
    ("what are my pre and post hospitalization days", "30 days before and 60 days after."), ("how old am I", "You are 28."),
    ("was my policy active on 10 Sep 2025", "Yes, your policy was in force on 10 Sep 2025: it falls within 15 Mar 2025 to 14 Mar 2026."),
    ("what is the ped waiting period", "36 months from your first policy inception, 15 Mar 2024."), ("how much was my hospital deposit", "You paid ₹50,000 in advance and ₹1,34,500 at the end."),
    ("what is my premium tier", "Tier 2 (Rest of India)."), ("how many days was I in hospital", "You stayed 4 days.")])
def test_a_correct_answer_from_the_block_is_not_flagged_by_the_number_guard(question, answer):
    assert "numbers" not in kinds(answer, question)


def test_a_wrong_date_is_still_flagged():
    assert "numbers" in kinds("Your policy expires on 14 Mar 2027.", "when does my policy expire")


# ---------------------------------------------------------------- the sample schedule, question by question (what the model is given and what the code answers)
@pytest.mark.parametrize("question,line", [
    ("when does my policy expire", "Policy expiry (end of current policy period): 14 Mar 2026"), ("when did my policy start", "Policy start (current policy period): 15 Mar 2025"),
    ("what is my policy number", "Policy number: SYN-2805-0000-0001"), ("what is my sum insured", "Base sum insured: ₹5,00,000"),
    ("do I have a co-pay or deductible", "Co-payment: None (Nil)"), ("what is my room rent limit", "Room rent limit: Up to 1% of base sum insured per day = Rs. 5,000 per day"),
    ("was my policy active on 10 Sep 2025", "Policy in force on the admission date: Your policy was in force on the admission date (10 Sep 2025): it falls within the policy period 15 Mar 2025 to 14 Mar 2026.")])
def test_the_line_that_answers_each_question_is_in_the_model_input(question, line):
    s = customer_session()
    assert line in _claim_block(s, None)
    if "deductible" in question:
        assert "Aggregate deductible: None (Nil)" in _claim_block(s, None)


def test_the_customer_states_a_wrong_expiry_the_reply_must_not_open_with_yes_or_say_it_is_missing():
    q = "will I get this claim as my policy expired in march 2026"
    assert "decision" in kinds("Yes, you will get this claim.", q) and "decision" in kinds("No. Your policy has expired.", q)
    ok = "It appears likely: your policy period runs from 15 Mar 2025 to 14 Mar 2026, and you were admitted on 10 Sep 2025, inside it. About ₹1,22,125 once your documents arrive; ₹1,01,625 is counted so far. Your insurer's team decides."
    assert kinds(ok, q) == []


def test_a_yes_that_repeats_after_the_rewrite_is_repaired_in_code():
    q = "will I get this claim as my policy expired in march 2026"
    s = customer_session()
    m = Chat(*[("text", "Yes, it appears likely because your policy period ends on 14 Mar 2026.")] * 2)
    r = ask(m, q, s)
    assert not r.reply.lower().startswith("yes") and "14 Mar 2026" in r.reply and r.guards["rewritten"] and r.guards["fixed"]


# ---------------------------------------------------------------- policy in force on the admission date
def test_the_sample_claim_is_in_force():
    r = E.policy_in_force(SAMPLES["TC07"]["claim"])
    assert r["in_force"] and r["relation"] == "within"


def test_a_claim_admitted_after_the_policy_end_is_flagged_as_not_covered():
    c = SAMPLES["TC13"]["claim"]
    res = E.assess(c)
    assert res["recommendation"] == "likely_not_payable" and res["coverage"]["status"] == "not_covered"
    assert res["amounts"]["estimated_payable_if_docs_supplied"] == 0 and res["amounts"]["payable_confirmed_now"] == 0
    view = assessment_view(res)
    assert next(iter(view)) == "policy_in_force_on_the_admission_date" and "not in force" in view["policy_in_force_on_the_admission_date"] and "10 Apr 2026" in view["policy_in_force_on_the_admission_date"]
    assert any("Policy in force" in i for i in view["issues"])


def test_admission_before_the_policy_start_is_flagged_too():
    c = copy.deepcopy(SAMPLES["TC07"]["claim"])
    c["admission_datetime"], c["discharge_datetime"] = "2025-03-01T10:00", "2025-03-05T10:00"
    assert E.policy_in_force(c)["relation"] == "before" and E.assess(c)["recommendation"] == "likely_not_payable"


def test_a_claim_without_a_policy_period_is_not_assumed_in_or_out():
    c = copy.deepcopy(SAMPLES["TC07"]["claim"])
    c.pop("policy_period")
    assert E.policy_in_force(c) is None
    res = E.assess(c)
    assert res["recommendation"] == E.assess(SAMPLES["TC07"]["claim"])["recommendation"]
    assert "could not be checked" in assessment_view(res)["policy_in_force_on_the_admission_date"]


def test_the_facts_block_says_when_the_policy_was_not_in_force():
    s = {"id": "s", "history": [], "claim": copy.deepcopy(SAMPLES["TC13"]["claim"])}
    assert "Your policy was not in force on the admission date (10 Apr 2026)" in facts.facts_text(s)


def test_the_number_guard_accepts_the_in_force_sentence_for_a_what_if_date():
    ctx = ctx_for("what if I was admitted on 10 Apr 2026")
    out = call_tool("assess_claim", {"what_if": {"admission_datetime": "2026-04-10T10:00"}}, ctx)
    assert "not in force" in out["policy_in_force_on_the_admission_date"]
    assert "numbers" not in [k for k, _ in check_reply("Your policy would not be in force on 10 Apr 2026, after 14 Mar 2026.", ctx)]
