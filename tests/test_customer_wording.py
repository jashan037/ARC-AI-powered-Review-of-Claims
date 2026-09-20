"""What the customer reads: plain wording, no officer instructions, no clause numbers or policy jargon. The officer wording stays as it was."""
import json

import pytest

from app.config import settings
from app.retrieval.azure_search import get_retriever
from app.tools.registry import TurnContext, call_tool, render_final

SAMPLES = json.load(open(settings.data_dir / "sample_claims.json", encoding="utf-8"))
R = get_retriever()
JARGON = ("Annexure B", "D.1.19", "B.2.7", "Protect Benefit is not in force", "E.1.7", "aggregate deductible", "on hold")
OFFICER_VOICE = ("Request the", "from the insured", "Re-run", "Verify", "the officer decides", "before communicating a rejection", "judgement", "Route to")


def render(sample, answer, audience, **final):
    ctx = TurnContext(session={"claim": SAMPLES[sample]["claim"], "uin": settings.default_uin, "history": [], "audience": audience}, retriever=R)
    rid = call_tool("assess_claim", {}, ctx)["result_id"]
    assert call_tool("final_answer", dict(answer_type=answer, headline="Short headline.", result_id=rid, **final), ctx) == {"status": "accepted"}
    return render_final(ctx.final, ctx)


def test_documents_summary_gives_the_customer_the_plain_request_not_the_models_officer_steps():
    steps = ["Request original medicine bills from the insured.", "Verify prescriptions are signed and dated.", "Re-run the claim assessment after upload."]
    customer = render("TC07", "documents_answer", "customer", next_steps=steps).summary_markdown
    assert "Please send the missing prescription." in customer
    for officer_only in OFFICER_VOICE:
        assert officer_only.lower() not in customer.lower(), officer_only
    officer = render("TC07", "documents_answer", "officer", next_steps=steps).summary_markdown
    assert all(step in officer for step in steps) and "The officer decides." in officer                     # the officer still sees the model's steps


def test_documents_summary_with_nothing_missing_has_no_next_steps_for_a_customer():
    s = render("TC01", "documents_answer", "customer", next_steps=["Verify everything."]).summary_markdown
    assert "**All 9 documents are complete.**" in s and "Next steps" not in s and "Verify" not in s


def test_deduction_summary_for_a_customer_has_no_clause_numbers_or_jargon():
    s = render("TC07", "deduction_explanation", "customer", focus="all").summary_markdown
    assert "12 billed items are non-medical extras that your policy doesn't pay for, so ₹12,500 is not payable." in s
    assert "is held because the prescription is missing" in s and "₹1,22,125" in s and "62.5%" in s
    for word in JARGON:
        assert word not in s, word
    o = render("TC07", "deduction_explanation", "officer", focus="all").summary_markdown
    assert "appear in Annexure B" in o and "The officer decides." in o                                          # the officer version is unchanged


def test_deductible_and_protect_benefit_wording_for_a_customer():
    s = render("TC08", "deduction_explanation", "customer", focus="all").summary_markdown
    assert "You pay the first ₹25,000 of the claim yourself (your deductible) before the insurer pays." in s and "D.1.19" not in s and "B.2.7" not in s
    assert "D.1.19" in render("TC08", "deduction_explanation", "officer", focus="all").summary_markdown
    p = render("TC12", "deduction_explanation", "customer", focus="all").summary_markdown
    assert "are payable because your policy includes the Protect Benefit." in p and "Annexure" not in p


@pytest.mark.parametrize("sample", sorted(SAMPLES))
def test_no_customer_assessment_reads_like_an_instruction_to_an_officer(sample):
    s = render(sample, "claim_assessment", "customer").summary_markdown
    for officer_only in OFFICER_VOICE + ("Annexure B", "subject to human review", "For the officer"):
        assert officer_only.lower() not in s.lower(), (sample, officer_only)
    assert "The officer decides" not in s


def test_a_customer_is_told_a_claims_officer_confirms_before_anything_is_decided():
    assert "A claims officer will confirm this before any decision is made." in render("TC02", "claim_assessment", "customer").summary_markdown
    assert "A claims officer will review" in render("TC10", "claim_assessment", "customer").summary_markdown
