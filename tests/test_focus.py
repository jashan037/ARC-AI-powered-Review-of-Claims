"""The focus guard (the topic of the question decides what the answer leads with) and the timing guard (a past treatment cannot be waited for)."""
import datetime as dt

import pytest

from app.tools import claims_engine as E
from app.tools import focus, timing
from app.tools.guards import check_reply, fix_reply
from app.tools.registry import TurnContext, call_tool, precompute
from app.retrieval.azure_search import get_retriever
from tests.test_hardening_stage4 import customer_session


def ctx_for(question, session=None):
    ctx = TurnContext(session=session or customer_session(), retriever=get_retriever(), question=question)
    precompute(ctx)
    return ctx


def kinds(text, question):
    return [k for k, _ in check_reply(text, ctx_for(question))]


# ---------------------------------------------------------------- the topic of a question
@pytest.mark.parametrize("question,topic", [
    ("which items are not payable?", "non_medical"),
    ("what is not covered on my bill?", "non_medical"),
    ("what are the extras you took off?", "non_medical"),
    ("why was my room rent reduced?", "room"),
    ("why were the doctor fees cut?", "doctor_fees"),
    ("how much is being held and why?", "held"),
    ("do I have a co-pay or deductible?", "deductible"),
    ("how much will be paid?", "overall"),
    ("explain my claim", "overall"),
    ("which hospital was I in?", None),
    ("is cataract surgery covered?", None),
    ("when does my policy expire?", None),
])
def test_the_question_decides_the_topic(question, topic):
    assert focus.of(question) == topic


# ---------------------------------------------------------------- the extras question leads with the extras (the old P1-4 complaint)
NON_PAYABLE = ("**₹12,500** would likely not be payable: 12 items on your bill are extras your plan doesn't cover. The largest are Attendant food charges ₹2,800, "
               "Surgical gloves ₹2,400 and Service charges ₹2,000. The other 9 come to ₹5,300.")


def test_the_extras_answer_needs_the_total_the_count_the_three_largest_and_the_rest():
    assert "focus" not in kinds(NON_PAYABLE, "which items are not payable?")
    # the room-rent explanation first, with the extras only at the end: wrong lead
    bad = "Your room was billed ₹8,000 a day against your plan's ₹5,000 a day, so the room and the doctor, theatre and nursing charges come down by ₹49,875.\n\nExtras are ₹12,500."
    assert "focus" in kinds(bad, "which items are not payable?")
    fixed = fix_reply(bad, ctx_for("which items are not payable?"))
    assert fixed.startswith("₹12,500 of your bill is extras") and "Attendant food charges ₹2,800" in fixed and "The other 9 come to ₹5,300." in fixed


def test_a_missing_figure_of_the_topic_is_added_in_code():
    ctx = ctx_for("which items are not payable?")
    thin = "**₹12,500** of your bill would likely not be payable."
    assert "focus" in [k for k, _ in check_reply(thin, ctx)]
    fixed = fix_reply(thin, ctx)
    assert "12 items" in fixed and "Surgical gloves ₹2,400" in fixed


@pytest.mark.parametrize("question,figures", [
    ("why was my room rent reduced?", ["₹5,000", "₹8,000"]),
    ("how much is being held and why?", ["₹20,500"]),
    ("why were the doctor fees reduced?", ["₹37,875"]),
])
def test_each_topic_has_its_own_required_figures(question, figures):
    ctx = ctx_for(question)
    fixed = fix_reply("Some of your bill was reduced.", ctx)
    for f in figures:
        assert f in fixed, (question, f, fixed)


def test_a_what_if_is_left_to_the_what_if_rules():
    ctx = ctx_for("what if the room rent was 5000 a day?")
    call_tool("assess_claim", {"what_if": {"room_rate_per_day": 5000}}, ctx)
    assert focus.plan(ctx) is None


# ---------------------------------------------------------------- a claim whose policy was not in force leads with that
def not_in_force_session():
    s = customer_session()
    c = dict(s["claim"])
    c["policy_period"] = ["2025-03-15", "2026-03-14"]
    c["admission_datetime"], c["discharge_datetime"] = "2026-04-20T14:30", "2026-04-24T11:00"
    s["claim"] = c
    return s


def test_a_claim_that_was_not_in_force_leads_with_that_not_with_an_amount():
    ctx = ctx_for("how much will be paid?", not_in_force_session())
    assert ctx.results["assessment"]["recommendation"] == "likely_not_payable"
    bad = "About ₹1,22,125 of your bill looks payable once your documents arrive."
    assert "focus" in [k for k, _ in check_reply(bad, ctx)]
    fixed = fix_reply(bad, ctx)
    assert fixed.startswith("Your policy was not in force on the admission date")
    good = "Your policy was not in force on the admission date (20 Apr 2026): it is after the end of the policy period 15 Mar 2025 to 14 Mar 2026."
    assert "focus" not in [k for k, _ in check_reply(good, ctx)]


# ---------------------------------------------------------------- timing: nobody can wait for a treatment that already happened
def test_a_treatment_already_past_is_never_something_to_wait_for(monkeypatch):
    monkeypatch.setenv("ARC_TODAY", "2026-09-21")
    ctx = ctx_for("what if I had cataract surgery in December 2025?")
    call_tool("check_waiting_period", {"first_policy_inception": "2024-03-15", "admission_date": "2025-12-10", "procedure": "cataract surgery"}, ctx)
    assert timing.all_past(ctx) == dt.date(2025, 12, 31)
    bad = "You would need to wait until 15 Mar 2026 for cataract surgery."
    assert "timing" in [k for k, _ in check_reply(bad, ctx)]
    fixed = fix_reply(bad, ctx)
    assert "wait until" not in fixed and "15 Mar 2026" in fixed and "already" not in fixed.split("\n")[0]


def test_a_future_treatment_may_still_be_waited_for(monkeypatch):
    monkeypatch.setenv("ARC_TODAY", "2025-01-01")
    ctx = ctx_for("what if I had cataract surgery in December 2025?")
    assert timing.all_past(ctx) is None
    assert "timing" not in [k for k, _ in check_reply("You would need to wait until 15 Mar 2026.", ctx)]


def test_the_accident_exception_is_a_condition_not_a_verdict():
    ctx = ctx_for("is cataract surgery covered?")
    assert "timing" in [k for k, _ in check_reply("Accidents are exempt from this waiting period.", ctx)]
    assert "this waiting period wouldn't apply if the condition was caused by an accident" in fix_reply("Accidents are exempt from this waiting period.", ctx)
    assert "timing" not in [k for k, _ in check_reply("It wouldn't apply if the condition was caused by an accident.", ctx)]


def test_never_says_this_claim_was_an_accident_when_the_documents_say_illness():
    ctx = ctx_for("is there a 30 day waiting period?")
    assert ctx.claim["is_accident"] is False
    bad = "The 30-day rule doesn't apply here. Your admission was an accident, so it is exempt."
    assert "timing" in [k for k, _ in check_reply(bad, ctx)]
    assert "was an accident" not in fix_reply(bad, ctx)


def test_the_engine_still_owns_the_dates():
    assert E.today() == dt.date.fromisoformat("2025-01-01")     # tests/conftest.py sets ARC_TODAY
