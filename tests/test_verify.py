"""Adversarial tests for the verification layer: a fake model that returns wrong figures, wrong verdicts and invented policy statements. All of them must be caught."""
import copy
import json

import pytest

from app.config import settings
from app.retrieval.azure_search import get_retriever
from app.tools import verify
from app.tools.guards import check_reply, fix_reply
from app.tools.registry import TurnContext, call_tool, precompute
from tests.test_hardening_stage4 import customer_session
from tests.test_purechat import Chat, ask

SAMPLES = json.load(open(settings.data_dir / "sample_claims.json", encoding="utf-8"))


def ctx_for(question, session=None):
    ctx = TurnContext(session=session or customer_session(), retriever=get_retriever(), question=question)
    precompute(ctx)
    return ctx


def late_session():
    """TC13: admitted 10 Apr 2026, after the policy period ended on 14 Mar 2026."""
    return {"id": "s", "history": [], "uin": settings.default_uin, "claim": copy.deepcopy(SAMPLES["TC13"]["claim"])}


def kinds(text, question="hello", session=None, tools=()):
    ctx = ctx_for(question, session)
    for name, args in tools:
        call_tool(name, args, ctx)
    return [k for k, _ in check_reply(text, ctx)]


# ---------------------------------------------------------------- wrong figures (the number guard)
@pytest.mark.parametrize("text", ["Your estimated payment is ₹1,32,125.", "12 items come to ₹15,500.", "Your policy ends on 14 Mar 2027.", "You stayed 6 days.", "The share paid is 72.5%.", "You have 7 documents missing."])
def test_wrong_figures_are_caught(text):
    assert "numbers" in kinds(text, "what is my claim")


def test_durations_and_counts_in_the_customers_own_words_are_fine():
    assert "numbers" not in kinds("You stayed 4 days and 12 items are extras.", "what is my claim")


# ---------------------------------------------------------------- wrong verdicts (G3)
@pytest.mark.parametrize("text,rule", [
    ("Your policy was in force on the admission date.", "policy in force"),
    ("Your claim is within the policy period.", "policy in force"),
])
def test_a_verdict_that_contradicts_the_policy_in_force_result_is_caught(text, rule):
    ks = kinds(text, "was my policy active", late_session())
    assert "verdict" in ks


def test_the_opposite_direction_too():
    assert "verdict" in kinds("Your policy had expired before you were admitted.", "was my policy active on 10 Sep 2025")
    assert "verdict" not in kinds("Your policy was in force on 10 Sep 2025.", "was my policy active on 10 Sep 2025")


def test_filing_verdicts(monkeypatch):
    monkeypatch.setenv("ARC_TODAY", "2026-09-21")
    assert "verdict" in kinds("Your documents are on time.", "is my claim late")
    assert "verdict" not in kinds("Your documents are being sent 372 days after discharge, which is late; a claims officer reviews it.", "is my claim late")
    monkeypatch.setenv("ARC_TODAY", "2025-09-20")
    assert "verdict" in kinds("Your claim is late and past the deadline.", "is my claim late")


def test_waiting_period_verdicts():
    tools = [("check_waiting_period", {"first_policy_inception": "2024-03-15", "admission_date": "2025-12-10", "procedure": "cataract surgery"})]     # 21 months: not yet served
    assert "verdict" in kinds("The 24-month waiting period is over for December 2025.", "is cataract covered", tools=tools)
    assert "verdict" not in kinds("The 24-month waiting period is not yet over; it is served on 15 Mar 2026.", "is cataract covered", tools=tools)
    served = [("check_waiting_period", {"first_policy_inception": "2024-03-15", "admission_date": "2026-05-10", "procedure": "cataract surgery"})]
    assert "verdict" in kinds("The waiting period has not been served yet.", "is cataract covered", tools=served)


def test_document_and_non_medical_verdicts():
    assert "verdict" in kinds("All your documents are received.", "what is missing")
    assert "verdict" in kinds("Nothing is missing.", "what is missing")
    assert "verdict" in kinds("The non-medical items are payable.", "what about gloves")
    assert "verdict" not in kinds("The non-medical items are not payable.", "what about gloves")
    assert "verdict" in kinds("Your claim looks likely to be paid.", "will i get this", late_session())


def test_a_hypothetical_is_not_a_verdict():
    assert "verdict" not in kinds("If you were admitted before 14 Mar 2026 your policy would have been in force.", "what if", late_session())
    assert "verdict" not in kinds("Assuming the bill is ₹3,00,000, your policy was active.", "what if", late_session())


def test_the_fix_replaces_the_sentence_with_one_built_from_the_tool_result():
    ctx = ctx_for("was my policy active", late_session())
    fixed = fix_reply("Good news. Your policy was in force on the admission date. Anything else?", ctx)
    assert "Your policy was not in force on the admission date (10 Apr 2026)" in fixed and "was in force on the admission date." not in fixed.replace("was not in force on the admission date", "")
    assert fixed.startswith("Good news.") and fixed.endswith("Anything else?")


def test_end_to_end_a_wrong_verdict_is_sent_back_once_then_replaced_in_code():
    wrong = "Your policy was in force on the admission date."
    s = late_session()
    r = ask(Chat(("text", wrong), ("text", wrong)), "was my policy active on the admission date?", s)
    assert r.guards["rewritten"] and r.guards["fixed"] and "was not in force on the admission date (10 Apr 2026)" in r.reply
    ok = ask(Chat(("text", wrong), ("text", "Your policy was not in force on the admission date (10 Apr 2026): it is after the policy period 15 Mar 2025 to 14 Mar 2026.")), "was my policy active?", late_session())
    assert ok.guards["rewritten"] and not ok.guards["fixed"] and ok.reply.startswith("Your policy was not in force")


# ---------------------------------------------------------------- invented policy statements (G4)
def test_a_policy_statement_with_nothing_retrieved_is_unsupported():
    for text in ("Maternity expenses are covered after a 9 month waiting period.", "Transplant expenses are covered up to ₹2,00,000 under your plan.", "Dental treatment is excluded from the policy.",
                 "The waiting period for cataract is 12 months."):
        assert "policy" in kinds(text, "what does my policy cover"), text


def test_a_statement_that_the_retrieved_passage_supports_passes():
    ctx = ctx_for("is cataract surgery covered")
    call_tool("search_policy", {"query": "waiting period cataract surgery"}, ctx)
    assert any("24 months" in c.text or "24 month" in c.text for c in ctx.seen.values()), "the local index must hold the 24-month rule for this test"
    assert [k for k, _ in check_reply("Cataract surgery has a 24 months waiting period, except after an accident.", ctx) if k == "policy"] == []
    assert "policy" in [k for k, _ in check_reply("Cataract surgery has a 18 months waiting period.", ctx)]        # the wrong duration is not in any passage


def test_facts_from_the_documents_support_statements_about_them():
    assert "policy" not in kinds("Your room rent limit is up to 1% of the base sum insured per day, which is ₹5,000 per day.", "what is my room rent limit")
    assert "policy" not in kinds("Your plan has no co-payment and no deductible.", "do i have a co-pay")


def test_unsupported_policy_sentences_are_dropped_and_an_empty_reply_says_it_cannot_confirm():
    ctx = ctx_for("what does my policy cover")
    fixed = fix_reply("Your claim looks likely. Maternity expenses are covered after 9 months.", ctx)
    assert "Maternity" not in fixed and fixed.startswith("Your claim looks likely")
    assert fix_reply("Maternity expenses are covered after 9 months.", ctx) == verify.CANNOT_CONFIRM


def test_end_to_end_an_invented_policy_statement_never_reaches_the_customer():
    bad = "Dental treatment is covered under your plan up to a limit."
    r = ask(Chat(("text", bad), ("text", bad)), "does my plan cover dental treatment?", customer_session())
    assert "dental" not in r.reply.lower() and r.reply == verify.CANNOT_CONFIRM and r.guards["fixed"]


# ---------------------------------------------------------------- names, hedging, repeats
def test_names_of_plans_and_hospitals_must_be_the_customers():
    assert "names" in kinds("Your Optima Super Secure plan pays room rent at actuals.", "what plan")
    assert "names" in kinds("You were treated at Apollo Hospital.", "which hospital")
    assert "names" not in kinds("You were treated at Riverside Multispeciality Hospital (DEMO) on your Optima Lite plan.", "which hospital")
    assert "Apollo" not in fix_reply("You were treated at Apollo Hospital. Anything else?", ctx_for("which hospital"))


@pytest.mark.parametrize("text", ["Your claim is approved.", "Your claim has been approved.", "₹1,01,625 is confirmed.", "The extras are not payable.", "That isn't covered."])
def test_unhedged_outcomes_and_approved_confirmed_are_caught(text):
    assert "decision" in kinds(text, "how is my claim")


def test_hedged_outcomes_pass_and_the_repair_hedges():
    assert "decision" not in kinds("The extras would likely not be payable, and I can't approve a claim; a claims officer decides.", "how is my claim")
    assert fix_reply("The extras are not payable.", ctx_for("which extras")) == "The extras would likely not be payable."


def test_a_note_already_shown_is_not_shown_again():
    hist = [dict(user="q", reply="Your claim looks likely to be paid.\n\n> You can drop the prescription anywhere on this page.")]
    out = verify.suppress_repeats("It rose to ₹1,60,000.\n\n> You can drop the prescription anywhere on this page.", hist)
    assert out == "It rose to ₹1,60,000."
    assert verify.suppress_repeats("You can drop the prescription anywhere on this page.", hist) == "You can drop the prescription anywhere on this page."   # nothing else left: kept
    assert verify.suppress_repeats("Your claim looks likely to be paid ₹1,22,125.", hist) == "Your claim looks likely to be paid ₹1,22,125."           # figures are never treated as a repeated note


# ---------------------------------------------------------------- dates the customer mentions are checked in code; ordinary explanation is not policy
def notes(question):
    return ctx_for(question).tool_outputs[0].get("dates_you_mentioned", [])


def test_a_date_the_customer_mentions_is_checked_against_the_policy_period_in_code():
    assert notes("was my policy active on 20 April 2026?") == ["Your policy was not in force on that date (20 Apr 2026): it is after the end of the policy period 15 Mar 2025 to 14 Mar 2026."]
    assert notes("was my policy active on 10 Sep 2025?")[0].startswith("Your policy was in force on that date (10 Sep 2025)")
    both = notes("what about 10/09/2025 and 2026-04-20?")
    assert len(both) == 2 and any(n.startswith("Your policy was in force") for n in both) and any("not in force" in n and "20 Apr 2026" in n for n in both)


def test_a_month_the_customer_mentions_is_placed_against_the_policy_period():
    assert notes("my policy expired in march 2026") == ["March 2026 is only partly inside your policy period (15 Mar 2025 to 14 Mar 2026): 1 Mar 2026 to 14 Mar 2026 is inside it, the rest is not."]
    assert notes("surgery in December 2025")[0].startswith("All of December 2025 is inside your policy period")
    assert notes("surgery in May 2026")[0].startswith("May 2026 is after the end of your policy period")
    assert notes("how much will be paid") == []


def test_the_dates_are_tool_results_so_the_reply_may_state_them():
    ctx = ctx_for("was my policy active on 20 April 2026?")
    assert "numbers" not in [k for k, _ in check_reply("Your policy was not in force on 20 Apr 2026: it ended on 14 Mar 2026.", ctx)]


def test_ordinary_explanation_is_not_dropped_as_a_policy_statement():
    ctx = ctx_for("why is my payment lower")
    text = "Your payment is lower because part of the room charge was withheld in proportion to the plan's limit, and the extras disappear from the total."
    assert "policy" not in [k for k, _ in check_reply(text, ctx)] and fix_reply(text, ctx).startswith("Your payment is lower")


def test_a_sentence_about_another_date_is_judged_against_that_date_not_the_admission_date():
    q = "was my policy active on 20 April 2026?"
    assert "verdict" not in kinds("**No — your policy was not active on 20 April 2026.**", q)                                  # right: the period ended 14 Mar 2026
    assert "verdict" in kinds("Yes, your policy was in force on 20 April 2026.", q)                                              # wrong for that date
    assert "verdict" in kinds("Your policy was not in force on 10 Sep 2025.", q)                                                 # wrong for the admission date
    fixed = fix_reply("Yes, your policy was in force on 20 April 2026.", ctx_for(q))
    assert "Your policy was not in force on that date (20 Apr 2026)" in fixed
