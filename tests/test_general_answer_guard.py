"""If assess_claim ran this turn, a general_answer is rejected once (the TC04 flake); a second general_answer is accepted."""
import json
from types import SimpleNamespace as NS

from app.agent.runner import FoundryAgent
from app.config import settings
from app.retrieval.azure_search import get_retriever
from app.tools.registry import TurnContext, call_tool, validate_final

SAMPLES = json.load(open(settings.data_dir / "sample_claims.json", encoding="utf-8"))
GENERAL = {"answer_type": "general_answer", "headline": "Here is some general information."}


def fc(name, args, cid):
    return NS(type="function_call", name=name, arguments=json.dumps(args), call_id=cid)


class Model:
    """assess_claim first, then whatever final_answer choices the test scripts (each a function of the assess result_id)."""

    def __init__(self, finals):
        self.finals, self.step, self.rid, self.seen_outputs = list(finals), 0, None, []
        self.conversations = NS(create=lambda **_: NS(id="c"), delete=lambda conversation_id, timeout=None: None)
        self.responses = NS(create=self._create)

    def _create(self, input, conversation, extra_body, timeout=None):
        self.step += 1
        if self.step == 1:
            return NS(output=[fc("assess_claim", {}, "c1")], output_text="")
        out = json.loads(input[0]["output"])
        self.rid = self.rid or out.get("result_id")
        self.seen_outputs.append(out)
        args = self.finals.pop(0)(self.rid)
        return NS(output=[fc("final_answer", args, f"f{self.step}")], output_text="")


def agent(model):
    a = object.__new__(FoundryAgent)
    a.openai, a.ref = model, {"agent_reference": {"name": "t", "type": "agent_reference"}}
    return a


def session(claim=True):
    return {"id": "s", "claim": SAMPLES["TC04"]["claim"] if claim else None, "uin": settings.default_uin, "history": []}


def good(rid):
    return {"answer_type": "claim_assessment", "headline": "x", "result_id": rid}


def test_general_answer_after_assess_claim_is_rejected_once_and_the_model_can_correct_itself():
    model = Model([lambda rid: GENERAL, good])
    res = agent(model).ask(session(), "Assess this claim")
    assert res.answer_type == "claim_assessment" and "₹1,35,000" in res.markdown
    assert [t["ok"] for t in res.trace if t["tool"] == "final_answer"] == [False, True]
    problem = model.seen_outputs[1]["problems"][0]          # what the model was told
    assert "claim_assessment" in problem and "deduction_explanation" in problem and "documents_answer" in problem and model.rid in problem


def test_if_the_model_insists_on_general_answer_the_second_attempt_is_accepted():
    res = agent(Model([lambda rid: GENERAL, lambda rid: GENERAL])).ask(session(), "Assess this claim")
    assert res.answer_type == "general_answer" and res.status == "ok"
    assert [t["ok"] for t in res.trace if t["tool"] == "final_answer"] == [False, True]      # rejected once, not twice


def test_a_correct_answer_type_is_not_touched():
    res = agent(Model([good])).ask(session(), "Assess this claim")
    assert res.answer_type == "claim_assessment" and [t["ok"] for t in res.trace if t["tool"] == "final_answer"] == [True]


def test_general_answer_without_assess_claim_is_accepted_at_once():
    ctx = TurnContext(session=session(False), retriever=get_retriever())
    assert validate_final(GENERAL, ctx) == []
    assert call_tool("final_answer", GENERAL, ctx) == {"status": "accepted"}


def test_the_guard_needs_a_successful_assess_claim():
    ctx = TurnContext(session=session(False), retriever=get_retriever())
    assert "error" in call_tool("assess_claim", {}, ctx)          # no claim loaded: nothing to assess
    assert validate_final(GENERAL, ctx) == []


def test_validate_final_names_the_result_id_and_the_three_right_types():
    ctx = TurnContext(session=session(), retriever=get_retriever())
    rid = call_tool("assess_claim", {}, ctx)["result_id"]
    errs = validate_final(GENERAL, ctx)
    assert len(errs) == 1 and rid in errs[0]
    ctx.general_answer_rejected = True
    assert validate_final(GENERAL, ctx) == []
