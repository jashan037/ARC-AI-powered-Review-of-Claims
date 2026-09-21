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
from ..rendering import compact, render as R
from ..rendering.customer_labels import customerize_rendered
from ..rendering.scrub import find as find_internal, officer_texts, scrub_final
from ..resilience import TurnAbort
from ..retrieval.base import Chunk, Retriever
from . import claims_engine as E
from .evidence import resolve
from .focus import focus_for
from .number_guard import allowed_from, drop_sentences, offenders, reformat_amounts, split_sentences
from .routing import policy_kind
from .plain_questions import NOT_IN_DOCUMENTS, claim_facts, fact_answer, plain_kind

ANSWER_TYPES = ["claim_assessment", "coverage_answer", "waiting_period_answer", "deduction_explanation",
                "documents_answer", "definition_answer", "insufficient_information", "general_answer", "direct_answer"]
# what a direct_answer may open under "Show more": each id is built by existing code from a tool result (see _render_direct)
DETAILS = ["room_working", "non_medical_list", "documents_checklist", "estimate_breakdown", "waiting_period", "policy_reference"]
NEEDS_CLAIM_DETAILS = {"room_working", "non_medical_list", "documents_checklist", "estimate_breakdown"}
MAX_REPLY_SENTENCES, MAX_REPLY_WORDS = 4, 85            # "at most 4 sentences and about 80 words"
_LIST_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
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
    tool_outputs: list = field(default_factory=list)      # what the tools returned this turn (final_answer excluded): the number guard checks direct answers against it
    voice_rejected: bool = False            # the customer-voice guard asks for a rewrite once
    type_rejected: bool = False             # a customer's documents_answer or deduction_explanation is sent back once, to become a direct_answer
    figures_rejected: bool = False          # a payment answer with no figure in it is sent back once
    policy_rejected: bool = False           # a policy question answered as a direct_answer is sent back once
    exception_rejected: bool = False        # a coverage or waiting-period answer that leaves out the exception its own cited passage states is sent back once
    reason_rejected: bool = False           # an amount without the reason behind it is sent back once; after that the reason is added in code
    prerun: str = ""                        # "assess" or "search": what the code ran before the model's first call (see runner.py)
    number_rejected: bool = False           # the number guard asks for a rewrite once
    numbers_dropped: list = field(default_factory=list)   # offending numbers that were still there after the rewrite (their sentence was dropped)
    number_fallbacks: int = 0               # replies rebuilt in code because nothing was left after dropping

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
             "headline": {**_S, "description": "At most 2 short sentences that directly answer the question. For claim_assessment this is not shown, so keep it short. Not used by direct_answer."},
             "reply": {**_S, "description": "Only for direct_answer: the whole answer in plain text, at most 4 sentences and about 80 words. A list only for 3 or more parallel items. "
                       "Every amount, percentage, date and count in it must be copied from a tool result of this turn."},
             "details": {"type": "array", "items": {"type": "string", "enum": DETAILS},
                         "description": "Only for direct_answer: what may open under Show more, built by the backend from tool results. room_working, non_medical_list, documents_checklist and "
                                        "estimate_breakdown need assess_claim this turn; waiting_period needs assess_claim or check_waiting_period; policy_reference needs citations."},
             "verdict": {"type": "string", "enum": ["covered", "covered_with_conditions", "not_covered", "depends", "insufficient_information", "not_applicable"],
                         "description": "Only for coverage_answer."},
             "points": {"type": "array", "description": "Key findings, most important first. At most 3 points; each detail at most 150 characters.",
                        "items": _obj({"label": _S, "status": {"type": "string", "enum": ["ok", "warning", "problem", "info"]}, "detail": _S,
                                       "citations": {"type": "array", "items": _S, "description": "chunk_key values returned by tools."}}, ["label", "status", "detail"])},
             "next_steps": {"type": "array", "items": _S, "description": "At most 3 things for the officer to check."}, "caveats": {"type": "array", "items": _S},
             "citations": {"type": "array", "items": _S, "description": "chunk_key values returned by search_policy, get_clause or the deterministic tools."},
             "result_id": {**_S, "description": "result_id from assess_claim (claim_assessment, deduction_explanation, documents_answer) or check_waiting_period (waiting_period_answer)."},
             "focus": {"type": "string", "enum": ["room", "associated", "non_medical", "hold", "deductible", "all"], "description": "For deduction_explanation."}},
             ["answer_type"])),
]


def customer_facts(res: dict) -> dict:
    """What lies behind the amounts of an assessment, straight from the engine's result: the room-rent limit and proportion, the non-medical items, the documents, the waiting periods."""
    bill, c = res["bill"], res["claim"]
    lines, sums = bill["lines"], R._sum
    facts = {"plan": c["plan"], "base_sum_insured": round(c["base_si_lakh"] * 100000)}
    if bill["room_rule"]["type"] != "at_actuals" and bill["room_ratio"] < 1:
        facts["room_rent"] = dict(plan_limit_per_day=bill["room_limit_per_day"], billed_per_day=bill["room_rate_per_day"], days=bill["room_days"],
                                  proportion_paid_percent=round(bill["room_ratio"] * 100, 1), rule=R._rule_text(bill["room_rule"], c["base_si_lakh"]),
                                  applies_to="the room charges and the associated medical expenses (doctor and consultation fees, operation theatre, nursing, anaesthesia)")
    nm = [l for l in lines if l["category"] == "non_medical"]
    facts["non_medical_items"] = dict(count=len(nm), total=sums(lines, ("non_medical",), "billed"), examples=[l["description"] for l in nm[:4]], payable=bool(bill["protect_benefit_in_force"]))
    facts["associated_medical_expenses"] = dict(billed=sums(lines, ("associated",), "billed"), payable=sums(lines, ("associated",), "payable"))
    facts["documents"] = dict(missing=[R.DOC_SHORT.get(d["id"], d["name"]) for d in res["documents"]["checklist"] if d["status"] != "ok"])
    facts["waiting_periods"] = [dict(name=k["name"], status=k["status"]) for k in res["waiting"]["checks"]]
    return facts


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
    if entry["ok"] and name != "final_answer":
        ctx.tool_outputs.append(out)
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
        out = {**E.compact_summary(res), "result_id": rid, "what_if_applied": bool(a.get("what_if"))}
        if ctx.session.get("audience") == "customer":   # the facts behind each reason, so a reply can say why and not only how much; the policy quotes come from search_policy
            out["customer_facts"] = customer_facts(res)
        else:
            out["evidence"] = _evidence_for(ctx, res["evidence_refs"])
        return out

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


# Customer mode: words that speak about the customer in the third person, tell someone to check things for an officer, point at the screen, or tell the insurer what to decide.
_VOICE = re.compile(
    r"\bthe insured\b|\bthe claimant\b|\bthe policy ?holder\b|\binsured person\b|\bverify\b|\bconfirm whether\b|\bcheck whether\b|\bensure that\b"
    r"|\bfor the (?:claims )?officer\b|\bthe (?:claims )?officer (?:should|must|will need|to)\b|\broute to\b|\bescalate\b|\bflag(?:ged)? for review\b"
    r"|\bshow more\b|\bsee below\b|\b(?:details|working) below\b|\badjudicat\w*|\bthe insurer (?:should|must)\b|\bshould (?:pay|approve|reject|deny|settle)\b|\bAudience\b"
    r"|(?:^|[.!?]\s+)(?:please\s+)?(?:approve|reject|deny|decline|settle|pay)\b(?! attention)", re.I)


def voice_problems(a: dict) -> list[tuple[str, str]]:
    """(where, phrase) for every phrase in the model's customer-facing text that a customer should not be spoken to with."""
    out = []
    for where, text in officer_texts(a):
        out += [(where, m.group(0)) for m in _VOICE.finditer(text)]
        if where.startswith("next_steps"):   # a next step must not be a decision; a reply may say "I can't approve your claim"
            out += [(where, line.strip()[:50]) for line in text.split("\n") if _DECISION.search(line.strip())]
    return out


# The reason a customer needs together with an amount, by topic (the topic comes from the question: see focus.py). A reply that gives the amount and not the reason is sent back once;
# if it still lacks it, the reason is added in code from the tool result (_ensure_reason).
_REASON = {
    "room": ("the room rent was reduced because your plan pays for a room only up to a daily limit and the room billed cost more, so the room and the related doctor and nursing charges are paid in proportion.",
             lambda t: "room" in t and re.search(r"limit|cap\b|allowed|maximum|proportion|per day|a day|/day|%", t)),
    "associated": ("doctor and consultation fees are reduced together with the room rent, because the room-rent proportion also applies to them.",
                   lambda t: re.search(r"\broom\b|proportion", t)),
    "non_medical": ("your policy does not pay for non-medical items such as gloves and masks.",
                    lambda t: re.search(r"non-?\s?medical|not (?:cover|pay)\b|(?:doesn't|does not|do not|don't) (?:cover|pay)", t)),
    "hold": ("the amount is held until you send the missing prescription.", lambda t: re.search(r"prescription|document|missing", t)),
}


def _has_reason(reply: str, topic: str) -> bool:
    return bool(_REASON[topic][1](re.sub(r"[\u2010\u2011]", "-", reply).lower()))


def _reason_sentence(ctx: TurnContext) -> str | None:
    """The reason for the topic of the question, built in code from the assessment result. None when the result has no such reason (for example a plan with no room limit)."""
    claims = [r["data"] for r in ctx.results.values() if r["kind"] == "claim"]
    topic = focus_for(ctx.question)
    if not claims or topic not in _REASON:
        return None
    facts = customer_facts(claims[-1])
    room = facts.get("room_rent")
    if topic in ("room", "associated") and room:
        return (f"Your plan pays for a room up to {R.inr(room['plan_limit_per_day'])} a day and yours was {R.inr(room['billed_per_day'])} a day, "
                f"so the room and the related doctor and nursing charges are paid at {R.pct(room['proportion_paid_percent'] / 100)}.")
    nm = facts["non_medical_items"]
    if topic == "non_medical" and nm["count"] and not nm["payable"]:
        ex = " and ".join(x.lower() for x in nm["examples"][:2])
        return f"Your policy does not pay for non-medical items such as {ex}."
    if topic == "hold" and claims[-1]["bill"].get("prescription_missing"):
        return "The amount is held until you send the missing prescription."
    return None


def _ensure_reason(a: dict, ctx: TurnContext) -> dict:
    """The model was asked once for the reason and still left it out: add the sentence built from the tool result."""
    topic, reply = focus_for(ctx.question), a.get("reply") or ""
    if topic in _REASON and re.search(r"[₹\d]", reply) and not _has_reason(reply, topic) and (sentence := _reason_sentence(ctx)):
        return dict(a, reply=f"{reply.rstrip()} {sentence}")
    return a


def _allowed_numbers(ctx: TurnContext) -> dict:
    """What a direct answer may state: the tool results of this turn, the customer's own figures (a what-if asks about ₹6,000 and the answer may say so),
    and the numbers the model passed to the calculating tools, which the engine then used."""
    args = [t.get("args") for t in ctx.trace if t["tool"] in ("assess_claim", "check_waiting_period") and t.get("args")]
    return allowed_from(ctx.tool_outputs + [ctx.question] + args)


_EXC = re.compile(r"(?:\bexcept(?:\s+for)?|\bnot\s+(?:be\s+)?applicable\s+(?:for|to))\s+([^.;:]{3,90})", re.I)
_EXC_STOP = {"claims", "arising", "provided", "covered", "treatment", "policy", "which", "under", "where", "being", "cover", "those", "their", "there", "these", "shall", "made", "such", "with"}


def missing_exception(a: dict, ctx: TurnContext) -> str | None:
    """The first exception (\"except claims arising due to an Accident\") stated by a passage the answer cites, when the answer never mentions what the exception is about. None if all is said."""
    said = " ".join([a.get("headline") or ""] + [f"{p.get('label', '')} {p.get('detail', '')}" for p in a.get("points") or []] + list(a.get("caveats") or []) + list(a.get("next_steps") or [])).lower()
    keys = list(a.get("citations") or []) + [c for p in a.get("points") or [] for c in p.get("citations") or []]
    for key in dict.fromkeys(keys):
        chunk = ctx.seen.get(key)
        if not chunk:
            continue
        for part in _same_rule(chunk, ctx):
            if not (m := _EXC.search(part.text)):
                continue
            words = [w for w in re.findall(r"[a-z]{5,}", m.group(1).lower()) if w not in _EXC_STOP]
            if words and not any(w in said for w in words):
                return re.sub(r"\s+", " ", m.group(0)).strip()[:110]
    return None


def _same_rule(chunk: Chunk, ctx: TurnContext) -> list[Chunk]:
    """The cited passage, the other passages of this turn that belong to the same rule, and the rule's own passage (C.1.b.vi is the list under C.1.b, where the exception is stated,
    even when this turn only retrieved the list)."""
    def rule(c: Chunk) -> str:
        return ".".join(c.clause.split(" ")[0].split(".")[:3])
    parts = [chunk] + [c for c in ctx.seen.values() if c is not chunk and rule(c) == rule(chunk)]
    if rule(chunk) != chunk.clause.split(" ")[0]:      # a deeper clause than the rule: read the rule's passage too
        for cid in resolve([rule(chunk)]):
            parent = ctx.retriever.get_by_chunk_id(cid, ctx.uin)
            if parent and all(parent.chunk_key != c.chunk_key for c in parts):
                parts.append(parent)
    return parts


def _direct_problems(a: dict, ctx: TurnContext) -> list[str]:
    """The rules of a direct_answer: short, a list only for three or more items, known details that have something to show, and only numbers the tools returned."""
    errs, reply = [], (a.get("reply") or "").strip()
    if not reply:
        return ["reply is required for direct_answer."]
    lines = [l for l in reply.split("\n") if l.strip()]
    items = [l for l in lines if _LIST_LINE.match(l)]
    prose = " ".join(l for l in lines if not _LIST_LINE.match(l))
    if count_sentences(prose) > MAX_REPLY_SENTENCES or len(reply.split()) > MAX_REPLY_WORDS:
        errs.append(f"The reply is too long: {count_sentences(prose)} sentences and {len(reply.split())} words. Use at most {MAX_REPLY_SENTENCES} sentences and about 80 words, and put the working in details.")
    if 0 < len(items) < 3:
        errs.append("Use a list only for 3 or more parallel items; write 1 or 2 items as plain sentences.")
    details = a.get("details") or []
    unknown = [d for d in details if d not in DETAILS]
    if unknown:
        errs.append(f"Unknown details {unknown}. The menu is {DETAILS}.")
    have_claim = any(r["kind"] == "claim" for r in ctx.results.values())
    have_wait = any(r["kind"] == "waiting" for r in ctx.results.values())
    for d in details:
        if d in NEEDS_CLAIM_DETAILS and not have_claim:
            errs.append(f"details '{d}' needs assess_claim in this turn. Call it first, or leave '{d}' out.")
        if d == "waiting_period" and not (have_claim or have_wait):
            errs.append("details 'waiting_period' needs assess_claim or check_waiting_period in this turn. Call one first, or leave it out.")
        if d == "policy_reference" and not a.get("citations"):
            errs.append("details 'policy_reference' needs citations from search_policy or get_clause.")
    if have_claim and not ctx.figures_rejected and not ctx.number_rejected and not re.search(r"\d", reply):
        errs.append("Answer with the figures: a payment answer states the actual amounts, percentages or counts from the assess_claim result, "
                    "for example what was deducted and why. Rewrite the reply with them.")
    if have_claim and not ctx.reason_rejected and re.search(r"[₹\d]", reply) and (topic := focus_for(ctx.question)) in _REASON and not _has_reason(reply, topic):
        errs.append(f"Give the reason: {_REASON[topic][0]} Say it in one plain sentence together with the amount, using the figures in customer_facts.")
    if not ctx.number_rejected:
        bad = offenders(reply, _allowed_numbers(ctx))
        if bad:
            hint = " No tool has been called this turn: call get_claim_summary, assess_claim or check_waiting_period first." if not ctx.tool_outputs else ""
            errs.append(f"Numbers in the reply that no tool returned this turn: {bad}. Every amount, percentage, date and count must be copied from a tool result of this turn; "
                        f"do not calculate or round.{hint}")
    return errs


def validate_final(a: dict, ctx: TurnContext) -> list[str]:
    errs, t = [], a.get("answer_type")
    if t not in ANSWER_TYPES:
        return [f"answer_type must be one of {ANSWER_TYPES}."]
    if t != "direct_answer" and t not in NEEDS_CLAIM_RESULT and not (a.get("headline") or "").strip():   # a claim assessment's headline is never shown
        errs.append("headline is required.")
    if t == "direct_answer":
        errs += _direct_problems(a, ctx)
    if len(a.get("headline", "")) > 500:
        errs.append("headline must be under 500 characters.")
    rid = a.get("result_id")
    claim_rids = [k for k, r in ctx.results.items() if r["kind"] == "claim"]
    topic_only = t == "claim_assessment" and focus_for(ctx.question) in _REASON and not re.search(r"how much|assess|what did you find|will be paid|estimate|overall", ctx.question, re.I)
    if ctx.session.get("audience") == "customer" and (t in ("documents_answer", "deduction_explanation") or topic_only) and not ctx.type_rejected:
        errs.append("Customer answer type: a customer's question about one payment topic or about documents gets answer_type direct_answer. Write reply (at most 4 sentences and about 80 words, "
                    "with the figures from assess_claim) and details (documents_checklist, room_working, non_medical_list, estimate_breakdown), and call final_answer again.")
    if ctx.session.get("audience") == "customer" and t == "direct_answer" and policy_kind(ctx.question) in ("coverage", "definition") and not ctx.policy_rejected:
        errs.append("Policy question: this asks what the policy wording says, not about the customer's own claim. Answer with coverage_answer (a verdict, points and citations), "
                    "waiting_period_answer or definition_answer, using the policy passages and their chunk_keys, not with direct_answer.")
    if ctx.session.get("audience") == "customer" and t in ("coverage_answer", "waiting_period_answer") and not ctx.exception_rejected and (exc := missing_exception(a, ctx)):
        errs.append(f"Exception: the passage you cite says \"{exc}\". A customer must not be told the rule without its exception. Say it in the headline or in a point, in plain words.")
    if ctx.plain and t not in ("general_answer", "direct_answer") and not ctx.plain_rejected:
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
    if ctx.session.get("audience") == "customer" and not ctx.voice_rejected:
        voice = voice_problems(a)
        if voice:
            errs.append("Customer wording: you are writing for the customer. Rewrite in \"you\" and \"your claim\", plain words, next steps as \"Please ...\", and do not point at the screen. "
                        "Never say the insured, the claimant, verify, confirm whether, for the officer, or tell anyone to decide. Remove or rephrase: " + " | ".join(f"{w}: '{p}'" for w, p in voice[:4]))
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


def _strip_voice(a: dict) -> dict:
    """The customer-voice guard has asked once. Whatever officer voice is still there is removed, so it never reaches the customer:
    whole next steps, caveats and points, and the sentences of the headline or reply."""
    bad = lambda text: bool(text) and bool(_VOICE.search(text) or _DECISION.search(text.strip()))   # noqa: E731
    out = dict(a)
    for key in ("next_steps", "caveats"):
        if key in out:
            out[key] = [x for x in out[key] or [] if not bad(x)]
    if out.get("points"):
        out["points"] = [p for p in out["points"] if not bad(p.get("label")) and not bad(p.get("detail"))]
    for key in ("headline", "reply"):
        if out.get(key) and bad(out[key]):
            kept = " ".join(s for s in split_sentences(out[key]) if not bad(s))
            out[key] = kept or ("" if key == "headline" else "I couldn't confirm that from your documents.")
    return out


def _fallback_reply(ctx: TurnContext) -> str:
    """A sentence built in code from a tool result, for a reply that had nothing verifiable left."""
    claim = ctx.session.get("claim")
    if claim and (fact := fact_answer(ctx.question, claim)):
        return fact
    claims = [r["data"] for r in ctx.results.values() if r["kind"] == "claim"]
    if claims:
        am = claims[-1]["amounts"]
        return f"Your estimated payment is {R.inr(am['estimated_payable_if_docs_supplied'])}, of which {R.inr(am['payable_confirmed_now'])} is confirmed today."
    return "I couldn't confirm that from your documents."


def _drop_unverified_numbers(a: dict, ctx: TurnContext) -> dict:
    """The second attempt still holds a number no tool returned: drop its sentence, or rebuild the reply in code when nothing is left."""
    allowed, reply = _allowed_numbers(ctx), a.get("reply") or ""
    bad = offenders(reply, allowed)
    if not bad:
        return a
    ctx.numbers_dropped.append(bad)
    kept = drop_sentences(reply, allowed)
    if not kept:
        ctx.number_fallbacks += 1
        kept = _fallback_reply(ctx)
    return dict(a, reply=kept)


def _final(a: dict, ctx: TurnContext) -> dict:
    if ctx.plain == "fact" and ctx.plain_rejected and a.get("answer_type") not in ("general_answer", "direct_answer") and ctx.session.get("claim"):
        # told once, and the model still wants a longer answer for a plain question: the claim's own fields answer it, not the model
        a = dict(answer_type="general_answer", headline=fact_answer(ctx.question, ctx.session["claim"]) or NOT_IN_DOCUMENTS)
    if ctx.session.get("audience") == "customer" and ctx.voice_rejected and voice_problems(a):
        a = _strip_voice(a)
    if a.get("answer_type") == "direct_answer":
        a = dict(a, reply=re.sub(r"\b([a-z]+)_([a-z]+)\b", r"\1 \2", reformat_amounts(a.get("reply") or "")))   # a status code copied from a tool ("not_applicable") is written as words
        if "policy_reference" in (a.get("details") or []) and not a.get("citations"):
            a = dict(a, details=[d for d in a["details"] if d != "policy_reference"])         # nothing to show under it: dropped, not a reason to ask again   # ₹1,22,125, never 122125.0; the value is the same, so the number guard is unaffected
        if ctx.number_rejected:
            a = _drop_unverified_numbers(a, ctx)
    errs = validate_final(a, ctx)
    if any(e.startswith("Numbers in the reply") for e in errs):
        ctx.number_rejected = True             # once; a second attempt with numbers no tool returned loses those sentences (see _drop_unverified_numbers)
    if a.get("answer_type") == "general_answer" and any(e.startswith("You called assess_claim") for e in errs):
        ctx.general_answer_rejected = True   # rejected once: if the model insists on general_answer, its second choice stands
    if any(e.startswith("next_steps are things for the claims officer") for e in errs):
        ctx.decision_wording_rejected = True   # likewise once: a rephrase is asked for, not an endless loop
    if any(e.startswith("Keep the answer short") for e in errs):
        ctx.length_caps_rejected = True         # once: the officer's view shows the top three points either way
    if any(e.startswith("This is a plain question") for e in errs):
        ctx.plain_rejected = True               # once; a persistent fact question is then answered from the claim itself (see the top of this function)
    if any(e.startswith("Exception:") for e in errs):
        ctx.exception_rejected = True
    if any(e.startswith("Policy question:") for e in errs):
        ctx.policy_rejected = True
    if any(e.startswith("Give the reason:") for e in errs):
        ctx.reason_rejected = True
    if any(e.startswith("Customer answer type:") for e in errs):
        ctx.type_rejected = True
    if any(e.startswith("Answer with the figures") for e in errs):
        ctx.figures_rejected = True
    if any(e.startswith("Customer wording:") for e in errs):
        ctx.voice_rejected = True               # once, like the others
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
    if a.get("answer_type") == "direct_answer" and ctx.reason_rejected:
        a = _ensure_reason(a, ctx)
    if ctx.session.get("audience") == "customer" and ctx.exception_rejected and a.get("answer_type") in ("coverage_answer", "waiting_period_answer") and (exc := missing_exception(a, ctx)):
        a = dict(a, caveats=list(a.get("caveats") or []) + [f"The wording makes an exception: {exc}."])   # asked once and still missing: the passage's own words are added
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


def _render_direct(final: dict, ctx: TurnContext) -> R.Rendered:
    """The reply is the summary. Each id in details becomes a collapsed section built by the same code that builds a full assessment, from this turn's tool results."""
    ev = R.Evidence(ctx.retriever, ctx.uin)
    claims = [r["data"] for r in ctx.results.values() if r["kind"] == "claim"]
    waits = [r["data"] for r in ctx.results.values() if r["kind"] == "waiting"]
    res = claims[-1] if claims else None
    secs = []
    for d in dict.fromkeys(final.get("details") or []):
        if d == "room_working" and res:
            secs.append(compact.room_section(res, ev))
        elif d == "non_medical_list" and res:
            secs.append(compact.nonpayable_section(res, ev))
        elif d == "documents_checklist" and res:
            secs.append(compact.documents_section(res, ev))
        elif d == "estimate_breakdown" and res:
            secs.append(compact.estimate_section(res))
        elif d == "waiting_period" and res:
            secs.append(compact.waiting_section(res, ev))
        elif d == "waiting_period" and waits:
            for k in waits[-1]["checks"]:
                ev.add_refs(k["evidence"])
            status = "problem" if any(k["status"] == "violated" for k in waits[-1]["checks"]) else "ok"
            secs.append(compact.section("waiting", "Waiting period", status, compact._body(R._waiting_table_lines(waits[-1]["context"], waits[-1]["checks"]))))
    ev.add_keys(final.get("citations") or [])           # citations are for policy facts only; they become the reference chips
    reply = final["reply"].strip()
    secs += compact._evidence_section(ev)
    full = reply + "".join(f"\n\n### {s['title']}\n{s['markdown']}" for s in secs if s["id"] != "evidence")
    return R.Rendered(full, ev.as_list(), reply, secs)


def _render(final: dict, ctx: TurnContext) -> R.Rendered:
    final = scrub_final(final)   # last line of defence: no internal identifier reaches the officer, whatever the model wrote
    t, rid, ret = final["answer_type"], final.get("result_id"), ctx.retriever
    aud = ctx.session.get("audience", "officer")
    if t == "direct_answer":
        return _render_direct(final, ctx)
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
