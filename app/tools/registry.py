"""The tools the agent can call, and what happens when it does.

Function tools run here in the backend (see agent/runner.py). The agent must finish every
turn by calling `final_answer`; the backend validates it and renders the Markdown itself.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field

from ..config import settings
from ..rendering import render as R
from ..rendering.customer_labels import customerize_rendered
from ..rendering.scrub import find as find_internal, officer_texts, scrub_final
from ..resilience import TurnAbort
from ..retrieval.base import Chunk, Retriever
from . import claims_engine as E
from .evidence import resolve
from .focus import focus_for
from .plain_questions import NOT_IN_DOCUMENTS, claim_facts, fact_answer, plain_kind

ANSWER_TYPES = ["claim_assessment", "coverage_answer", "waiting_period_answer", "deduction_explanation",
                "documents_answer", "definition_answer", "insufficient_information", "general_answer"]
NEEDS_CLAIM_RESULT = {"claim_assessment", "deduction_explanation"}
NEEDS_CITATIONS = {"coverage_answer", "definition_answer", "waiting_period_answer", "documents_answer"}


@dataclass
class TurnContext:
    session: dict
    retriever: Retriever
    seen: dict = field(default_factory=dict)      # chunk_key -> Chunk, everything the agent has been shown this turn
    results: dict = field(default_factory=dict)   # result_id -> {"kind": "claim"|"waiting", "data": ...}
    trace: list = field(default_factory=list)
    question: str = ""                            # what the user typed this turn (used only to recognise plain questions, see plain_questions.py)
    final: dict | None = None
    final_errors: int = 0
    general_answer_rejected: bool = False   # the "you assessed a claim, so do not answer general_answer" guard fires once per turn
    decision_wording_rejected: bool = False # so does the "next_steps must not be decisions" guard
    internal_terms_rejected: bool = False   # and the "no result ids, chunk keys or tool names in officer text" guard
    length_caps_rejected: bool = False      # and the "keep it short" guard (headline, points, next steps)
    plain_rejected: bool = False            # and the "a plain question gets a one-line general_answer, not an assessment" guard
    focus_corrected: list = field(default_factory=list)   # (model's focus, focus decided from the question) whenever they differed

    @property
    def plain(self) -> str | None:
        """"fact" (a detail of the loaded claim), "chat" (greeting, thanks, off topic) or None. Such a turn never needs assess_claim."""
        return plain_kind(self.question, self.session.get("claim"))

    @property
    def uin(self) -> str:
        claim = self.session.get("claim") or {}
        return claim.get("policy_uin") or self.session.get("uin") or settings.default_uin


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


_S, _N, _B = {"type": "string"}, {"type": "number"}, {"type": "boolean"}

SCHEMAS = [
    dict(name="search_policy", description="Semantic search over the indexed policy wording for the policy version that applies to this claim. "
         "Use for any question about what the policy says. Returns passages with a chunk_key you can cite. Search again with different words if the first results do not answer the question.",
         parameters=_obj({"query": {**_S, "description": "Plain-language search query, for example 'waiting period for cholecystectomy'."},
                          "top_k": {"type": "integer", "description": "How many passages to return (default 5, max 8)."}}, ["query"])),
    dict(name="get_clause", description="Fetch a specific clause by its number, for example 'C.1.b', 'B.1.1.1 Note iii', 'A.1.2 Def. 5', 'E.1.7' or 'Annexure B'.",
         parameters=_obj({"clause_ref": _S}, ["clause_ref"])),
    dict(name="check_waiting_period", description="Deterministic waiting-period check. Use whenever the user gives dates (policy start and treatment date), or to answer 'has the waiting period been served'. "
         "Never calculate months or days yourself.",
         parameters=_obj({"first_policy_inception": {**_S, "description": "ISO date the first policy with the insurer started, YYYY-MM-DD."},
                          "admission_date": {**_S, "description": "ISO date of admission or treatment, YYYY-MM-DD."},
                          "diagnosis": _S, "procedure": _S, "is_accident": _B, "pre_existing": _B,
                          "prior_continuous_coverage_months": {**_N, "description": "Months of continuous cover ported from another insurer."}},
                         ["first_policy_inception", "admission_date"])),
    dict(name="lookup_non_medical_item", description="Check whether a billed item is on the policy's Annexure B list of non-medical items (non-payable unless Protect Benefit is in force).",
         parameters=_obj({"item": _S}, ["item"])),
    dict(name="get_claim_summary", description="The details of the claim loaded in this session: insured name, plan and sum insured, policy number, hospital, diagnosis, procedure, "
         "admission and discharge (already formatted), days in hospital and the amount claimed. Use it, and nothing else, to answer a plain question about these details. "
         "A null value is not in the customer's documents. Address, phone number and email are never in it.", parameters=_obj({})),
    dict(name="assess_claim", description="Run the full deterministic assessment on the claim loaded in this session: eligibility, waiting periods, room-rent proportion, "
         "non-medical items, documents, deductions and the estimated payment. Optionally pass what_if to test a change, for example {'room_rate_per_day': 5000}.",
         parameters=_obj({"what_if": {"type": "object", "description": "Optional overrides. Allowed keys: first_policy_inception, admission_datetime, plan, base_si_lakh, room_rate_per_day, "
                          "protect_benefit_opted, aggregate_deductible_remaining, is_accident, pre_existing, copay_percent, diagnosis, procedure, documents.",
                          "additionalProperties": True}})),
    dict(name="final_answer", description="REQUIRED last step of every turn. The backend validates and formats your answer; do not write the answer as plain text. "
         "Numbers and dates for claims come from tool results via result_id, so never type them yourself for claim_assessment or deduction_explanation.",
         parameters=_obj({
             "answer_type": {"type": "string", "enum": ANSWER_TYPES},
             "headline": {**_S, "description": "At most 2 short sentences that directly answer the question. For claim_assessment this is not shown, so keep it short."},
             "verdict": {"type": "string", "enum": ["covered", "covered_with_conditions", "not_covered", "depends", "insufficient_information", "not_applicable"],
                         "description": "Only for coverage_answer."},
             "points": {"type": "array", "description": "Key findings, most important first. At most 3 points; each detail at most 150 characters.",
                        "items": _obj({"label": _S, "status": {"type": "string", "enum": ["ok", "warning", "problem", "info"]}, "detail": _S,
                                       "citations": {"type": "array", "items": _S, "description": "chunk_key values returned by tools."}}, ["label", "status", "detail"])},
             "next_steps": {"type": "array", "items": _S, "description": "At most 3 things for the officer to check."}, "caveats": {"type": "array", "items": _S},
             "citations": {"type": "array", "items": _S, "description": "chunk_key values returned by search_policy, get_clause or the deterministic tools."},
             "result_id": {**_S, "description": "result_id from assess_claim (claim_assessment, deduction_explanation, documents_answer) or check_waiting_period (waiting_period_answer)."},
             "focus": {"type": "string", "enum": ["room", "associated", "non_medical", "hold", "deductible", "all"], "description": "For deduction_explanation."}},
             ["answer_type", "headline"])),
]


def _remember(ctx: TurnContext, chunks: list[Chunk]) -> list[dict]:
    for c in chunks:
        ctx.seen.setdefault(c.chunk_key, c)
    return [dict(chunk_key=c.chunk_key, citation=c.citation) for c in chunks]


def _evidence_for(ctx: TurnContext, refs: list[str]) -> list[dict]:
    chunks = []
    for cid in resolve(refs):
        ch = ctx.retriever.get_by_chunk_id(cid, ctx.uin) or ctx.retriever.get_by_chunk_id(cid, None)
        if ch:
            chunks.append(ch)
    return _remember(ctx, chunks)


def _new_result(ctx: TurnContext, kind: str, data: dict) -> str:
    rid = f"{kind}-{uuid.uuid4().hex[:8]}"
    ctx.results[rid] = dict(kind=kind, data=data)
    return rid


def _claim_or_error(ctx: TurnContext):
    return ctx.session.get("claim")


# ---------------------------------------------------------------------------------------------
def call_tool(name: str, args: dict, ctx: TurnContext) -> dict:
    """Never raises. Errors go back to the model as {'error': ...} so it can recover."""
    t0 = time.perf_counter()
    try:
        out = _dispatch(name, args or {}, ctx)
    except TurnAbort:   # deadline gone or Azure unavailable: the turn must stop, not go back to the model as a tool error
        raise
    except Exception as e:  # noqa: BLE001 - tool errors must reach the model, not crash the request
        out = {"error": f"{type(e).__name__}: {e}"}
    entry = dict(tool=name, args=args, ok="error" not in out, ms=round((time.perf_counter() - t0) * 1000))
    if not entry["ok"]:   # keep the reason so evaluations and logs can say why a call failed (no claim data is added)
        entry["problems"] = out.get("problems") or [out["error"]]
    ctx.trace.append(entry)
    return out


def _dispatch(name: str, a: dict, ctx: TurnContext) -> dict:
    if name == "search_policy":
        top_k = max(1, min(int(a.get("top_k") or 5), 8))
        chunks = ctx.retriever.search(a["query"], uin=ctx.uin, top_k=top_k)
        _remember(ctx, chunks)
        return {"policy_uin": ctx.uin, "results": [c.short(1500, query=a["query"]) for c in chunks],
                "note": "" if chunks else "No passages matched. Try different words or answer with insufficient_information."}

    if name == "get_clause":
        ids = resolve(a["clause_ref"])
        chunks = [c for c in (ctx.retriever.get_by_chunk_id(i, ctx.uin) or ctx.retriever.get_by_chunk_id(i, None) for i in ids) if c]
        if not chunks:
            return {"error": f"Could not resolve clause '{a['clause_ref']}'. Use search_policy instead."}
        _remember(ctx, chunks)
        return {"clauses": [c.short(4000) for c in chunks]}

    if name == "check_waiting_period":
        mini = dict(first_policy_inception=a["first_policy_inception"], admission_datetime=a["admission_date"] + "T00:00",
                    discharge_datetime=a["admission_date"] + "T23:59", diagnosis=a.get("diagnosis", ""), procedure=a.get("procedure", ""),
                    is_accident=bool(a.get("is_accident")), pre_existing=bool(a.get("pre_existing")),
                    prior_continuous_coverage_months=int(a.get("prior_continuous_coverage_months") or 0))
        checks, wctx = E.check_waiting_period(mini), E.waiting_context(mini)
        rid = _new_result(ctx, "waiting", dict(context=wctx, checks=checks))
        refs = [r for k in checks if k["status"] != "not_applicable" for r in k["evidence"]] or ["C.1.c"]
        return dict(result_id=rid, elapsed_months=wctx["elapsed_months"], elapsed_days=wctx["elapsed_days"],
                    checks=[{k: v for k, v in c.items() if k in ("code", "name", "status", "required", "detail", "eligible_from")} for c in checks],
                    evidence=_evidence_for(ctx, refs))

    if name == "lookup_non_medical_item":
        claim = _claim_or_error(ctx)
        res = E.lookup_non_medical_item(a["item"])
        res["evidence"] = _evidence_for(ctx, ["C.3.k", "Annexure B", "B.2.3"])
        if claim:
            res["protect_benefit_in_force_for_loaded_claim"] = E.protect_in_force(claim)
        return res

    if name == "get_claim_summary":
        claim = _claim_or_error(ctx)
        if not claim:
            return {"loaded": False, "message": "No claim is loaded in this session."}
        return dict(loaded=True, **claim_facts(claim), not_in_the_documents=NOT_IN_DOCUMENTS)

    if name == "assess_claim":
        claim = _claim_or_error(ctx)
        if not claim:
            return {"error": "No claim is loaded in this session. Ask the user to attach or select a claim first."}
        if ctx.plain:
            return {"error": "The user asked a plain question (a detail of the claim, a greeting or small talk), not about payment, deductions, eligibility, waiting periods "
                             "or documents. Do not assess the claim. Use get_claim_summary if you need a detail, then final_answer with answer_type general_answer."}
        res = E.assess(E.apply_what_if(claim, a.get("what_if")))
        res["what_if"] = {k: v for k, v in (a.get("what_if") or {}).items() if k in E.WHAT_IF_KEYS or k == "documents"} or None
        if res["what_if"]:   # the claim as submitted, computed by the same engine, so the answer can show "before -> after"
            base = E.assess(claim)
            res["baseline"] = dict(estimated=base["amounts"]["estimated_payable_if_docs_supplied"], confirmed=base["amounts"]["payable_confirmed_now"],
                                   recommendation=base["recommendation"])
        rid = _new_result(ctx, "claim", res)
        return {**E.compact_summary(res), "result_id": rid, "what_if_applied": bool(a.get("what_if")), "evidence": _evidence_for(ctx, res["evidence_refs"])}

    if name == "final_answer":
        return _final(a, ctx)
    return {"error": f"Unknown tool '{name}'."}


# ---------------------------------------------------------------------------------------------
# How much the officer is asked to read. Rejected once for rephrasing, like the other guards; the compact view then shows the top three anyway.
MAX_HEADLINE_SENTENCES, MAX_POINTS, MAX_POINT_CHARS, MAX_NEXT_STEPS = 2, 3, 150, 3
_ABBREVIATIONS = {"e.g", "i.e", "def", "rs", "no", "vs", "p", "pp", "approx", "sec", "cl", "mr", "mrs", "dr", "st", "fig", "excl", "cf", "etc"}


def count_sentences(text: str) -> int:
    """Sentences in a short text. A full stop after an abbreviation (Def. 5, p. 28, Rs. 1 lakh) or inside a number (1.5, C.1.b) does not end one."""
    t = (text or "").strip()
    n = 1 if t else 0
    for m in re.finditer(r"([A-Za-z.]*)([.!?])\s+(?=[A-Z0-9₹\"“'(])", t):
        if m.group(2) == "." and (m.group(1).lower().rstrip(".") in _ABBREVIATIONS or (len(m.group(1)) == 1 and m.group(1).isupper())):
            continue
        n += 1
    return n


# Next steps are for the officer to check or flag. These read as decisions or instructions to decide.
_DECISION = re.compile(
    r"^\W*(do not|don't|dont|reject|deny|decline|approve|pay(?! attention)|admit|settle|repudiate|refuse|disallow)\b"
    r"|\b(do not|don't|should not|must not|cannot|can't|shall not) (be )?(pay|paid|admit|admitted|approve|approved|settle|settled|accept|accepted)\b"
    r"|\b(reject|deny|decline|repudiate|disallow) (the|this|that) (claim|request)\b"
    r"|\bmark (\w+ ){0,3}(as )?(non-?payable|payable|rejected|approved)\b", re.I)


def validate_final(a: dict, ctx: TurnContext) -> list[str]:
    errs, t = [], a.get("answer_type")
    if t not in ANSWER_TYPES:
        return [f"answer_type must be one of {ANSWER_TYPES}."]
    if not (a.get("headline") or "").strip():
        errs.append("headline is required.")
    if len(a.get("headline", "")) > 500:
        errs.append("headline must be under 500 characters.")
    rid = a.get("result_id")
    claim_rids = [k for k, r in ctx.results.items() if r["kind"] == "claim"]
    if ctx.plain and t != "general_answer" and not ctx.plain_rejected:
        errs.append("This is a plain question (a detail of the claim, a greeting or small talk), not about payment, deductions, eligibility, waiting periods or documents. "
                    "Do not assess the claim and do not use a longer answer type. Answer with answer_type general_answer: one short sentence, no points, no citations. "
                    f"For a detail of the claim use get_claim_summary; if it does not hold what was asked (address, phone number, email), say \"{NOT_IN_DOCUMENTS}\"")
    if t == "general_answer" and claim_rids and not ctx.general_answer_rejected and not ctx.plain:
        errs.append(f"You called assess_claim this turn, so general_answer is the wrong answer type. Use claim_assessment (assess or evaluate the claim), "
                    f"deduction_explanation (why an amount was deducted or held) or documents_answer (missing documents), with result_id={claim_rids[-1]}.")
    decisions = [s for s in a.get("next_steps") or [] if _DECISION.search(s)]
    if decisions and not ctx.decision_wording_rejected:
        errs.append("next_steps are things for the claims officer to check, verify, confirm, request or flag. They must never be a decision or an instruction to "
                    "decide (pay, admit, approve, reject, deny, decline, settle). Rephrase these: " + " | ".join(s[:90] for s in decisions[:3]))
    if not ctx.length_caps_rejected:
        long_ = []
        if t not in NEEDS_CLAIM_RESULT and count_sentences(a.get("headline", "")) > MAX_HEADLINE_SENTENCES:
            long_.append(f"the headline has {count_sentences(a.get('headline', ''))} sentences (max {MAX_HEADLINE_SENTENCES})")
        pts = a.get("points") or []
        if len(pts) > MAX_POINTS:
            long_.append(f"{len(pts)} points (max {MAX_POINTS})")
        wordy = [p.get("label", "?") for p in pts if len(p.get("detail", "")) > MAX_POINT_CHARS]
        if wordy:
            long_.append(f"point detail over {MAX_POINT_CHARS} characters in: " + ", ".join(f"'{w}'" for w in wordy[:3]))
        if len(a.get("next_steps") or []) > MAX_NEXT_STEPS:
            long_.append(f"{len(a['next_steps'])} next_steps (max {MAX_NEXT_STEPS})")
        if long_:
            errs.append("Keep the answer short for the claims officer: " + "; ".join(long_) + f". Use at most {MAX_HEADLINE_SENTENCES} sentences in the headline, at most "
                        f"{MAX_POINTS} points of at most {MAX_POINT_CHARS} characters each, and at most {MAX_NEXT_STEPS} next_steps. Put the most important first and rephrase.")
    leaks = [(where, term) for where, text in officer_texts(a) for term in find_internal(text)]
    if leaks and not ctx.internal_terms_rejected:
        errs.append("Text the claims officer reads (headline, points, next_steps, caveats) must never mention result ids, chunk keys, field names or tool names "
                    "(result_id, chunk_key, assess_claim, check_waiting_period and so on). Say \"the waiting-period check\" or \"the claim assessment\" in plain words, "
                    "and put clause references in citations. Remove or rephrase: " + " | ".join(f"{w}: '{t}'" for w, t in leaks[:4]))
    if t in NEEDS_CLAIM_RESULT and (rid not in ctx.results or ctx.results[rid]["kind"] != "claim"):
        errs.append(f"{t} needs result_id from assess_claim. Call assess_claim first.")
    if t == "waiting_period_answer" and rid and rid not in ctx.results:
        errs.append("result_id is unknown. Use the result_id returned by check_waiting_period.")
    cites = list(a.get("citations") or []) + [c for p in a.get("points") or [] for c in p.get("citations") or []]
    bad = sorted({c for c in cites if c not in ctx.seen})
    if bad:
        errs.append(f"These citations were never returned by a tool this turn: {bad}. Cite only chunk_key values from tool results. Valid examples: {list(ctx.seen)[:6]}")
    if t in NEEDS_CITATIONS and not cites and a.get("verdict") != "insufficient_information" and not (t in ("waiting_period_answer", "documents_answer") and rid):
        errs.append(f"{t} must cite at least one chunk_key. Call search_policy or get_clause, or answer with insufficient_information.")
    if t == "coverage_answer" and not a.get("points"):
        errs.append("coverage_answer needs at least one point (label, status, detail).")
    for p in a.get("points") or []:
        if len(p.get("detail", "")) > 600:
            errs.append(f"Point '{p.get('label')}' detail is too long (max 600 characters).")
    return errs


def _final(a: dict, ctx: TurnContext) -> dict:
    if ctx.plain == "fact" and ctx.plain_rejected and a.get("answer_type") != "general_answer" and ctx.session.get("claim"):
        # told once, and the model still wants a longer answer for a plain question: the claim's own fields answer it, not the model
        a = dict(answer_type="general_answer", headline=fact_answer(ctx.question, ctx.session["claim"]) or NOT_IN_DOCUMENTS)
    errs = validate_final(a, ctx)
    if a.get("answer_type") == "general_answer" and any(e.startswith("You called assess_claim") for e in errs):
        ctx.general_answer_rejected = True   # rejected once: if the model insists on general_answer, its second choice stands
    if any(e.startswith("next_steps are things for the claims officer") for e in errs):
        ctx.decision_wording_rejected = True   # likewise once: a rephrase is asked for, not an endless loop
    if any(e.startswith("Keep the answer short") for e in errs):
        ctx.length_caps_rejected = True         # once: the officer's view shows the top three points either way
    if any(e.startswith("This is a plain question") for e in errs):
        ctx.plain_rejected = True               # once; a persistent fact question is then answered from the claim itself (see the top of this function)
    if any(e.startswith("Text the claims officer reads") for e in errs):
        ctx.internal_terms_rejected = True      # once, like the others; the renderer removes whatever a second attempt still contains
    if errs and ctx.final_errors < 2:
        ctx.final_errors += 1
        return {"error": "final_answer rejected. Fix and call final_answer again.", "problems": errs}
    if errs:   # third attempt: keep the answer but strip what cannot be verified
        a = dict(a)
        a["citations"] = [c for c in a.get("citations") or [] if c in ctx.seen]
        for p in a.get("points") or []:
            p["citations"] = [c for c in p.get("citations") or [] if c in ctx.seen]
        a["caveats"] = list(a.get("caveats") or []) + ["Some citations could not be verified and were removed. Treat this answer with extra care."]
    ctx.final = a
    return {"status": "accepted"}


TOOL_NAMES = frozenset(s["name"] for s in SCHEMAS)


def trace_summary(trace: list) -> list[dict]:
    """What a screen may show about how an answer was produced: tool name, success and milliseconds. Never arguments, results or problem texts."""
    out = []
    for t in trace or []:
        ms = t.get("ms")
        out.append(dict(tool=t.get("tool") if t.get("tool") in TOOL_NAMES else "other", ok=bool(t.get("ok")),
                        ms=int(ms) if isinstance(ms, (int, float)) and not isinstance(ms, bool) else None))
    return out


def render_final(final: dict, ctx: TurnContext) -> R.Rendered:
    out = _render(final, ctx)
    # the officer wording is locked by the golden files; a customer gets plain names instead of Excl codes, annexure letters and clause numbers
    return customerize_rendered(out) if ctx.session.get("audience", "officer") == "customer" else out


def _focus(final: dict, ctx: TurnContext) -> str:
    """The question decides the focus; the model's choice stands only when the question does not point at a part."""
    chosen, decided = final.get("focus") or "all", focus_for(ctx.question)
    if decided and decided != chosen:
        ctx.focus_corrected.append((chosen, decided))
        return decided
    return chosen


def _render(final: dict, ctx: TurnContext) -> R.Rendered:
    final = scrub_final(final)   # last line of defence: no internal identifier reaches the officer, whatever the model wrote
    t, rid, ret = final["answer_type"], final.get("result_id"), ctx.retriever
    aud = ctx.session.get("audience", "officer")
    if t == "claim_assessment":
        note = " ".join(final.get("caveats") or []) or None
        return R.render_claim_assessment(ctx.results[rid]["data"], ret, note, aud)
    if t == "deduction_explanation":
        return R.render_deduction_explanation(ctx.results[rid]["data"], _focus(final, ctx), ret, audience=aud)
    if t == "waiting_period_answer" and rid in ctx.results:
        r = ctx.results[rid]
        data = r["data"] if r["kind"] == "waiting" else r["data"]["waiting"]
        return R.render_waiting(data, final, ret, ctx.uin, aud)
    if t == "documents_answer" and rid in ctx.results and ctx.results[rid]["kind"] == "claim":
        return R.render_documents(ctx.results[rid]["data"], final, ret, aud)
    return R.render_qa(final, ret, ctx.uin, aud)
