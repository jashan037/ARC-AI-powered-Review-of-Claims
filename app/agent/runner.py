from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from ..config import settings
from ..observability import log_turn
from ..rendering import render as R
from ..resilience import TurnAbort, call_with_retry, turn_scope
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
    status: str = "ok"                            # ok | timeout | unavailable | incomplete
    latency_ms: int = 0


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
_ABORT_ANSWERS = {
    "timeout": ("## This is taking longer than expected",
                "The assistant did not finish in time, so nothing was assessed or changed. Please try again in a moment. "
                "If it keeps happening, tell your administrator."),
    "unavailable": ("## The assistant is temporarily unavailable",
                    "The Azure AI service is busy or not responding, so nothing was assessed or changed. Please try again in a minute."),
    "incomplete": ("## I could not complete that request",
                   "The assistant did not produce a validated answer. Please rephrase the question or try again."),
}


def _notice(kind: str) -> str:
    title, body = _ABORT_ANSWERS[kind]
    return f"{title}\n\n{body}\n"


class FoundryAgent:
    def __init__(self):
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        self.project = AIProjectClient(endpoint=settings.project_endpoint, credential=DefaultAzureCredential())
        # max_retries=0: the SDK default (2 silent retries, 10 minute timeout) is replaced by call_with_retry and the turn deadline
        self.openai = self.project.get_openai_client().with_options(max_retries=0)
        self.ref = {"agent_reference": {"name": settings.agent_name, "type": "agent_reference"}}

    def _model(self, scope, fn):
        """A model-service call: timeout, retry on 429/5xx, never retried after a read timeout (see resilience.py)."""
        scope.model_calls += 1
        return call_with_retry(fn, label="model", timeout=settings.model_timeout_s, retry_timeouts=False)

    def ask(self, session: dict, message: str) -> AgentResult:
        t0 = time.perf_counter()
        ctx = TurnContext(session=session, retriever=get_retriever())
        conv, status, error = None, "ok", None
        with turn_scope() as scope:
            try:
                # A fresh conversation per turn: the backend owns chat history, so there are never dangling tool calls.
                conv = self._model(scope, lambda t: self.openai.conversations.create(timeout=t))
                input_ = _claim_block(session) + _history_block(session) + "Question: " + message
                for step in range(settings.max_agent_steps):
                    resp = self._model(scope, lambda t: self.openai.responses.create(input=input_, conversation=conv.id, extra_body=self.ref, timeout=t))
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
            except TurnAbort as e:
                status, error = e.kind, f"{e.where}:{type(e.cause).__name__ if e.cause else 'deadline'}"
            except Exception as e:  # noqa: BLE001 - a real error (bad request, auth, bug): log the class, let the API answer cleanly
                log_turn(agent="foundry", session=session, message=message, status="error", answer_type="-", trace=ctx.trace, scope=scope,
                         latency_ms=round((time.perf_counter() - t0) * 1000), error=type(e).__name__)
                raise
            finally:
                if conv is not None:   # best effort, short timeout, no retry: the run expires by itself after 10 minutes anyway
                    try:
                        self.openai.conversations.delete(conversation_id=conv.id, timeout=5)
                    except Exception:  # noqa: BLE001 - cleanup only
                        pass
            if status == "ok" and ctx.final is None:
                status = "incomplete"
            latency = round((time.perf_counter() - t0) * 1000)
            log_turn(agent="foundry", session=session, message=message, status=status, answer_type=(ctx.final or {}).get("answer_type", "-"),
                     trace=ctx.trace, scope=scope, latency_ms=latency, error=error)
        if ctx.final is None:
            return AgentResult(_notice(status), "general_answer", [], ctx.trace, None, ctx.results, status, latency)
        out = render_final(ctx.final, ctx)
        return AgentResult(out.markdown, ctx.final["answer_type"], out.citations, ctx.trace, ctx.final, ctx.results, "ok", latency)


# =============================================================================================
# Offline stand-in. NOT the agent: a keyword router that exercises the same tools and renderers
# so the API, sessions, rendering and tests work without any Azure resources.
# =============================================================================================
class OfflineAgent:
    def ask(self, session: dict, message: str) -> AgentResult:
        t0 = time.perf_counter()
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
        latency = round((time.perf_counter() - t0) * 1000)
        log_turn(agent="offline", session=session, message=message, status="ok", answer_type=final["answer_type"], trace=ctx.trace, latency_ms=latency)
        return AgentResult(out.markdown, final["answer_type"], out.citations, ctx.trace, final, ctx.results, "ok", latency)


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
