from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..config import settings
from ..rendering import render as R
from ..retrieval.azure_search import get_retriever
from ..tools import claims_engine as E
from ..tools.registry import TurnContext, call_tool, render_final


@dataclass
class AgentResult:
    markdown: str
    answer_type: str
    citations: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    final: dict | None = None
    results: dict = field(default_factory=dict)   # result_id -> {"kind", "data"}: the deterministic tool outputs behind the answer


def _history_block(session: dict, n_turns: int = 4) -> str:
    turns = session.get("history", [])[-n_turns:]
    if not turns:
        return ""
    rows = []
    for t in turns:
        rows.append(f"User: {t['user'][:400]}\nAssistant ({t['answer_type']}): {t['headline'][:300]}")
    return "Earlier in this conversation (for context only):\n" + "\n".join(rows) + "\n\n"


def _claim_block(session: dict) -> str:
    c = session.get("claim")
    if not c:
        return "No claim is loaded in this session.\n\n"
    return (f"A claim is loaded in this session: {c['claim_id']}, insured {c.get('insured_name')}, plan {c['plan']}, "
            f"{c.get('procedure')} for {c.get('diagnosis')}. Use assess_claim to work on it.\n\n")


# =============================================================================================
# Foundry agent (azure-ai-projects 2.x, Responses-based agents)
# =============================================================================================
class FoundryAgent:
    def __init__(self):
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        self.project = AIProjectClient(endpoint=settings.project_endpoint, credential=DefaultAzureCredential())
        self.openai = self.project.get_openai_client()
        self.ref = {"agent_reference": {"name": settings.agent_name, "type": "agent_reference"}}

    def ask(self, session: dict, message: str) -> AgentResult:
        ctx = TurnContext(session=session, retriever=get_retriever())
        # A fresh conversation per turn: the backend owns chat history, so there are never dangling tool calls.
        conv = self.openai.conversations.create()
        input_ = _claim_block(session) + _history_block(session) + "Question: " + message
        try:
            for step in range(settings.max_agent_steps):
                resp = self.openai.responses.create(input=input_, conversation=conv.id, extra_body=self.ref)
                calls = [i for i in resp.output if i.type == "function_call"]
                if not calls:
                    if ctx.final is not None:
                        break
                    if step == settings.max_agent_steps - 1:
                        break
                    input_ = "Reminder: finish this turn by calling final_answer with the right answer_type."
                    continue
                outputs = []
                for c in calls:
                    result = call_tool(c.name, json.loads(c.arguments or "{}"), ctx)
                    outputs.append({"type": "function_call_output", "call_id": c.call_id, "output": json.dumps(result, ensure_ascii=False)})
                if ctx.final is not None:
                    break
                input_ = outputs
        finally:
            try:
                self.openai.conversations.delete(conversation_id=conv.id)
            except Exception:  # noqa: BLE001 - cleanup only
                pass
        if ctx.final is None:
            md = ("## I could not complete that request\n\nThe assistant did not produce a validated answer. "
                  "Please rephrase the question or try again.\n")
            return AgentResult(md, "general_answer", [], ctx.trace, None, ctx.results)
        out = render_final(ctx.final, ctx)
        return AgentResult(out.markdown, ctx.final["answer_type"], out.citations, ctx.trace, ctx.final, ctx.results)


# =============================================================================================
# Offline stand-in. NOT the agent: a keyword router that exercises the same tools and renderers
# so the API, sessions, rendering and tests work without any Azure resources.
# =============================================================================================
class OfflineAgent:
    def ask(self, session: dict, message: str) -> AgentResult:
        ctx = TurnContext(session=session, retriever=get_retriever())
        m = message.lower()
        claim = session.get("claim")
        what_if = _parse_what_if(m)

        if claim and re.search(r"\bwhy\b.*\b(deduct|reduc|cut|not paid|non-payable|hold|held)|explain.*(deduction|room|amount)|how.*(calculated|worked out)", m):
            rid = call_tool("assess_claim", {"what_if": what_if} if what_if else {}, ctx)["result_id"]
            focus = ("room" if re.search(r"room|rent", m) else "non_medical" if re.search(r"non.?medical|glove|mask|annexure|consumable", m)
                     else "hold" if re.search(r"hold|held|prescription", m) else "deductible" if re.search(r"deductible|co-?pay", m) else "all")
            final = dict(answer_type="deduction_explanation", headline="", result_id=rid, focus=focus)
        elif claim and re.search(r"assess|evaluate|adjudicat|payable|how much|estimate|check (this|the) claim|what will be paid", m):
            rid = call_tool("assess_claim", {"what_if": what_if} if what_if else {}, ctx)["result_id"]
            final = dict(answer_type="claim_assessment", headline="", result_id=rid)
        elif claim and re.search(r"document|missing|required|paperwork", m):
            rid = call_tool("assess_claim", {}, ctx)["result_id"]
            final = dict(answer_type="documents_answer", headline="Here is the document checklist for this claim.", result_id=rid)
        else:
            hits = call_tool("search_policy", {"query": message, "top_k": 3}, ctx)["results"]
            final = dict(answer_type="general_answer" if not hits else "definition_answer",
                         headline=("I could not find anything in the policy wording about that." if not hits else
                                   "Offline mode: these are the closest passages in the policy wording. Connect the Foundry agent for a written answer."),
                         points=[dict(label=h["title"] or h["clause"], status="info", detail=(h["excerpt"] if len(h["excerpt"]) <= 300 else h["excerpt"][:300].rsplit(" ", 1)[0] + " ..."), citations=[h["chunk_key"]]) for h in hits],
                         citations=[h["chunk_key"] for h in hits])
            if not hits:
                final["answer_type"] = "insufficient_information"
        ctx.final = final
        out = render_final(final, ctx)
        return AgentResult(out.markdown, final["answer_type"], out.citations, ctx.trace, final, ctx.results)


def _parse_what_if(m: str) -> dict:
    out = {}
    if (x := re.search(r"room (?:rent )?(?:was|is|of|at)?\s*(?:rs\.?|₹)?\s*([\d,]+)", m)):
        out["room_rate_per_day"] = float(x.group(1).replace(",", ""))
    if "protect benefit" in m and re.search(r"opted|in force|had", m):
        out["protect_benefit_opted"] = True
    if re.search(r"prescription.*(supplied|submitted|provided|available)", m):
        out["documents"] = {"pharmacy_bills_prescription": {"present": True, "complete": True}}
    return out


_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = FoundryAgent() if settings.agent_mode == "foundry" else OfflineAgent()
    return _agent
