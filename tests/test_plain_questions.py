"""Plain questions ("what's my name", "hello") get a one-line answer, never an assessment, even when a claim is loaded.

Reproduced with the real agent: with a claim loaded, "what's my name" sometimes ended as a full claim assessment (the model called assess_claim itself),
and "which hospital was I in" could not be answered at all because get_claim_summary had no hospital. These tests pin the fixes.
"""
import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import intake
from app.agent.instructions import SYSTEM_PROMPT
from app.agent.runner import FoundryAgent, OfflineAgent
from app.main import app
from app.retrieval.azure_search import get_retriever
from app.tools.plain_questions import NOT_IN_DOCUMENTS, chat_answer, fact_answer, fact_fields, plain_kind
from app.tools.registry import SCHEMAS, TurnContext, call_tool


def customer_session():
    s = {"id": "s", "history": [], "audience": "customer", "uin": "HDFHLIP25041V062425"}
    for name, data in intake.sample_files():
        intake.store(s, name, intake.process_file(name, data))
    assert intake.build(s)["status"] == "ready"
    return s


def ctx_for(question, session=None):
    return TurnContext(session=session or customer_session(), retriever=get_retriever(), question=question)


FACTS = {      # question -> the start of the answer
    "what's my name": "Your name on this claim is Rohan Verma.",
    "which hospital was I in": "You were treated at Riverside Multispeciality Hospital (DEMO).",
    "when was I admitted": "You were admitted on 10 Sep 2025 at 14:30.",
    "how many days did I stay": "You stayed 4 days in hospital, from 10 Sep 2025 to 14 Sep 2025.",
    "what is my policy number": "Your policy number is ",
    "what plan do I have": "You have the Optima Lite plan, with a sum insured of ₹5,00,000.",
    "when was I discharged": "You were discharged on 14 Sep 2025 at 11:00.",
    "what was my diagnosis": "The diagnosis on this claim is Acute appendicitis.",
    "what procedure did I have": "The procedure on this claim is Laparoscopic appendectomy.",
    "what is the amount claimed": "The amount claimed is ₹1,84,500.",
    "what is my address": NOT_IN_DOCUMENTS,
    "what's my phone number": NOT_IN_DOCUMENTS,
}
REAL_QUESTIONS = [
    "How much will be paid?", "Why was my room rent reduced?", "What documents are missing?", "Which items are not payable?", "Will my claim be approved?",
    "Is cataract surgery covered, and is there a waiting period?", "What is the waiting period for my plan?", "What happens next?", "What did you find on my claim?",
    "How many days does the insured have to send us the reimbursement documents after discharge?", "What is the room rent limit on the Optima Lite plan?",
    "Is my plan covering maternity?", "Assess this claim", "What would be paid if the room rent had been 5,000 a day?",
]
CHAT = ["hello", "thanks", "who are you", "what's the weather", "Hi!", "Thank you", "what's the weather like today?"]


@pytest.mark.parametrize("q,expected", FACTS.items())
def test_fact_questions_are_recognised_and_answered_from_the_claim(q, expected):
    s = customer_session()
    assert plain_kind(q, s["claim"]) == "fact"
    assert fact_answer(q, s["claim"]).startswith(expected)


def test_the_policy_number_comes_from_the_schedule():
    s = customer_session()
    assert s["claim"]["policy_number"] and s["claim"]["policy_number"] in fact_answer("what is my policy number", s["claim"])


@pytest.mark.parametrize("q", REAL_QUESTIONS)
def test_real_questions_are_never_plain(q):
    assert plain_kind(q, customer_session()["claim"]) is None and fact_fields(q) == []


@pytest.mark.parametrize("q", CHAT)
def test_small_talk_is_recognised_with_or_without_a_claim(q):
    assert plain_kind(q, None) == "chat" and plain_kind(q, customer_session()["claim"]) == "chat" and chat_answer(q)


def test_a_fact_question_without_a_claim_is_not_plain():
    assert plain_kind("what's my name", None) is None      # nothing to look it up in: the model explains that no claim is loaded


# ---------------------------------------------------------------- get_claim_summary
def test_get_claim_summary_holds_everything_a_plain_question_needs():
    out = call_tool("get_claim_summary", {}, ctx_for("x"))
    assert out["loaded"] and out["insured"] == "Rohan Verma" and out["hospital"].startswith("Riverside") and out["policy_number"]
    assert out["days_in_hospital"] == 4 and out["admitted"] == "10 Sep 2025 at 14:30" and out["claimed_amount"] == "₹1,84,500"
    assert out["diagnosis"] == "Acute appendicitis" and out["procedure"] == "Laparoscopic appendectomy" and out["sum_insured"] == "₹5,00,000"
    assert out["not_in_the_documents"] == NOT_IN_DOCUMENTS
    assert not any(k in out for k in ("address", "phone", "email"))


def test_a_field_the_documents_do_not_have_is_null_not_invented():
    s = customer_session()
    s["claim"] = {k: v for k, v in s["claim"].items() if k not in ("hospital", "policy_number")}
    out = call_tool("get_claim_summary", {}, ctx_for("x", s))
    assert out["hospital"] is None and out["policy_number"] is None
    assert fact_answer("which hospital was I in", s["claim"]) == NOT_IN_DOCUMENTS


# ---------------------------------------------------------------- assess_claim is refused for plain questions only
@pytest.mark.parametrize("q", ["what's my name", "hello", "what's the weather"])
def test_assess_claim_is_refused_for_a_plain_question(q):
    out = call_tool("assess_claim", {}, ctx_for(q))
    assert "error" in out and "general_answer" in out["error"]


@pytest.mark.parametrize("q", ["How much will be paid?", "Assess this claim", "Why was my room rent reduced?"])
def test_assess_claim_still_runs_for_real_questions(q):
    assert "result_id" in call_tool("assess_claim", {}, ctx_for(q))


# ---------------------------------------------------------------- the guard and the fallback (a scripted model)
def fc(name, args, cid):
    return NS(type="function_call", name=name, arguments=json.dumps(args), call_id=cid)


class Script:
    """Each step lists the (tool, args) the scripted model calls next; args may be a function of this Script (to read rid, the last result_id seen)."""

    def __init__(self, steps):
        self.steps, self.outputs, self.rid, self.n = list(steps), [], None, 0
        self.conversations = NS(create=lambda **_: NS(id="c"), delete=lambda conversation_id, timeout=None: None)
        self.responses = NS(create=self._create)

    def _create(self, input, conversation, extra_body, timeout=None):
        if isinstance(input, list):
            self.outputs += [json.loads(i["output"]) for i in input]
            self.rid = next((o["result_id"] for o in self.outputs if "result_id" in o), self.rid)
        calls = self.steps.pop(0)
        self.n += 1
        return NS(output=[fc(n, a(self) if callable(a) else a, f"c{self.n}{i}") for i, (n, a) in enumerate(calls)], output_text="")


def agent(model):
    a = object.__new__(FoundryAgent)
    a.openai, a.ref = model, {"agent_reference": {"name": "t", "type": "agent_reference"}}
    return a


NAME = {"answer_type": "general_answer", "headline": "Your name on this claim is Rohan Verma."}


def test_the_normal_path_is_get_claim_summary_then_a_general_answer():
    res = agent(Script([[("get_claim_summary", {})], [("final_answer", NAME)]])).ask(customer_session(), "what's my name")
    assert res.answer_type == "general_answer" and res.summary_markdown.strip().endswith("Rohan Verma.") and res.sections == []
    assert [t["tool"] for t in res.trace] == ["get_claim_summary", "final_answer"]


def test_when_the_model_reaches_for_assess_claim_it_is_told_no_and_answers_plainly():
    model = Script([[("assess_claim", {})], [("get_claim_summary", {})], [("final_answer", NAME)]])
    res = agent(model).ask(customer_session(), "what's my name")
    assert res.answer_type == "general_answer" and res.sections == [] and "₹" not in res.markdown
    assert [(t["tool"], t["ok"]) for t in res.trace] == [("assess_claim", False), ("get_claim_summary", True), ("final_answer", True)]


def test_a_plain_question_answered_with_a_longer_type_is_sent_back_once_and_can_be_corrected():
    wrong = {"answer_type": "coverage_answer", "headline": "Your name is on the policy.", "verdict": "covered", "points": [dict(label="Name", status="info", detail="Rohan Verma")]}
    model = Script([[("final_answer", wrong)], [("final_answer", NAME)]])
    res = agent(model).ask(customer_session(), "what's my name")
    assert res.answer_type == "general_answer" and [t["ok"] for t in res.trace] == [False, True]
    assert "plain question" in model.outputs[0]["problems"][0] and NOT_IN_DOCUMENTS in model.outputs[0]["problems"][0]


def test_if_the_model_insists_the_claims_own_fields_answer_it_never_an_assessment():
    wrong = {"answer_type": "coverage_answer", "headline": "Something long.", "verdict": "covered", "points": [dict(label="x", status="info", detail="y", citations=[])], "citations": []}
    res = agent(Script([[("final_answer", wrong)], [("final_answer", wrong)]])).ask(customer_session(), "which hospital was I in")
    assert res.status == "ok" and res.answer_type == "general_answer" and res.sections == []
    assert "Riverside Multispeciality Hospital (DEMO)" in res.markdown and "₹" not in res.markdown


def test_the_general_answer_guard_still_protects_real_questions():
    general = {"answer_type": "general_answer", "headline": "Here is some general information."}
    model = Script([[("assess_claim", {})], [("final_answer", general)],
                    [("final_answer", lambda m: {"answer_type": "claim_assessment", "headline": "x", "result_id": m.rid})]])
    res = agent(model).ask(customer_session(), "How much will be paid?")
    assert res.answer_type == "claim_assessment" and [t["ok"] for t in res.trace if t["tool"] == "final_answer"] == [False, True]


# ---------------------------------------------------------------- the offline stand-in and the API
@pytest.mark.parametrize("q", list(FACTS) + CHAT)
def test_the_offline_stand_in_answers_them_in_one_line(q):
    res = OfflineAgent().ask(customer_session(), q)
    assert res.answer_type == "general_answer" and res.sections == [] and res.trace == []
    assert len(res.summary_markdown.strip().splitlines()) <= 3 and "Show more" not in res.summary_markdown


def test_over_the_api_a_customer_gets_one_line_and_no_show_more():
    c = TestClient(app)
    sid = c.post("/sessions", json={"audience": "customer"}).json()["session_id"]
    c.post(f"/sessions/{sid}/documents/sample")
    assert c.post(f"/sessions/{sid}/intake").json()["status"] == "ready"
    r = c.post(f"/sessions/{sid}/chat", json={"message": "what's my name"}).json()
    assert r["answer_type"] == "general_answer" and r["sections"] == [] and "Rohan Verma" in r["summary_markdown"]
    r = c.post(f"/sessions/{sid}/chat", json={"message": "how much will be paid?"}).json()
    assert r["answer_type"] == "claim_assessment" and r["sections"]


# ---------------------------------------------------------------- the prompt and the tool description
def test_the_prompt_says_when_not_to_assess_and_what_to_say_when_it_is_not_in_the_documents():
    assert "Call assess_claim only when the question is about payment, deductions, eligibility, waiting periods or documents" in SYSTEM_PROMPT
    assert "I don't see that in your documents." in SYSTEM_PROMPT and "get_claim_summary and nothing else" in SYSTEM_PROMPT
    assert "do not call any tool" in SYSTEM_PROMPT


def test_the_tool_description_lists_the_new_fields():
    d = next(s for s in SCHEMAS if s["name"] == "get_claim_summary")["description"]
    assert all(w in d for w in ("hospital", "policy number", "days in hospital", "null"))
