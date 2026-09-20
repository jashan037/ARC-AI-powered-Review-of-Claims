"""The failure transcripts written by scripts/eval/eval_agent.py show what happened without leaking tool argument values."""
import importlib.util
from types import SimpleNamespace as NS

from app.config import ROOT

spec = importlib.util.spec_from_file_location("eval_agent", ROOT / "scripts" / "eval" / "eval_agent.py")
eval_agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_agent)


def test_transcript_lists_tools_argument_keys_final_answer_and_the_rendered_answer_but_not_tool_argument_values():
    res = NS(status="ok", answer_type="general_answer", markdown="## Answer\nbody",
             trace=[dict(tool="assess_claim", args={"what_if": {"diagnosis": "PRIVATE-VALUE-123"}}, ok=True, ms=812),
                    dict(tool="final_answer", args={"answer_type": "general_answer", "headline": "the headline"}, ok=False, ms=0,
                         problems=["You called assess_claim this turn, so general_answer is the wrong answer type."]),
                    dict(tool="final_answer", args={"answer_type": "claim_assessment", "headline": "x", "result_id": "claim-1"}, ok=True, ms=0)])
    t = eval_agent.build_transcript("TC04 Specified procedure", 2, "Assess this claim", res, ["answer_type general_answer, expected claim_assessment"],
                                    ["final_answer rejected 1x then accepted"], 14.2)
    assert "FAILED" in t and "Assess this claim" in t and "answer_type general_answer, expected claim_assessment" in t
    assert "`assess_claim`" in t and "argument keys: ['what_if']" in t and "812 ms" in t
    assert "PRIVATE-VALUE-123" not in t                                  # argument values never appear
    assert "Attempt 1" in t and "(rejected)" in t and "Attempt 2" in t and "(accepted)" in t and "the headline" in t
    assert "wrong answer type" in t and "## Answer" in t


def test_a_retried_but_passing_run_is_labelled_as_such_and_a_clean_run_needs_no_transcript():
    res = NS(status="ok", answer_type="claim_assessment", markdown="", trace=[])
    assert "passed, but needed a retry" in eval_agent.build_transcript("Q", 1, "q", res, [], ["rate limited (429) 1x, retried"], 1.0)
    assert not any("cited passages".startswith(p) for p in eval_agent.NEEDS_TRANSCRIPT)     # the harmless unanswerable-question note does not trigger one
