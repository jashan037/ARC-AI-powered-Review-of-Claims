"""Free text is capped at the source: headline 2 sentences, 3 points of at most 150 characters, 3 next steps. Rejected once for rephrasing."""
import json
from types import SimpleNamespace as NS

import pytest

from app.agent.instructions import SYSTEM_PROMPT
from app.agent.runner import FoundryAgent
from app.config import settings
from app.retrieval.azure_search import get_retriever
from app.tools.registry import (MAX_HEADLINE_SENTENCES, MAX_NEXT_STEPS, MAX_POINT_CHARS, MAX_POINTS, SCHEMAS, TurnContext, call_tool, count_sentences,
                                validate_final)

R = get_retriever()
SAMPLES = json.load(open(settings.data_dir / "sample_claims.json", encoding="utf-8"))
MSG = "Keep the answer short"


def ctx():
    return TurnContext(session={"uin": settings.default_uin, "history": []}, retriever=R)


def qa(**kw):
    return {"answer_type": "general_answer", "headline": "Fine.", **kw}


def point(detail="Short detail.", label="Label"):
    return dict(label=label, status="info", detail=detail)


# ---------------------------------------------------------------- the sentence counter
@pytest.mark.parametrize("text,n", [
    ("", 0), ("One sentence.", 1), ("One. Two.", 2), ("One. Two. Three.", 3), ("No full stop at all", 1), ("Is it covered? Yes, with conditions.", 2),
    ("The limit is ₹5,000/day. Anything above is reduced.", 2), ("A waiting period of 24 months applies (Excl02). It does not apply to accidents.", 2),
    ("See A.1.2 Def. 5 and C.1.b for the rule. It applies afresh.", 2), ("Room rent is capped at 1.5% of the sum insured per day.", 1),
    ("See p. 28 of the policy. Then check Rs. 1 lakh KYC.", 2), ("The waiting period is 24 months, e.g. for cataract. Accidents are exempt.", 2),
    ("Policy C.1.b.vi lists it. Joint replacement surgeries are on the list. The wait is 24 months.", 3), ("Dr. Rao operated. He did not use an implant.", 2),
    ("Covered.  Two spaces.", 2), ("Ends with number 5. 2 more items.", 2), ("Mr. A. Kumar was admitted. He recovered.", 2)])
def test_count_sentences(text, n):
    assert count_sentences(text) == n, text


# ---------------------------------------------------------------- each cap, at its boundary
def test_limits_are_the_agreed_ones():
    assert (MAX_HEADLINE_SENTENCES, MAX_POINTS, MAX_POINT_CHARS, MAX_NEXT_STEPS) == (2, 3, 150, 3)


def test_exactly_at_the_limits_is_accepted():
    a = qa(headline="First sentence. Second sentence.", points=[point("x" * 150), point(), point()], next_steps=["Check a.", "Check b.", "Check c."])
    assert validate_final(a, ctx()) == []


@pytest.mark.parametrize("bad,expect", [
    (qa(headline="One. Two. Three."), "the headline has 3 sentences (max 2)"),
    (qa(points=[point()] * 4), "4 points (max 3)"),
    (qa(points=[point("x" * 151, "Long one")]), "point detail over 150 characters in: 'Long one'"),
    (qa(next_steps=["Check a.", "Check b.", "Check c.", "Check d."]), "4 next_steps (max 3)")])
def test_each_cap_is_enforced(bad, expect):
    errs = validate_final(bad, ctx())
    assert len(errs) == 1 and errs[0].startswith(MSG) and expect in errs[0] and "at most 2 sentences" in errs[0] and "at most 3 next_steps" in errs[0]


def test_all_problems_are_listed_together():
    err = validate_final(qa(headline="First one. Second one. Third one.", points=[point("y" * 200)] * 5, next_steps=["Check."] * 5), ctx())[0]
    assert "3 sentences" in err and "5 points" in err and "over 150 characters" in err and "5 next_steps" in err


def test_the_headline_of_a_claim_assessment_is_not_shown_so_it_is_not_capped():
    c = TurnContext(session={"claim": SAMPLES["TC07"]["claim"], "uin": settings.default_uin, "history": []}, retriever=R)
    rid = call_tool("assess_claim", {}, c)["result_id"]
    assert validate_final(dict(answer_type="claim_assessment", headline="One. Two. Three. Four.", result_id=rid), c) == []
    assert validate_final(dict(answer_type="documents_answer", headline="One. Two. Three.", result_id=rid), c)[0].startswith(MSG)   # shown, so capped


# ---------------------------------------------------------------- rejected once, like the other guards
def test_rejected_once_then_the_second_attempt_stands():
    c = ctx()
    long = qa(headline="One. Two. Three.", points=[point()] * 4)
    first = call_tool("final_answer", long, c)
    assert "error" in first and first["problems"][0].startswith(MSG)
    assert call_tool("final_answer", long, c) == {"status": "accepted"}              # rejected once, not twice
    assert c.length_caps_rejected


def test_a_shortened_second_attempt_is_accepted_and_nothing_is_cut_from_it():
    c = ctx()
    assert "error" in call_tool("final_answer", qa(points=[point()] * 5), c)
    assert call_tool("final_answer", qa(headline="Short one.", points=[point(), point(), point()]), c) == {"status": "accepted"}
    assert len(c.final["points"]) == 3


def test_it_is_independent_of_the_other_once_only_guards():
    c = ctx()
    bad = qa(headline="One. Two. Three.", next_steps=["Reject the claim.", "See result_id.", "Check c.", "Check d."])
    err = call_tool("final_answer", bad, c)["problems"]
    assert len(err) == 3 and c.length_caps_rejected and c.decision_wording_rejected and c.internal_terms_rejected


# ---------------------------------------------------------------- what the model is told
def test_the_prompt_and_the_tool_schema_state_the_caps():
    for phrase in ("at most 2 short sentences", "at most 3 of at most 150 characters", "next_steps: at most 3"):     # the prompt was shortened; the guards enforce the caps
        assert phrase in SYSTEM_PROMPT, phrase
    schema = {s["name"]: s for s in SCHEMAS}["final_answer"]["parameters"]["properties"]
    assert "At most 2 short sentences" in schema["headline"]["description"] and "At most 3 points" in schema["points"]["description"] and "150 characters" in schema["points"]["description"]
    assert "At most 3" in schema["next_steps"]["description"]
    assert "at most 6" not in SYSTEM_PROMPT and "Max 6" not in json.dumps(schema)


# ---------------------------------------------------------------- through the agent loop with a verbose model
class WordyModel:
    def __init__(self):
        self.step, self.told = 0, None
        self.conversations = NS(create=lambda **_: NS(id="c"), delete=lambda conversation_id, timeout=None: None)
        self.responses = NS(create=self._create)

    def _create(self, input, conversation, extra_body, timeout=None):
        self.step += 1
        fc = lambda args: NS(type="function_call", name="final_answer", arguments=json.dumps(args), call_id=f"c{self.step}")   # noqa: E731
        if self.step == 1:
            return NS(output=[fc(qa(headline="Sentence one. Sentence two. Sentence three.", points=[point()] * 5, next_steps=["Check a."] * 4))], output_text="")
        self.told = json.loads(input[0]["output"])["problems"][0]
        return NS(output=[fc(qa(headline="Short answer. Two sentences.", points=[point("Fine.")] * 3, next_steps=["Check a."]))], output_text="")


def test_the_loop_sends_the_model_back_once_and_delivers_the_short_version():
    a = object.__new__(FoundryAgent)
    a.openai, a.ref = WordyModel(), {"agent_reference": {"name": "t", "type": "agent_reference"}}
    res = a.ask({"id": "s", "uin": settings.default_uin, "history": []}, "hello")
    assert res.status == "ok" and [t["ok"] for t in res.trace if t["tool"] == "final_answer"] == [False, True]
    assert "5 points (max 3)" in a.openai.told and len(res.final["points"]) == 3 and len(res.summary_markdown) > 0
