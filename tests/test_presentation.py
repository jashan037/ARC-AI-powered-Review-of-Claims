"""What the model is shown of long clauses, what the officer sees as evidence, and that next steps are never decisions."""
import re

import pytest

from app.config import settings
from app.rendering.render import render_qa
from app.retrieval.azure_search import get_retriever
from app.retrieval.base import best_window
from app.tools.registry import TurnContext, call_tool, validate_final

R = get_retriever()          # conftest forces the local BM25 retriever
LIST = R.get_by_chunk_id("C1-b-list", settings.default_uin)


# ---------------------------------------------------------------- best_window
def test_short_text_is_returned_whole():
    assert best_window("a short   clause", "anything", 100) == "a short clause"


def test_a_long_text_is_windowed_around_the_query_terms_not_cut_at_the_start():
    text = "Illnesses: " + " ".join(f"- item{i} of the first part" for i in range(60)) + " Surgical procedures: - Adenoidectomy - Joint replacement surgeries - Hernia " + "- tail entry " * 60
    w = best_window(text, "is knee replacement covered", 300)
    assert "Joint replacement surgeries" in w and len(w) <= 300 + 4 and w.startswith("… ") and w.endswith(" …")
    assert "item1 of" not in w                                    # the head of the text is not what was asked about


def test_a_term_that_occurs_once_outweighs_a_term_that_occurs_everywhere():
    text = ("policy covers cover covered " * 40) + "the rare entry glaucoma surgery is listed here " + ("policy covers cover covered " * 40)
    assert "glaucoma" in best_window(text, "policy covered glaucoma", 200)


def test_no_matching_term_falls_back_to_the_head():
    text = "alpha beta gamma " * 100
    assert best_window(text, "zzzz qqqq", 60) == "alpha beta gamma alpha beta gamma alpha beta gamma alpha ..."[:len(best_window(text, "zzzz qqqq", 60))]
    assert best_window(text, "zzzz qqqq", 60).startswith("alpha beta gamma") and best_window(text, "zzzz qqqq", 60).endswith("...")


# ---------------------------------------------------------------- what the model is shown
def _ctx():
    return TurnContext(session={}, retriever=R)


def test_search_shows_the_procedures_half_of_the_specified_list_not_just_its_first_600_characters():
    assert LIST.text.index("Joint replacement") > 600                    # the entry sits past the old cut-off
    out = call_tool("search_policy", {"query": "Is knee replacement covered and what is the waiting period?", "top_k": 8}, _ctx())
    hit = next(r for r in out["results"] if r["chunk_key"].endswith(":C1-b-list"))
    assert "Joint replacement surgeries" in hit["excerpt"]


def test_get_clause_returns_a_long_clause_in_full():
    out = call_tool("get_clause", {"clause_ref": "E.1.7"}, _ctx())
    full = re.sub(r"\s+", " ", R.get_by_chunk_id("E1.7", settings.default_uin).text).strip()
    assert len(full) > 3000 and out["clauses"][0]["excerpt"] == full


# ---------------------------------------------------------------- evidence shown to the officer
def _qa(label, detail):
    final = dict(answer_type="coverage_answer", verdict="covered_with_conditions", headline="h",
                 points=[dict(label=label, status="warning", detail=detail, citations=[LIST.chunk_key])], citations=[LIST.chunk_key])
    return render_qa(final, R, settings.default_uin)


def test_the_evidence_quote_follows_the_point_that_cites_the_clause():
    about_procedure = _qa("Joint replacement is a listed surgical procedure", "Joint replacement surgeries are on the specified procedures list.")
    quote = about_procedure.citations[0]["excerpt"]
    assert "Joint replacement surgeries" in quote and "Joint replacement surgeries" in about_procedure.markdown.split("### Evidence")[1]   # the whole entry, not cut mid-phrase
    about_illness = _qa("Cataract is a listed illness", "Cataract and other disorders of lens and retina are on the specified illnesses list.")
    assert "Cataract" in about_illness.citations[0]["excerpt"]


def test_without_a_point_the_evidence_quote_is_still_the_start_of_the_clause():
    final = dict(answer_type="definition_answer", headline="h", citations=[LIST.chunk_key])
    assert _qa("x", "y") and render_qa(final, R, settings.default_uin).citations[0]["excerpt"].startswith("Illnesses")


# ---------------------------------------------------------------- next steps are never decisions
DECISIONS = ["Do not admit/pay under standard cover until 2027-03-01 unless the claim arises from an Accident.",
             "Reject the claim as the waiting period is not served.", "Pay the amount once the prescription arrives.",
             "If Protect Benefit is not in force, mark these items as non-payable per Annexure B (C.3.k).",
             "The insurer should not pay this.", "Approve the claim subject to documents.", "Deny the claim."]
CHECKS = ["Check the Policy Schedule for a waiting-period modification.", "Confirm the first policy inception date with the insured.",
          "Request the prescription for the pharmacy bills.", "Flag the conflicting admission dates for the claims officer.",
          "Verify whether Protect Benefit is in force before the bill review.", "Pay attention to the accident exception.",
          "Confirm with the claims officer before communicating any rejection."]


def _final(steps):
    return dict(answer_type="general_answer", headline="Hello.", next_steps=steps)


@pytest.mark.parametrize("step", DECISIONS)
def test_decision_wording_in_next_steps_is_rejected(step):
    errs = validate_final(_final([step]), _ctx())
    assert len(errs) == 1 and "never be a decision" in errs[0] and step[:20] in errs[0]


@pytest.mark.parametrize("step", CHECKS)
def test_things_to_check_are_accepted(step):
    assert validate_final(_final([step]), _ctx()) == []


def test_the_rephrase_request_is_made_once_then_the_second_attempt_stands():
    ctx = _ctx()
    first = call_tool("final_answer", _final([DECISIONS[0]]), ctx)
    assert "error" in first and "Rephrase" in first["problems"][0]
    assert call_tool("final_answer", _final([DECISIONS[0]]), ctx) == {"status": "accepted"}        # rejected once, not twice
    ctx2 = _ctx()
    assert "error" in call_tool("final_answer", _final([DECISIONS[0]]), ctx2)
    assert call_tool("final_answer", _final([CHECKS[0]]), ctx2) == {"status": "accepted"}
