"""Filing time (a review flag, never a rejection), the patient, renewal facts, and the both-figures rule for payment answers."""
import copy
import json

import pytest

from app import intake
from app.config import settings
from app.retrieval.azure_search import get_retriever
from app.tools import claims_engine as E
from app.tools import facts
from app.tools.guards import check_reply, fix_reply
from app.tools.registry import TurnContext, assessment_view, precompute
from tests.test_hardening_stage4 import customer_session

SAMPLES = json.load(open(settings.data_dir / "sample_claims.json", encoding="utf-8"))


def ctx_for(question):
    ctx = TurnContext(session=customer_session(), retriever=get_retriever(), question=question)
    precompute(ctx)
    return ctx


def test_filing_within_30_days_of_discharge_is_fine(monkeypatch):
    monkeypatch.setenv("ARC_TODAY", "2025-10-14")                       # discharge 14 Sep 2025: day 30 is the last day
    c = SAMPLES["TC07"]["claim"]
    r = E.filing_status(c)
    assert (r["days_since_discharge"], r["late"], r["due_by"]) == (30, False, "2025-10-14")
    assert E.assess(c)["recommendation"] == "likely_eligible_pending_documents"


def test_a_late_filing_is_a_review_flag_not_a_rejection(monkeypatch):
    monkeypatch.setenv("ARC_TODAY", "2026-09-21")                       # 372 days after the discharge of the sample claim
    c = SAMPLES["TC07"]["claim"]
    res = E.assess(c)
    assert E.filing_status(c)["days_since_discharge"] == 372 and E.filing_status(c)["late"]
    assert res["recommendation"] == "needs_human_review" and res["coverage"]["status"] == "needs_review"                       # flagged for a person, not "not payable"
    assert res["amounts"]["estimated_payable_if_docs_supplied"] == 122125 and res["amounts"]["payable_confirmed_now"] == 101625   # the money is untouched
    view = assessment_view(res)
    assert "flagged for a claims officer to review, not rejected" in view["time_limit_for_sending_documents"] and "372 days" in view["time_limit_for_sending_documents"]
    assert any("Time limit for sending documents" in i for i in view["issues"])


def test_the_first_message_says_so_when_the_filing_is_late(monkeypatch):
    monkeypatch.setenv("ARC_TODAY", "2026-09-21")
    s = customer_session()
    msg = intake.first_message(s["claim"], intake.build(s)["missing"])
    assert "**About timing:** Your documents are being sent 372 days after discharge; the policy asks for them within 30 days (by 14 Oct 2025)." in msg and "not rejected" in msg
    monkeypatch.setenv("ARC_TODAY", "2025-09-20")
    assert "About timing" not in intake.first_message(s["claim"], [])


def test_the_facts_carry_the_filing_check_the_patient_and_the_renewal(monkeypatch):
    monkeypatch.setenv("ARC_TODAY", "2026-09-21")
    text = facts.facts_text(customer_session())
    assert "- Time limit for sending documents: Your documents are being sent 372 days after discharge" in text
    assert "- Patient is the insured person: The patient named in your documents is the insured person." in text
    assert "- Next renewal would start on: 15 Mar 2026" in text and "Cover cannot be gained by renewing after the treatment" in text and "renewal schedule or payment proof for every year" in text


def test_a_patient_who_is_not_the_insured_person_is_flagged():
    c = copy.deepcopy(SAMPLES["TC07"]["claim"])
    c["insured_name"], c["patient_name"] = "Rohan Verma", "Meera Verma"
    res = E.assess(c)
    assert res["recommendation"] == "needs_human_review" and any(k["code"] == "PATIENT" and k["status"] == "needs_review" for k in res["checks"])
    c["patient_name"] = "Mr. ROHAN  VERMA"
    assert next(k for k in E.assess(c)["checks"] if k["code"] == "PATIENT")["status"] == "satisfied"


def test_arc_today_is_only_read_from_the_environment(monkeypatch):
    monkeypatch.delenv("ARC_TODAY")
    import datetime as dt
    assert E.today() == dt.date.today()


# ---------------------------------------------------------------- every payment answer with something held gives both figures
@pytest.mark.parametrize("q", ["how much will be paid?", "what will I get?", "why is my payment lower?"])
def test_a_payment_answer_needs_both_figures_when_something_is_held(q):
    ctx = ctx_for(q)
    assert "figures" in [k for k, _ in check_reply("About ₹1,22,125 looks payable.", ctx)] and "figures" in [k for k, _ in check_reply("₹1,01,625 is counted so far.", ctx)]
    assert "figures" not in [k for k, _ in check_reply("About ₹1,22,125 looks payable once your documents arrive; ₹1,01,625 is counted so far.", ctx)]
    fixed = fix_reply("About ₹1,22,125 looks payable.", ctx)
    assert "₹1,01,625 is counted so far" in fixed and fixed.startswith("About ₹1,22,125 looks payable.")


def test_other_questions_do_not_need_the_figures():
    for q in ("which items are not payable?", "which hospital was I in?", "what documents are missing?"):
        assert "figures" not in [k for k, _ in check_reply("You were treated at Riverside.", ctx_for(q))]


# ---------------------------------------------------------------- figures and mentions the answer must carry (found by the accuracy suite)
def test_a_what_if_that_changes_the_bill_must_state_the_new_bill():
    ctx = ctx_for("what if the room rent was 5000 a day?")
    from app.tools.registry import call_tool
    call_tool("assess_claim", {"what_if": {"room_rate_per_day": 5000}}, ctx)
    reply = "Your estimated payment would rise to ₹1,60,000, with ₹1,39,500 counted so far."
    assert "figures" in [k for k, _ in check_reply(reply, ctx)]
    assert "In that case your bill would be ₹1,72,500." in fix_reply(reply, ctx)
    ok = reply + " Your bill would be ₹1,72,500."
    assert "figures" not in [k for k, _ in check_reply(ok, ctx)]


def test_a_cover_left_question_is_answered_from_the_tool_worked_out_in_code():
    from app.tools.registry import question_amounts
    ctx = ctx_for("I already claimed 3 lakh earlier this policy year. How much cover do I have left after this claim?")
    left = ctx.tool_outputs[0]["cover_left"]
    assert (left["cover_left"], left["cover_amount"]) == (127875, 550000) and left["other_claims_you_mentioned"][0]["amount"] == 300000
    assert "figures" in [k for k, _ in check_reply("Your total cover is ₹5,50,000.", ctx)]
    fixed = fix_reply("Your total cover is ₹5,50,000.", ctx)
    assert "About ₹1,27,875 of your ₹5,50,000 cover would be left." in fixed and "assumed paid in full" in fixed
    assert ctx_for("how much cover will I have left after this claim?").tool_outputs[0]["cover_left"]["cover_left"] == 427875
    assert question_amounts("2 lakh and ₹3,00,000 and 1.5 crore and 250000 and 4 days") == [200000.0, 15000000.0, 300000.0, 250000.0]
    assert "cover_left" not in ctx_for("how much will be paid").tool_outputs[0]


def test_a_waiting_period_answer_mentions_the_accident_exception():
    for q in ("is there a 30 day waiting period?", "is cataract surgery covered?"):
        ctx = ctx_for(q)
        assert "figures" in [k for k, _ in check_reply("No, it does not apply to you.", ctx)]
        assert fix_reply("No, it does not apply to you.", ctx).endswith("Accidents are exempt from this waiting period.")
        assert "figures" not in [k for k, _ in check_reply("No; accidents are exempt anyway.", ctx)]
    assert "figures" not in [k for k, _ in check_reply("It is 36 months.", ctx_for("what is the waiting period for a condition I had before the policy?"))]


def test_when_the_customer_says_the_policy_ended_the_answer_states_what_the_documents_show():
    ctx = ctx_for("will I get this claim as my policy expired in march 2026?")
    assert "figures" in [k for k, _ in check_reply("It appears likely to be paid.", ctx)]
    assert "Your documents show the policy period ending on 14 Mar 2026." in fix_reply("It appears likely to be paid.", ctx)
    assert "figures" not in [k for k, _ in check_reply("Your policy period ends on 14 March 2026; you were admitted on 10 Sep 2025, inside it. It appears likely to be paid: about ₹1,22,125 once your documents arrive, ₹1,01,625 counted so far.", ctx)]
