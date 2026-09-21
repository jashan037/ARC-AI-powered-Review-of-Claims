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
