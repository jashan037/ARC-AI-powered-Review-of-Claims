"""Turn structured results into the final formatted answer.

Rule: numbers, dates and clause citations are never typed by the LLM. They come from tool results
(claims_engine) and from the retrieved chunks. The LLM only chooses the answer type and writes short
explanations, which are validated before they get here.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from ..retrieval.base import Chunk, Retriever, best_window
from ..tools.evidence import resolve

FOOTER_ASSESS = ("> **Important:** This is an AI-assisted estimate based on the policy wording and the documents provided. "
                 "It is not the final claim decision. The claims officer decides.")
FOOTER_QA = "> AI-assisted answer from the policy wording. A claims officer makes the final decision on any claim."

DOC_SHORT = {
    "claim_form": "Claim form", "photo_id_age_proof": "Photo ID and age proof", "hospital_registration": "Hospital registration certificate",
    "discharge_summary": "Discharge summary", "final_bill_receipts": "Final hospital bill with receipts",
    "implant_invoice_stickers": "Implant invoice and stickers", "previous_consultation_papers": "Previous consultation papers",
    "diagnostic_reports_bills": "Diagnostic reports and bills", "pharmacy_bills_prescription": "Pharmacy bills with prescription",
    "mlc_fir": "MLC / FIR copy", "alcohol_history": "Alcohol / intoxication certificate", "kyc": "KYC documents (claims above Rs. 1 lakh)",
    "neft_form": "NEFT form with cancelled cheque", "ambulance_invoice": "Ambulance invoice"}

STATUS_ICON = {"ok": "🟢", "warning": "🟠", "problem": "🔴", "info": "🔹"}


# ---------------------------------------------------------------- formatting helpers
def inr(n, neg=False) -> str:
    n = int(round(abs(float(n))))
    s = str(n)
    if len(s) > 3:
        head, tail, parts = s[:-3], s[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:]); head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("−" if neg and n else "") + "₹" + s


def d_fmt(iso: str) -> str:
    return dt.datetime.fromisoformat(iso).strftime("%d %b %Y").lstrip("0")


def _val(v) -> str:
    return d_fmt(v) if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else str(v)


def pct(x: float) -> str:
    v = round(x * 100, 2)
    return f"{v:g}%"


def _excerpt(text: str, n: int = 240) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + " …"


@dataclass
class Rendered:
    markdown: str                                                # the full answer, unchanged
    citations: list[dict] = field(default_factory=list)
    summary_markdown: str = ""                                   # the compact version shown first (see compact.py)
    sections: list[dict] = field(default_factory=list)          # [{id, title, status, markdown}] shown on demand


def norm_clause(text: str) -> str:
    """One spelling of a clause label everywhere: 'A.1.2 Def. 5', never 'A1.2 Def. 5'."""
    return re.sub(r"\bA(1\.[12]) Def", r"A.\1 Def", text)


def short_label(citation: str) -> str:
    """'Policy C.1.b, p.28' -> 'C.1.b p.28' (a chip)."""
    return re.sub(r",\s*p\.", " p.", norm_clause(re.sub(r"^Policy\s+", "", citation)))


class Evidence:
    """Collects chunks in order of first use and renders the evidence list."""

    def __init__(self, retriever: Retriever, uin: str | None):
        self.r, self.uin, self.chunks, self.hints = retriever, uin, {}, {}

    def add_refs(self, refs) -> list[Chunk]:
        out = []
        for cid in resolve(refs):
            ch = self.r.get_by_chunk_id(cid, self.uin) or self.r.get_by_chunk_id(cid, None)
            if ch:
                self.chunks.setdefault(ch.chunk_key, ch)
                out.append(ch)
        return out

    def add_keys(self, keys, hint: str | None = None) -> list[Chunk]:
        """hint = the words of the point that cites these chunks; a long clause is then quoted where it says that, not from its first line."""
        out = []
        for k in keys or []:
            ch = self.chunks.get(k) or self.r.get_by_key(k)
            if ch:
                self.chunks.setdefault(ch.chunk_key, ch)
                if hint:
                    self.hints[ch.chunk_key] = f"{self.hints.get(ch.chunk_key, '')} {hint}".strip()
                out.append(ch)
        return out

    def quote(self, c: Chunk, n: int) -> str:
        hint = self.hints.get(c.chunk_key)
        return best_window(c.text, hint, n) if hint else _excerpt(c.text, n)

    @staticmethod
    def label(chs: list[Chunk]) -> str:
        parts = []
        for c in chs:
            t = re.sub(r"\bA(1\.[12]) Def", r"A.\1 Def", c.citation)
            parts.append(t if not parts else re.sub(r"^Policy ", "", t))
        return "; ".join(parts)

    def cite_line(self, refs) -> str:
        chs = self.add_refs(refs)
        return f"**Evidence:** {self.label(chs)}" if chs else ""

    def section(self, limit: int = 8) -> str:
        if not self.chunks:
            return ""
        rows = [f"{i}. **{re.sub(r'\bA(1\.[12]) Def', r'A.\1 Def', c.citation)}** — “{self.quote(c, 240)}”" for i, c in enumerate(list(self.chunks.values())[:limit], 1)]
        return "### Evidence (why the AI said this)\n" + "\n".join(rows)

    def as_list(self) -> list[dict]:
        return [dict(chunk_key=c.chunk_key, citation=norm_clause(c.citation), label=short_label(c.citation), clause=c.clause, excerpt=self.quote(c, 300))
                for c in self.chunks.values()]


def _rule_text(rule: dict, base_si_lakh: float) -> str:
    if rule["type"] == "percent_of_base_si_per_day":
        return f"{rule['percent']:g}% of the {inr(base_si_lakh * 100000)} base sum insured per day"
    if rule["type"] == "single_private_room":
        return "the single private room rate of the hospital"
    return "actuals (no limit)"


def _sum(lines, cat, key):
    return sum(l[key] for l in lines if l["category"] in cat)


# ---------------------------------------------------------------- claim assessment
def render_claim_assessment(res: dict, retriever: Retriever, officer_note: str | None = None, audience: str = "officer") -> Rendered:
    c = res["claim"]
    ev = Evidence(retriever, c.get("policy_uin"))
    out = ["## AI-Assisted Claim Assessment", ""]
    hdr = [f"**Claim** {res['claim_id']} · **Insured** {c['insured_name']} · **Plan** {c['plan']} (base sum insured {inr(c['base_si_lakh'] * 100000)})",
           f"**Treatment** {c['procedure']} for {c['diagnosis']} · {d_fmt(c['admission'])} to {d_fmt(c['discharge'])}"]
    if c.get("policy_uin"):
        hdr.append(f"**Wording applied** UIN {c['policy_uin']}")
    out += ["  \n".join(hdr), ""]
    if res.get("what_if"):
        changes = ", ".join(f"{k.replace('_', ' ')} = {v if k != 'documents' else 'updated'}" for k, v in res["what_if"].items())
        out += [f"> 🔀 **What-if scenario, not the claim as submitted:** {changes}", ""]

    # --- recommendation
    rec = {"likely_eligible": "Likely eligible — subject to human review",
           "likely_eligible_pending_documents": "Likely eligible — pending documents, subject to human review",
           "likely_not_payable": "Likely not payable — subject to human review",
           "needs_human_review": "Needs human review — a provisional estimate is shown below"}[res["recommendation"]]
    out += ["### Recommendation", f"**{rec}**", ""]

    out += _coverage_section(res, ev)
    out += _waiting_section(res, ev)

    stop_early = res["recommendation"] == "likely_not_payable"
    if stop_early:
        out += ["### Bill review", "Not performed. The claim fails an eligibility check above, so room-rent, non-medical and document checks do not change the outcome.", ""]

    # --- room rent
    if not stop_early:
        out += _room_section(res, ev)
    if not stop_early:
        out += _nm_section(res, ev)
    if not stop_early:
        out += _docs_section(res, ev)
    out += _rest(res, ev, officer_note, retriever)
    full = Rendered(_tidy(out), ev.as_list())
    from . import compact
    full.summary_markdown, full.sections = compact.claim_summary(res, audience), compact.claim_sections(res, ev, officer_note)
    return full


def _coverage_section(res, ev):
    cov = res["coverage"]
    icon, head = {"covered": ("🟢", "Hospitalization: Covered"), "not_covered": ("🔴", "Not covered"), "needs_review": ("🟠", "Needs review")}[cov["status"]]
    return ["### Coverage", f"{icon} **{head}**", cov["detail"] + ".", ev.cite_line(cov["evidence"]), ""]


def _waiting_section(res, ev):
    w = res["waiting"]
    ctx, wchecks = w["context"], w["checks"]
    bad = any(k["status"] == "violated" for k in wchecks)
    out = ["### Waiting Period", "🔴 **Not satisfied**" if bad else "🟢 **Satisfied**",
           f"- First policy inception: {d_fmt(ctx['first_inception'])} → admission {d_fmt(ctx['admission_date'])} "
           f"({ctx['elapsed_months']} months, {ctx['elapsed_days']} days)"]
    for k in wchecks:
        word = {"satisfied": "satisfied", "violated": "**not satisfied**", "not_applicable": "not applicable"}[k["status"]]
        out.append(f"- {k['name']} ({k['code']}): {word}. {k['detail']}")
    out += [ev.cite_line([r for k in wchecks if k["status"] != "not_applicable" for r in k["evidence"]] or ["C.1.c"]), ""]
    return out


def _room_section(res, ev):
    c, bill = res["claim"], res["bill"]
    lines = bill["lines"]
    out = []
    out.append("### Room Rent")
    rr = bill["room_rule"]
    room_bill, room_pay = _sum(lines, ("room",), "billed"), _sum(lines, ("room",), "payable")
    assoc_bill, assoc_pay = _sum(lines, ("associated",), "billed"), _sum(lines, ("associated",), "payable")
    if rr["type"] == "at_actuals":
        out += ["🟢 **No room-rent limit**", f"{c['plan']} covers room rent at actuals, so no proportionate deduction applies."]
        refs = ["PLAN:" + c["plan"]]
    elif bill["room_ratio"] < 1:
        out += ["🟠 **Potential deduction**",
                f"- Allowed: {inr(bill['room_limit_per_day'])}/day ({_rule_text(rr, c['base_si_lakh'])})",
                f"- Billed: {inr(bill['room_rate_per_day'])}/day for {bill['room_days']} days",
                f"- Proportion payable: {pct(bill['room_ratio'])} ({inr(bill['room_limit_per_day'])} ÷ {inr(bill['room_rate_per_day'])})",
                f"- Room charges: {inr(room_bill)} → {inr(room_pay)} ({inr(room_bill - room_pay, True)})",
                f"- Associated medical expenses: {inr(assoc_bill)} → {inr(assoc_pay)} ({inr(assoc_bill - assoc_pay, True)})",
                "The same proportion applies to consultation, OT, nursing, anaesthesia and similar charges. Pharmacy, consumables and diagnostics are not reduced."]
        refs = ["B.1.1.1 Note iii", "A.1.2 Def. 5", "PLAN:" + c["plan"]]
    else:
        out += ["🟢 **Within the plan limit**", f"Billed {inr(bill['room_rate_per_day'] or 0)}/day is within the limit for {c['plan']}."]
        refs = ["PLAN:" + c["plan"]]
    out += [ev.cite_line(refs), ""]
    return out


def _nm_section(res, ev):
    bill = res["bill"]
    lines = bill["lines"]
    out = []
    nm = [l for l in lines if l["category"] == "non_medical"]
    out.append("### Non-payable Items")
    if nm and not bill["protect_benefit_in_force"]:
        tot = sum(l["billed"] for l in nm)
        out += [f"🔴 **Potentially non-payable: {inr(tot)}**",
                "These are listed in Annexure B as non-medical items, and the Protect Benefit is not in force on this plan.", "",
                "| Item | Billed | Annexure B |", "|---|---:|---:|"]
        out += [f"| {l['description']} | {inr(l['billed'])} | #{l['annexure_b_no'] or '?'} |" for l in nm]
        out += ["", ev.cite_line(["C.3.k", "Annexure B"]), ""]
    elif nm:
        out += [f"🟢 **Payable under the Protect Benefit: {inr(sum(l['billed'] for l in nm))}**",
                "Annexure B items are covered because the Protect Benefit is in force.", ev.cite_line(["B.2.3", "Annexure B"]), ""]
    else:
        out += ["🟢 **No non-medical items billed**", ""]
    return out


def _docs_section(res, ev):
    out = []
    out.append("### Missing Documents")
    for d in res["documents"]["checklist"]:
        name = DOC_SHORT.get(d["id"], d["name"])
        if d["status"] == "ok":
            out.append(f"- ✅ {name}")
        elif d["status"] == "incomplete":
            out.append(f"- ⚠️ {name} — {', '.join(d['missing_parts']) or 'incomplete'} missing")
        else:
            out.append(f"- ❌ {name} — not submitted")
    if res["documents"]["missing"] or res["documents"]["incomplete"]:
        out.append(ev.cite_line(["E.1.7"]))
    out.append("")
    return out


def _conflict_section(res, ev):
    if not res["inconsistencies"]:
        return []
    out = ["### Conflicts between documents"]
    for i in res["inconsistencies"]:
        out.append(f"- ⚠️ **{i['field'].replace('_', ' ').capitalize()}**: discharge summary says “{_val(i['discharge_summary'])}”, bill says “{_val(i['bill'])}”")
    return out + [ev.cite_line(["E.1.7"]), ""]


def _review_section(res, ev):
    review = [k for k in res["checks"] if k["status"] == "needs_review"]
    if not review:
        return []
    return ["### For the claims officer to review"] + [f"- 🟠 **{k['code']}** — {k['detail']}" for k in review] + [ev.cite_line([r for k in review for r in k["evidence"]]), ""]


def _estimate_rows(res):
    a = res["amounts"]

    def row(label, val):
        return f"{label:<36}{val:>14}"

    d_ = a["deductions"]
    if res["recommendation"] == "likely_not_payable":
        return [row("Hospital bill", inr(a["gross_billed"])), row("Not payable under the policy", inr(a["gross_billed"], True)),
                "-" * 50, row("Estimated insurer payment", inr(0))]
    out = [row("Hospital bill", inr(a["gross_billed"]))]
    for key, label in (("room", "Room-rent adjustment"), ("associated", "Associated-expense adjustment"), ("non_medical", "Non-payable items (Annexure B)")):
        if d_[key]:
            out.append(row(label, inr(d_[key], True)))
    calc = a["calc"]
    if calc["aggregate_deductible"]:
        out.append(row("Aggregate deductible", inr(calc["aggregate_deductible"], True)))
    if calc["copay"]:
        out.append(row("Co-payment", inr(calc["copay"], True)))
    label = "Provisional payment (pending review)" if res["recommendation"] == "needs_human_review" else "Estimated insurer payment"
    out += ["-" * 50, row(label, inr(a["estimated_payable_if_docs_supplied"]))]
    if a["held_pending"]:
        out += [row("  of which held for documents", inr(a["held_pending"])), row("  Confirmed payable today", inr(a["payable_confirmed_now"]))]
    return out


def _next_steps(res, audience="officer"):
    customer = audience == "customer"
    steps = []
    for d in res["documents"]["missing"] + res["documents"]["incomplete"]:
        parts = " and ".join(d.get("missing_parts", [])) or "document"
        name = DOC_SHORT.get(d["id"], d["name"])
        if customer:
            steps.append(f"Please send the missing {parts}." if d.get("missing_parts") and parts.split(" and ")[0].lower() in name.lower() else f"Please send the {parts} for: {name}.")
        else:
            steps.append(f"Request the {parts} for: {name.lower()}.")
    if any(k["status"] == "needs_review" for k in res["checks"]) or res["inconsistencies"]:
        steps.append("A claims officer will review the points flagged above." if customer else "Route to a claims officer for judgement on the flagged points.")
    if res["recommendation"] == "likely_not_payable":
        steps.append("A claims officer will confirm this before any decision is made." if customer else "Confirm the exclusion with the claims officer before communicating a rejection.")
    return steps


def _rest(res, ev, officer_note, retriever):
    bill, a = res["bill"], res["amounts"]
    out = []

    out += _conflict_section(res, ev) + _review_section(res, ev)

    # --- estimate
    out += ["### Estimated Assessment", "```text"] + _estimate_rows(res) + ["```", ""]
    calc = a.get("calc")
    if res["recommendation"] != "likely_not_payable" and calc and calc["aggregate_deductible"]:
        out += [ev.cite_line(["B.2.7", "D.1.19"]), ""]
    if bill["protect_benefit_in_force"] and any(l["category"] == "non_medical" for l in bill["lines"]):
        out += [ev.cite_line(["B.2.3", "C.3.k"]), ""]

    # --- next steps
    steps = _next_steps(res)
    if steps:
        out += ["### Next steps"] + [f"- {s}" for s in steps] + [""]
    if officer_note:
        out += ["### Notes", officer_note, ""]

    out += [ev.section(), "", FOOTER_ASSESS]
    return out


# ---------------------------------------------------------------- deduction explanation
def render_deduction_explanation(res: dict, focus: str, retriever: Retriever, headline: str | None = None, audience: str = "officer") -> Rendered:
    c, bill, a = res["claim"], res["bill"], res["amounts"]
    ev = Evidence(retriever, c.get("policy_uin"))
    lines = bill["lines"]
    f = focus or "all"
    out = ["## How the amount was worked out", ""]
    if headline:
        out += [headline, ""]
    shown = False
    if f in ("room", "associated", "all") and bill["room_ratio"] < 1:
        shown = True
        room_bill, room_pay = _sum(lines, ("room",), "billed"), _sum(lines, ("room",), "payable")
        assoc_bill, assoc_pay = _sum(lines, ("associated",), "billed"), _sum(lines, ("associated",), "payable")
        untouched = sum(l["billed"] for l in lines if l["category"] in ("pharmacy_medicine", "consumables", "diagnostics", "implant"))
        out += ["### Room rent: proportionate deduction",
                f"1. **Plan limit** — {c['plan']} pays room rent up to {_rule_text(bill['room_rule'], c['base_si_lakh'])} = **{inr(bill['room_limit_per_day'])}/day**.",
                f"2. **Bill** — {inr(bill['room_rate_per_day'])}/day for {bill['room_days']} days.",
                f"3. **Proportion** — {inr(bill['room_limit_per_day'])} ÷ {inr(bill['room_rate_per_day'])} = **{pct(bill['room_ratio'])}**.",
                f"4. **Room charges** — {inr(room_bill)} × {pct(bill['room_ratio'])} = {inr(room_pay)} → deduction **{inr(room_bill - room_pay)}**.",
                f"5. **Associated medical expenses** (consultation, OT, nursing, anaesthesia) — {inr(assoc_bill)} × {pct(bill['room_ratio'])} = {inr(assoc_pay)} → deduction **{inr(assoc_bill - assoc_pay)}**.",
                f"6. **Not reduced** — pharmacy, consumables and diagnostics ({inr(untouched)}).", "",
                ev.cite_line(["B.1.1.1 Note iii", "A.1.2 Def. 5", "PLAN:" + c["plan"]]), ""]
    if f in ("non_medical", "all"):
        nm = [l for l in lines if l["category"] == "non_medical"]
        if nm:
            shown = True
            out += ["### Non-medical items"]
            if bill["protect_benefit_in_force"]:
                out += [f"All {len(nm)} items ({inr(sum(l['billed'] for l in nm))}) are payable because the Protect Benefit is in force.", ev.cite_line(["B.2.3", "Annexure B"]), ""]
            else:
                out += [f"{len(nm)} billed items appear in Annexure B and the Protect Benefit is not in force, so {inr(sum(l['billed'] for l in nm))} is not payable.", "",
                        "| Item | Billed | Annexure B |", "|---|---:|---:|"] + [f"| {l['description']} | {inr(l['billed'])} | #{l['annexure_b_no'] or '?'} |" for l in nm] + ["", ev.cite_line(["C.3.k", "Annexure B"]), ""]
    if f in ("hold", "all") and a["held_pending"]:
        shown = True
        out += ["### Amount held for documents", f"{inr(a['held_pending'])} of medicines is on hold because the prescription is missing. "
                f"It becomes payable once supplied, moving the estimate from {inr(a['payable_confirmed_now'])} to {inr(a['estimated_payable_if_docs_supplied'])}.",
                ev.cite_line(["B.1.1.e", "E.1.7.i"]), ""]
    if f in ("deductible", "all") and a["calc"] and (a["calc"]["aggregate_deductible"] or a["calc"]["copay"]):
        shown = True
        out += ["### Deductible and co-payment"]
        if a["calc"]["aggregate_deductible"]:
            out.append(f"- Aggregate deductible: {inr(a['calc']['aggregate_deductible'])} is borne by the insured before the insurer pays (applied first, D.1.19).")
        if a["calc"]["copay"]:
            out.append(f"- Co-payment: {inr(a['calc']['copay'])}.")
        out += [ev.cite_line(["B.2.7", "D.1.19"]), ""]
    if not shown:
        out += ["No deduction applies to the part of the claim you asked about."]
        if a["deductions"] and any(a["deductions"].values()):
            out += ["Other adjustments do exist. Ask for the full assessment to see them."]
        out.append("")
    out += ["### Result", f"Estimated insurer payment: **{inr(a['estimated_payable_if_docs_supplied'])}**"
            + (f" ({inr(a['payable_confirmed_now'])} confirmed today)" if a["held_pending"] else ""), "", ev.section(), "", FOOTER_ASSESS]
    full = Rendered(_tidy(out), ev.as_list())
    from . import compact
    full.summary_markdown, full.sections = compact.deduction(res, full.markdown, ev, audience)
    return full


# ---------------------------------------------------------------- waiting period answer
def render_waiting(data: dict, final: dict, retriever: Retriever, uin: str | None, audience: str = "officer") -> Rendered:
    ev = Evidence(retriever, uin)
    ctx, checks = data["context"], data["checks"]
    out = ["## Waiting period check", "", final["headline"], ""] + _waiting_table_lines(ctx, checks)
    for k in checks:
        ev.add_refs(k["evidence"])
    out += _tail(final, ev, uin)
    full = Rendered(_tidy(out), ev.as_list())
    from . import compact
    full.summary_markdown, full.sections = compact.waiting(data, final, ev, audience)
    return full


def _waiting_table_lines(ctx, checks):
    out = [f"First policy inception {d_fmt(ctx['first_inception'])} · treatment date {d_fmt(ctx['admission_date'])} · "
           f"**{ctx['elapsed_months']} months ({ctx['elapsed_days']} days)** of continuous cover", "",
           "| Waiting period | Applies? | Required | Elapsed | Result |", "|---|---|---|---|---|"]
    for k in checks:
        applies = "No" if k["status"] == "not_applicable" else "Yes"
        elapsed = f"{ctx['elapsed_days']} days" if k["code"] == "Excl03" else f"{ctx['elapsed_months']} months"
        res = {"satisfied": "🟢 Satisfied", "violated": "🔴 Not yet satisfied", "not_applicable": "➖ Not applicable"}[k["status"]]
        out.append(f"| {k['name']} ({k['code']}) | {applies} | {k['required']} | {elapsed if applies == 'Yes' else '–'} | {res} |")
    out += [""]
    for k in checks:
        if k.get("eligible_from"):
            out.append(f"- **{k['name']}** is served from **{d_fmt(k['eligible_from'])}** (after {k['required']} of continuous cover).")
    return out


# ---------------------------------------------------------------- documents answer
def render_documents(res: dict, final: dict, retriever: Retriever, audience: str = "officer") -> Rendered:
    ev = Evidence(retriever, res["claim"].get("policy_uin"))
    out = ["## Documents check", "", final["headline"], ""]
    for d in res["documents"]["checklist"]:
        name = DOC_SHORT.get(d["id"], d["name"])
        out.append({"ok": f"- ✅ {name}", "incomplete": f"- ⚠️ {name} — {', '.join(d.get('missing_parts', []))} missing",
                    "missing": f"- ❌ {name} — not submitted"}[d["status"]])
    ev.add_refs(["E.1.7", "E.1.6"])
    out += _tail(final, ev, res["claim"].get("policy_uin"))
    full = Rendered(_tidy(out), ev.as_list())
    from . import compact
    full.summary_markdown, full.sections = compact.documents(res, final, ev, audience)
    return full


# ---------------------------------------------------------------- generic Q&A (coverage, definitions, insufficient info, general)
VERDICT = {"covered": "🟢 Likely covered", "covered_with_conditions": "🟠 Covered, with conditions", "not_covered": "🔴 Not covered",
           "depends": "🟠 Depends on the facts", "insufficient_information": "⚪ Insufficient information", "not_applicable": "🔹 Answer"}


def _tail(final: dict, ev: Evidence, uin: str | None) -> list[str]:
    ev.add_keys(final.get("citations"))
    out = [""]
    if final.get("next_steps"):
        out += ["### What to check next"] + [f"- {s}" for s in final["next_steps"]] + [""]
    if final.get("caveats"):
        out += ["### Notes"] + [f"- {s}" for s in final["caveats"]] + [""]
    out += [ev.section(), "", FOOTER_QA]
    return out


def render_qa(final: dict, retriever: Retriever, uin: str | None, audience: str = "officer") -> Rendered:
    ev = Evidence(retriever, uin)
    t = final["answer_type"]
    if t == "insufficient_information":
        title = "⚪ Insufficient information"
    elif t == "coverage_answer":
        title = VERDICT.get(final.get("verdict", "depends"), "🔹 Answer")
    elif t == "definition_answer":
        title = "📖 What the policy says"
    else:
        title = "Answer"
    out = [f"## {title}", "", final["headline"], ""]
    if final.get("points"):
        heading = {"coverage_answer": "Conditions and checks", "definition_answer": "Key points"}.get(t, "Key points")
        out.append(f"### {heading}")
        for p in final["points"]:
            chs = ev.add_keys(p.get("citations"), hint=f"{p['label']} {p['detail']}")
            ref = f" *({Evidence.label(chs)})*" if chs else ""
            out.append(f"{STATUS_ICON.get(p['status'], '🔹')} **{p['label']}** — {p['detail']}{ref}")
        out.append("")
    out += _tail(final, ev, uin)
    full = Rendered(_tidy(out), ev.as_list())
    from . import compact
    full.summary_markdown, full.sections = compact.qa(final, ev, audience)
    return full


def _tidy(parts: list[str]) -> str:
    text = "\n".join(p for p in parts if p is not None)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
