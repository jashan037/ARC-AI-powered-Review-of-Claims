"""The focus of a deduction explanation follows the question, decided in code; the model is corrected when it disagrees."""
import pytest

from app.agent.runner import OfflineAgent
from tests.test_plain_questions import Script, agent, customer_session
from app.tools.focus import focus_for

CASES = {
    "Which items are not payable?": "non_medical", "What is not covered on my bill?": "non_medical", "Are gloves and masks paid?": "non_medical",
    "Why are the non-medical items deducted?": "non_medical", "Why was my room rent reduced?": "room", "Explain the room rent deduction": "room",
    "Why were my doctor fees cut?": "associated", "Why was the consultation charge reduced?": "associated", "Why is money held?": "hold",
    "Why is the prescription amount on hold?": "hold", "Is there a deductible on my claim?": "deductible", "Is a co-pay applied?": "deductible",
    "Why is my payment lower than the bill?": "all", "Why was I paid less than I claimed?": "all", "Why was my room rent reduced and which items are not payable?": "all",
}


@pytest.mark.parametrize("q,expected", CASES.items())
def test_the_question_decides_the_focus(q, expected):
    assert focus_for(q) == expected


@pytest.mark.parametrize("q", ["How much will be paid?", "What documents are missing?", "hello", "what's my name", "Is cataract surgery covered?", ""])
def test_a_question_that_does_not_point_at_a_part_gives_no_focus(q):
    assert focus_for(q) is None


def deduction(focus):
    return {"answer_type": "deduction_explanation", "headline": "x", "result_id": lambda m: m.rid, **({"focus": focus} if focus else {})}


def run(question, model_focus):
    final = {"answer_type": "deduction_explanation", "headline": "x", "focus": model_focus}
    model = Script([[("assess_claim", {})], [("final_answer", lambda m: dict(final, result_id=m.rid))]])
    return agent(model).ask(customer_session(), question)


def test_a_wrong_focus_from_the_model_is_corrected_for_not_payable_items():
    res = run("Which items are not payable?", "all")
    assert res.answer_type == "deduction_explanation" and "Non-medical items" in res.summary_markdown
    assert "Room rent: proportionate deduction" not in res.summary_markdown                # the room-rent working no longer comes first
    assert "Surgical gloves" in "".join(s["markdown"] for s in res.sections)


def test_the_room_focus_is_kept_for_a_room_question_even_when_the_model_says_all():
    res = run("Why was my room rent reduced?", "all")
    assert res.summary_markdown.startswith("**Room rent: proportionate deduction**")


def test_the_model_focus_stands_when_the_question_is_not_clear():
    res = run("Tell me more about that", "hold")
    assert "prescription" in res.summary_markdown.lower() or "held" in res.summary_markdown.lower()


def test_the_correction_is_recorded_for_the_evaluation():
    from app.tools.registry import TurnContext, _focus
    from app.retrieval.azure_search import get_retriever
    ctx = TurnContext(session=customer_session(), retriever=get_retriever(), question="Which items are not payable?")
    assert _focus({"focus": "room"}, ctx) == "non_medical" and ctx.focus_corrected == [("room", "non_medical")]
    assert _focus({"focus": "non_medical"}, ctx) == "non_medical" and len(ctx.focus_corrected) == 1     # agreeing is not a correction


def test_the_offline_stand_in_uses_the_same_focus():
    res = OfflineAgent().ask(customer_session(), "Which items are not payable?")
    assert res.answer_type == "deduction_explanation" and "Non-medical items" in res.summary_markdown
