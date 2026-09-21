"""The tools the assistant can call and what they return.

Function tools run here in the backend. Their outputs are plain JSON with plain-language field names, because the model writes its reply from them:
a figure it states must be one of these values (the number guard in guards.py checks that). Nothing here writes an answer.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from ..config import settings
from ..rendering.customer_labels import customerize, section_name
from ..resilience import TurnAbort
from ..retrieval.base import Chunk, Retriever
from . import claims_engine as E
from .evidence import resolve
from .fmt import DOC_SHORT, inr, rule_text, sum_lines
from . import facts
from .sanitize import clean
from .totals import derived_totals

_S, _N, _B = {"type": "string"}, {"type": "number"}, {"type": "boolean"}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


SCHEMAS = [
    dict(name="search_policy", description="Search the policy wording for the wording that applies to this claim. Use it for any question about what the policy covers, excludes, defines or requires. "
         "Returns passages with their section name and page. Search again with different words if the passages do not answer the question.",
         parameters=_obj({"query": {**_S, "description": "Plain-language query, for example 'waiting period for cataract surgery'."},
                          "top_k": {"type": "integer", "description": "How many passages (default 5, at most 8)."}}, ["query"])),
    dict(name="get_clause", description="Read one clause of the policy by its number as printed in the policy, for example 'C.1.b', 'B.1.1.1 Note iii', 'E.1.7' or 'Annexure B'. Use search_policy when unsure of the number.",
         parameters=_obj({"clause_ref": _S}, ["clause_ref"])),
    dict(name="check_waiting_period", description="Check the waiting periods for two dates the customer gave, or for any dates you need. Never work out months or days yourself.",
         parameters=_obj({"first_policy_inception": {**_S, "description": "Date the first policy started, YYYY-MM-DD."},
                          "admission_date": {**_S, "description": "Date of admission or treatment, YYYY-MM-DD."},
                          "diagnosis": _S, "procedure": _S, "is_accident": _B, "pre_existing": _B,
                          "prior_continuous_coverage_months": {**_N, "description": "Months of continuous cover ported from another insurer."}},
                         ["first_policy_inception", "admission_date"])),
    dict(name="lookup_non_medical_item", description="Check whether a billed item is on the policy's list of non-medical items (not paid unless the Protect Benefit applies).",
         parameters=_obj({"item": _S}, ["item"])),
    dict(name="get_claim_summary", description="Everything read from the customer's documents, as labelled lines: the whole policy schedule (policy number, plan, start and expiry dates, sum insured, limits, deductible, co-pay, benefits), the hospital stay, amounts, documents received and missing, and a list of what was not found.",
         parameters=_obj({})),
    dict(name="cover_left", description="How much of the customer's cover amount would be left after this claim and after any other claims the customer says they made or will make this policy year. "
         "Use it for questions like 'how much cover do I have left' or 'if I already claimed 3 lakh'. Amounts the customer states are assumed paid in full and unverified.",
         parameters=_obj({"extra_claims": {"type": "array", "items": _N, "description": "Amounts (rupees) of OTHER claims the customer stated, e.g. [300000]. Never this claim's own estimate or bill: the tool already subtracts this claim."}})),
    dict(name="assess_claim", description="The assessment of the customer's claim: what is likely to be paid, what was taken off and why, the room-rent limit, the non-medical items, missing documents and waiting periods. "
         "It has already been run for this turn; call it again only to test a change (a what-if), for example {'room_rate_per_day': 5000}.",
         parameters=_obj({"what_if": {"type": "object", "description": "Changes to test. Allowed keys: first_policy_inception, admission_datetime, plan, base_si_lakh, room_rate_per_day, protect_benefit_opted, "
                          "aggregate_deductible_remaining, is_accident, pre_existing, copay_percent, diagnosis, procedure, documents, plan_room_limit_per_day. room_rate_per_day means \"the hospital had charged this rate\" (the room charge, the bill and the claimed amount change); "
                          "plan_room_limit_per_day means \"if my plan allowed this much a day\" (the bill stays).", "additionalProperties": True}})),
]
TOOL_NAMES = frozenset(s["name"] for s in SCHEMAS)


@dataclass
class TurnContext:
    session: dict
    retriever: Retriever
    question: str = ""
    seen: dict = field(default_factory=dict)         # chunk_key -> Chunk: every policy passage a tool returned this turn, in the order returned
    results: dict = field(default_factory=dict)      # "assessment" -> the engine result (the last one), "waiting" -> the last waiting-period result
    trace: list = field(default_factory=list)
    tool_outputs: list = field(default_factory=list)  # what the tools returned this turn: the number guard checks the reply against it
    prerun: bool = False

    @property
    def uin(self) -> str:
        claim = self.session.get("claim") or {}
        return claim.get("policy_uin") or self.session.get("uin") or settings.default_uin

    @property
    def claim(self) -> dict | None:
        return self.session.get("claim")


_REC = {"likely_eligible": "likely eligible", "likely_eligible_pending_documents": "likely eligible once the missing documents arrive",
        "likely_not_payable": "likely not payable", "needs_human_review": "needs a claims officer's review"}
_STATUS = {"satisfied": "already met", "violated": "not yet met", "not_applicable": "does not apply"}


def assessment_view(res: dict) -> dict:
    """The engine's result in plain-language fields: the amounts, why each amount was taken off, and what is missing. Only values the engine produced."""
    bill, claim, a = res["bill"], res["claim"], res["amounts"]
    lines = bill["lines"]
    pat = [k for k in res["checks"] if k["code"] == "PATIENT"]
    out = {"policy_in_force_on_the_admission_date": E.policy_in_force_text(res["policy_in_force"]) if res.get("policy_in_force") else "The policy period is not in the documents, so this could not be checked.",
           **({"time_limit_for_sending_documents": E.filing_text(res["filing"])} if res.get("filing") else {}),
           **({"patient_is_the_insured_person": pat[0]["detail"]} if pat else {}),
           "likely_outcome": _REC.get(res["recommendation"], res["recommendation"]), "hospital_bill_total": a["gross_billed"],
           "estimated_payment_once_documents_arrive": a["estimated_payable_if_docs_supplied"], "payment_counted_so_far": a["payable_confirmed_now"],
           "held_until_documents_arrive": a["held_pending"],
           "taken_off": {"room_rent": a["deductions"]["room"], "doctor_and_other_associated_fees": a["deductions"]["associated"], "non_medical_items": a["deductions"]["non_medical"]},
           "issues": [customerize(x) for x in E.compact_summary(res).get("issues", [])], "plan": claim["plan"]}
    if bill["room_rule"]["type"] != "at_actuals" and bill["room_ratio"] < 1:
        out["room_rent"] = dict(plan_limit_per_day=bill["room_limit_per_day"], billed_per_day=bill["room_rate_per_day"], days=bill["room_days"], share_paid_percent=round(bill["room_ratio"] * 100, 1),
                                rule=rule_text(bill["room_rule"], claim["base_si_lakh"]),
                                the_same_share_applies_to="the room charges and the associated medical expenses (doctor and consultation fees, operation theatre, nursing, anaesthesia)")
    nm = [l for l in lines if l["category"] == "non_medical"]
    out["non_medical_items"] = dict(count=len(nm), total=sum_lines(lines, ("non_medical",), "billed"), examples=[l["description"] for l in nm[:5]], paid_under_protect_benefit=bool(bill["protect_benefit_in_force"]))
    out["associated_medical_expenses"] = dict(billed=sum_lines(lines, ("associated",), "billed"), payable=sum_lines(lines, ("associated",), "payable"))
    out["totals_by_cause"] = derived_totals(res)
    out["documents_missing"] = [DOC_SHORT.get(d["id"], d["name"]) for d in res["documents"]["checklist"] if d["status"] != "ok"]
    out["waiting_periods"] = [dict(rule=k["name"], result=_STATUS.get(k["status"], k["status"])) for k in res["waiting"]["checks"]]
    if res.get("what_if"):
        out["what_if_changes"] = res["what_if"]
        out["what_if_assumptions"] = res.get("what_if_assumptions", [])
        out["before_the_change"] = dict(estimated_payment=res["baseline"]["estimated"], counted_so_far=res["baseline"]["confirmed"])
    return out


def _remember(ctx: TurnContext, chunks: list[Chunk]) -> None:
    for c in chunks:
        ctx.seen.setdefault(c.chunk_key, c)


def _passage(c: Chunk, limit: int, query: str | None = None) -> dict:
    text = c.short(limit, query=query)["excerpt"] if query else c.short(limit)["excerpt"]
    return {"section": section_name(c), "page": c.page_start, "text": customerize(text)}


def call_tool(name: str, args: dict, ctx: TurnContext) -> dict:
    """Never raises (except TurnAbort). Errors go back to the model as {'error': ...} so it can recover."""
    t0 = time.perf_counter()
    try:
        out = _dispatch(name, args or {}, ctx)
    except TurnAbort:   # deadline gone or Azure unavailable: the turn must stop, not go back to the model as a tool error
        raise
    except Exception as e:  # noqa: BLE001 - tool errors must reach the model, not crash the request
        out = {"error": f"{type(e).__name__}: {e}"}
    ok = "error" not in out
    ctx.trace.append(dict(tool=name, args=args, ok=ok, ms=round((time.perf_counter() - t0) * 1000)))
    if ok:
        ctx.tool_outputs.append(out)
    return out


def _dispatch(name: str, a: dict, ctx: TurnContext) -> dict:
    if name == "search_policy":
        chunks = ctx.retriever.search(a["query"], uin=ctx.uin, top_k=max(1, min(int(a.get("top_k") or 5), 8)))
        _remember(ctx, chunks)
        return {"passages": [_passage(c, 1500, a["query"]) for c in chunks], "note": "" if chunks else "No passages matched. Try different words, or tell the customer the wording does not say."}

    if name == "get_clause":
        ids = resolve(a["clause_ref"])
        chunks = [c for c in (ctx.retriever.get_by_chunk_id(i, ctx.uin) or ctx.retriever.get_by_chunk_id(i, None) for i in ids) if c]
        if not chunks:
            return {"error": f"Could not find clause '{a['clause_ref']}'. Use search_policy instead."}
        _remember(ctx, chunks)
        return {"passages": [_passage(c, 4000) for c in chunks]}

    if name == "check_waiting_period":
        mini = dict(first_policy_inception=a["first_policy_inception"], admission_datetime=a["admission_date"] + "T00:00", discharge_datetime=a["admission_date"] + "T23:59",
                    diagnosis=a.get("diagnosis", ""), procedure=a.get("procedure", ""), is_accident=bool(a.get("is_accident")), pre_existing=bool(a.get("pre_existing")),
                    prior_continuous_coverage_months=int(a.get("prior_continuous_coverage_months") or 0))
        checks, wctx = E.check_waiting_period(mini), E.waiting_context(mini)
        ctx.results["waiting"] = dict(context=wctx, checks=checks)
        for k in checks:
            if k["status"] != "not_applicable":
                _remember(ctx, [c for c in (ctx.retriever.get_by_chunk_id(i, ctx.uin) for i in resolve(k["evidence"][:1])) if c])
        return dict(months_since_the_policy_started=wctx["elapsed_months"], days_since_the_policy_started=wctx["elapsed_days"],
                    rules=[dict(rule=k["name"], result=_STATUS.get(k["status"], k["status"]), required=customerize(str(k["required"])), detail=customerize(k["detail"]),
                                **({"can_be_claimed_from": k["eligible_from"]} if k.get("eligible_from") else {})) for k in checks])

    if name == "lookup_non_medical_item":
        res = E.lookup_non_medical_item(a["item"])
        _remember(ctx, [c for c in (ctx.retriever.get_by_chunk_id("C3-k", ctx.uin),) if c])
        out = dict(item=a["item"], on_the_policys_non_medical_list=res["listed_in_annexure_b"], closest_matches=[m["item"] for m in res["matches"]], note=customerize(res["note"]))
        if ctx.claim:
            out["paid_under_this_claims_protect_benefit"] = bool(E.protect_in_force(ctx.claim))
        return out

    if name == "get_claim_summary":
        if not ctx.claim:
            return {"loaded": False, "message": "No claim is loaded."}
        return facts.summary(ctx.session)

    if name == "cover_left":
        if not ctx.claim:
            return {"error": "No claim is loaded."}
        return cover_left(ctx, [float(x) for x in (a.get("extra_claims") or [])])

    if name == "assess_claim":
        if not ctx.claim:
            return {"error": "No claim is loaded."}
        changed = E.apply_what_if(ctx.claim, a.get("what_if"))
        res = E.assess(changed)
        res["what_if_assumptions"] = changed.get("what_if_assumptions", [])
        res["what_if"] = {k: v for k, v in (a.get("what_if") or {}).items() if k in E.WHAT_IF_KEYS or k == "documents"} or None
        if res["what_if"]:
            base = E.assess(ctx.claim)
            res["baseline"] = dict(estimated=base["amounts"]["estimated_payable_if_docs_supplied"], confirmed=base["amounts"]["payable_confirmed_now"])
        ctx.results["assessment"] = res
        return assessment_view(res)
    return {"error": f"Unknown tool '{name}'."}


def cover_left(ctx: TurnContext, extra: list[float]) -> dict:
    """Cover amount (base sum insured + cumulative bonus + Plus Benefit if opted) minus this claim's estimate minus the amounts the customer stated. Arithmetic in code."""
    claim = ctx.claim
    sched = (ctx.session.get("documents") or {}).get("policy_schedule", {}).get("fields", {})
    base = claim["base_si_lakh"] * 100000
    bonus = sched.get("bonus")
    if bonus is None:
        bonus = max(0.0, (claim.get("sum_insured_available") or base) - base)
    est0 = ctx.results.get("assessment") or E.assess(claim)
    own = {round(v, 2) for v in (est0["amounts"]["estimated_payable_if_docs_supplied"], est0["amounts"]["payable_confirmed_now"], est0["amounts"]["gross_billed"], est0["amounts"]["held_pending"]) if v}
    ignored = [x for x in extra if round(x, 2) in own]
    extra = [x for x in extra if round(x, 2) not in own]                # this claim's own figures are subtracted by this tool: passing them again would count the claim twice
    assumptions = ["The other claims you mention are assumed to be paid in full; I can't verify them."] if extra else []
    if ignored:
        assumptions.append("An amount you gave matches this claim's own figures, so it was not counted a second time.")
    plus_note = None
    if sched.get("plus_benefit_opted"):
        plus_note = "Your schedule shows the Plus Benefit as opted, but I can't read its amount, so it is not included in the cover amount."
        assumptions.append(plus_note)
    cover = base + bonus
    est = ctx.results.get("assessment") or E.assess(claim)
    this_claim = est["amounts"]["estimated_payable_if_docs_supplied"]
    left = cover - this_claim - sum(extra)
    if left < 0:
        assumptions.append("The claims add up to more than your cover amount, so nothing would be left.")
    restore = sched.get("restore_benefit_text")
    out = dict(cover_amount=cover, base_sum_insured=base, cumulative_bonus=bonus, this_claim_estimate=this_claim, other_claims_you_mentioned=[dict(amount=x, status="stated by you, unverified") for x in extra],
               cover_left=max(0.0, round(left, 2)), assumptions=assumptions)
    if restore:
        out["restore_benefit_on_your_schedule"] = clean(restore)
    return out


_MONTH_YEAR = re.compile(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?,?\s+(\d{4})\b", re.I)
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def date_notes(ctx: TurnContext) -> list[str]:
    """What the policy period says about each date, or month, the customer mentioned: worked out in code so a question like "was I covered on 20 April 2026?" is answered from the engine."""
    claim = ctx.claim
    period = (claim or {}).get("policy_period")
    if not period or not all(period):
        return []
    import calendar
    import datetime as dt
    from .number_guard import scan
    fmt = lambda d: d.strftime("%d %b %Y").lstrip("0")   # noqa: E731
    start, end = dt.date.fromisoformat(period[0]), dt.date.fromisoformat(period[1])
    notes = []
    for (y, m, d), _raw in scan(ctx.question)["dates"]:
        if y and 1990 < y < 2100:
            try:
                r = E.policy_in_force(dict(policy_period=period, admission_datetime=f"{y:04d}-{m:02d}-{d:02d}T00:00"))
            except ValueError:
                continue
            notes.append(E.policy_in_force_text(r).replace("on the admission date", "on that date").replace("the admission date", "that date"))
    stripped = re.sub(r"\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?,?\s+\d{4}\b|\b[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b", " ", ctx.question)
    for mon, yr in _MONTH_YEAR.findall(stripped):
        mi, y = _MONTHS.index(mon[:3].lower()) + 1, int(yr)
        first, last = dt.date(y, mi, 1), dt.date(y, mi, calendar.monthrange(y, mi)[1])
        name = f"{calendar.month_name[mi]} {y}"
        if start <= first and last <= end:
            notes.append(f"All of {name} is inside your policy period ({fmt(start)} to {fmt(end)}).")
        elif last < start or first > end:
            notes.append(f"{name} is {'before the start' if last < start else 'after the end'} of your policy period ({fmt(start)} to {fmt(end)}): your policy was not in force then.")
        else:
            inside = (max(first, start), min(last, end))
            notes.append(f"{name} is only partly inside your policy period ({fmt(start)} to {fmt(end)}): {fmt(inside[0])} to {fmt(inside[1])} is inside it, the rest is not.")
    return list(dict.fromkeys(notes))


def precompute(ctx: TurnContext) -> dict | None:
    """Run assess_claim in code before the model's first call, so that most questions need one model call. The result counts as a tool result of this turn."""
    if not ctx.claim:
        return None
    out = call_tool("assess_claim", {}, ctx)
    ctx.prerun = "error" not in out
    if ctx.prerun and (notes := date_notes(ctx)):
        out["dates_you_mentioned"] = notes            # the same dict is in ctx.tool_outputs, so these dates count as tool results for the number guard
    return out if ctx.prerun else None


def fallback_reply(ctx: TurnContext) -> str:
    """A sentence built in code from the assessment, for a reply with nothing verifiable left."""
    res = ctx.results.get("assessment")
    if res:
        a = res["amounts"]
        return f"Your estimated payment is {inr(a['estimated_payable_if_docs_supplied'])}, of which {inr(a['payable_confirmed_now'])} is counted so far."
    return "I couldn't confirm that from your documents."
