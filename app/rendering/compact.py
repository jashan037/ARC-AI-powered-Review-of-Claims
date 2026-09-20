"""Compact answers: a short summary first, the details in named sections the officer can open.

`answer_markdown` (the full answer) is produced by render.py and is never changed by this module. Everything here is built from the same
structured data and the same helpers, so the summary and the sections cannot disagree with the full answer, and every figure is still
computed by code, never typed by the model.

Rule for every answer type: red flags are never hidden. A claim that is not payable, needs human review or has conflicting documents
says so in the summary itself, whatever else is collapsed. One short "The officer decides" line closes each summary.
"""
from __future__ import annotations

import re

from . import render as R
from .render import DOC_SHORT, STATUS_ICON, VERDICT, d_fmt, inr

OFFICER_LINE = "> **The officer decides.** AI-assisted, not a decision."

REC_TEXT = {"likely_eligible": "Likely eligible — subject to human review",
            "likely_eligible_pending_documents": "Likely eligible — pending documents, subject to human review",
            "likely_not_payable": "Likely not payable — subject to human review",
            "needs_human_review": "Needs human review — provisional estimate"}
REC_TEXT_CUSTOMER = {"likely_eligible": "Likely eligible — a claims officer makes the final decision",
                     "likely_eligible_pending_documents": "Likely eligible once your documents are complete",
                     "likely_not_payable": "Likely not payable — a claims officer will confirm",
                     "needs_human_review": "A claims officer needs to review this — provisional estimate"}
REC_ICON = {"likely_eligible": "🟢", "likely_eligible_pending_documents": "🟠", "likely_not_payable": "🔴", "needs_human_review": "🟠"}


def _close(audience: str) -> list[str]:
    """The line that closes a summary. The customer page shows its own disclaimer footer, so the customer summary does not repeat it."""
    return [] if audience == "customer" else [OFFICER_LINE]


# ---------------------------------------------------------------- small helpers
def _tidy(parts) -> str:
    return re.sub(r"\n{3,}", "\n\n", "\n".join(p for p in parts if p is not None)).strip() + "\n"


def _body(lines) -> str:
    """A section body: the helper's lines without its own '### Heading' (the section title replaces it)."""
    lines = list(lines)
    if lines and lines[0].startswith("### "):
        lines = lines[1:]
    return _tidy(lines).strip()


def section(sid: str, title: str, status: str, markdown: str) -> dict:
    return dict(id=sid, title=title, status=status, markdown=markdown.strip() + "\n")


def one_line(text: str, n: int = 160) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0].rstrip(",;:") + "…"


_BOILERPLATE = [re.compile(p, re.I) for p in (
    r"\bfinal (decision|settlement|payment|determination|audit|review)\b", r"\b(claims? )?officer (makes|decides|will decide|has the final)\b",
    r"\bai[- ]assisted\b", r"\bnot (a|the) (final )?(decision|determination|approval|guarantee)\b",
    r"\b(based|relies|rely) (solely |only |purely )?on (the )?(policy )?wording\b", r"\blimited (solely |only )?to (the )?(policy )?wording\b", r"\banswer is limited to\b",
    r"\bsubject to (final )?(review|verification|audit)\b")]


def useful_caveats(items) -> list[str]:
    """Model-written notes minus the clauses that only restate the standard disclaimer (that line is already shown once)."""
    out = []
    for text in items or []:
        keep = [c.strip() for c in re.split(r"(?<=[.!?;])\s+", str(text)) if c.strip() and not any(p.search(c) for p in _BOILERPLATE)]
        if keep:
            out.append(" ".join(keep).rstrip(";"))
    return out


def what_if_words(changes: dict) -> str:
    """The what-if overrides in plain words with currency, e.g. 'Room rent per day = ₹5,000'."""
    def money(v):
        return inr(v)

    def yes(v):
        return "yes" if v else "no"

    words = {"room_rate_per_day": ("Room rent per day", money), "base_si_lakh": ("Base sum insured", lambda v: inr(float(v) * 100000)),
             "aggregate_deductible_remaining": ("Aggregate deductible remaining", money), "protect_benefit_opted": ("Protect Benefit opted", yes),
             "is_accident": ("Accident", yes), "pre_existing": ("Pre-existing disease", yes), "copay_percent": ("Co-payment", lambda v: f"{v:g}%"),
             "first_policy_inception": ("First policy inception", lambda v: R._val(str(v)[:10])), "plan": ("Plan", str),
             "admission_datetime": ("Admission", lambda v: d_fmt(str(v))), "diagnosis": ("Diagnosis", str), "procedure": ("Procedure", str),
             "documents": ("Documents", lambda v: "updated")}
    parts = []
    for k, v in changes.items():
        label, fmt = words.get(k, (k.replace("_", " ").capitalize(), str))
        parts.append(f"{label} = {fmt(v)}")
    return ", ".join(parts)


# ---------------------------------------------------------------- claim assessment
def claim_reasons(res: dict, audience: str = "officer") -> list[tuple[float, str]]:
    """What moves the estimate, largest rupee impact first. Each item is (amount, one line)."""
    a, bill = res["amounts"], res["bill"]
    d, calc = a["deductions"], a.get("calc") or {}
    out = []
    room = d["room"] + d["associated"]
    if room:
        out.append((room, f"Room rent above your plan's limit: {inr(room, True)}" if audience == "customer" else f"Room rent above the plan limit: {inr(room, True)}"))
    if d["non_medical"]:
        n = sum(1 for l in bill["lines"] if l["category"] == "non_medical" and l["status"] == "non_payable")
        what = "your policy doesn't cover" if audience == "customer" else "(Annexure B)"
        out.append((d["non_medical"], f"{n} non-medical item{'s' if n != 1 else ''} {what}: {inr(d['non_medical'], True)}"))
    if calc.get("aggregate_deductible"):
        out.append((calc["aggregate_deductible"], f"Aggregate deductible: {inr(calc['aggregate_deductible'], True)}"))
    if calc.get("copay"):
        out.append((calc["copay"], f"Co-payment: {inr(calc['copay'], True)}"))
    if calc.get("sum_insured_cap_applied"):
        capped = calc["admissible_before_deductible"] - calc["aggregate_deductible"] - calc["copay"] - calc["payable"]
        if capped > 0:
            out.append((capped, f"Capped at the available sum insured: {inr(capped, True)}"))
    if a["held_pending"]:
        what = "Prescription missing" if bill.get("prescription_missing") else "Documents missing"
        out.append((a["held_pending"], f"{what}: {inr(a['held_pending'])} " + ("held until it arrives" if audience == "customer" else "on hold")))
    return sorted(out, key=lambda x: -x[0])


def red_flags(res: dict, audience: str = "officer") -> list[str]:
    """Not payable, needs human review, document conflicts. These are always shown in the summary."""
    out = []
    for k in res["checks"]:
        if k["status"] == "violated":
            out.append(f"🔴 **{k['name']}** — {k['detail']}")
    for k in res["checks"]:
        if k["status"] == "needs_review":
            out.append(f"🟠 **A claims officer will review this:** {k['detail']}" if audience == "customer" else f"🟠 **For the officer to review — {k['code']}**: {k['detail']}")
    for i in res["inconsistencies"]:
        out.append(f"⚠️ **Document conflict — {i['field'].replace('_', ' ')}**: discharge summary says “{R._val(i['discharge_summary'])}”, bill says “{R._val(i['bill'])}”")
    return out


def claim_summary(res: dict, audience: str = "officer") -> str:
    c, a = res["claim"], res["amounts"]
    rec = res["recommendation"]
    rec_text = (REC_TEXT_CUSTOMER if audience == "customer" else REC_TEXT)[rec]
    out = [f"**Claim {res['claim_id']}** · {c['insured_name']} · {c['plan']} · {c['procedure']} for {c['diagnosis']}", ""]

    if res.get("what_if"):
        out += [f"> 🔀 **What-if scenario, not the claim as submitted:** {what_if_words(res['what_if'])}"]
        base = res.get("baseline")
        if base:
            out += [f"> **Estimated payment {inr(base['estimated'])} → {inr(a['estimated_payable_if_docs_supplied'])}**"
                    + (f" · confirmed today {inr(base['confirmed'])} → {inr(a['payable_confirmed_now'])}" if base["confirmed"] != a["payable_confirmed_now"] or a["held_pending"] else "")
                    + (f" · {REC_TEXT[base['recommendation']].split(' —')[0]} → {REC_TEXT[rec].split(' —')[0]}" if base["recommendation"] != rec else "")]
        out += [""]

    out += [f"{REC_ICON[rec]} **{rec_text}**", ""]
    flags = red_flags(res, audience)

    if rec == "likely_not_payable":
        violated = [k for k in res["checks"] if k["status"] == "violated"]
        out += ["**Not payable** — " + "; ".join(f"{k['name']}: {k['detail']}" for k in violated), ""]
        out += [f"- {f}" for f in flags if not f.startswith("🔴")]
    else:
        out += [f"- {f}" for f in flags]
        if flags:
            out.append("")
        first = "Provisional payment" if rec == "needs_human_review" else "Estimated payment"
        out += [f"| {first} | Confirmed today | Held for documents |", "|---:|---:|---:|",
                f"| **{inr(a['estimated_payable_if_docs_supplied'])}** | **{inr(a['payable_confirmed_now'])}** | **{inr(a['held_pending'])}** |", ""]
        reasons = claim_reasons(res, audience)
        out.append("**Main reasons**")
        if reasons:
            out += [f"{i}. {text}" for i, (_, text) in enumerate(reasons[:3], 1)]
            if len(reasons) > 3:
                out.append(f"   *+ {len(reasons) - 3} more in the details*")
        else:
            out.append("No deductions. The bill is payable in full.")
        out.append("")

    steps = R._next_steps(res, audience)[:2]
    if steps:
        out += ["**Next steps**"] + [f"- {s}" for s in steps] + [""]
    out += _close(audience)
    return _tidy(out)


def claim_sections(res: dict, ev, officer_note: str | None) -> list[dict]:
    bill, rec = res["bill"], res["recommendation"]
    lines = bill["lines"]
    secs = []
    conflicts = R._conflict_section(res, ev)
    if conflicts:
        secs.append(section("conflicts", "Conflicts between documents", "problem", _body(conflicts)))
    review = R._review_section(res, ev)
    if review:
        secs.append(section("review", "For the claims officer to review", "warning", _body(review)))
    cov = res["coverage"]["status"]
    secs.append(section("coverage", "Coverage", {"covered": "ok", "not_covered": "problem"}.get(cov, "warning"), _body(R._coverage_section(res, ev))))
    secs.append(section("waiting", "Waiting period", "problem" if any(k["status"] == "violated" for k in res["waiting"]["checks"]) else "ok",
                        _body(R._waiting_section(res, ev))))
    if rec == "likely_not_payable":
        secs.append(section("bill_review", "Bill review", "info", "Not performed. The claim fails an eligibility check, so room-rent, non-medical and document checks do not change the outcome."))
    else:
        limited = bill["room_rule"]["type"] != "at_actuals" and bill["room_ratio"] < 1
        secs.append(section("room", "Room rent working", "warning" if limited else "ok", _body(R._room_section(res, ev))))
        nm = [l for l in lines if l["category"] == "non_medical"]
        if nm and bill["protect_benefit_in_force"]:
            title, status = f"Non-medical items ({len(nm)}), payable under Protect Benefit", "ok"
        else:
            title, status = f"Non-payable items ({len(nm)})", "warning" if nm else "ok"
        secs.append(section("nonpayable", title, status, _body(R._nm_section(res, ev))))
        checklist = res["documents"]["checklist"]
        done = sum(1 for d in checklist if d["status"] == "ok")
        secs.append(section("documents", f"Documents ({done} of {len(checklist)} complete)", "ok" if done == len(checklist) else "warning", _body(R._docs_section(res, ev))))
    secs.append(section("estimate", "Full estimate", "info", "```text\n" + "\n".join(R._estimate_rows(res)) + "\n```"))
    notes = useful_caveats([officer_note] if officer_note else [])
    if notes:
        secs.append(section("notes", "Notes", "info", "\n".join(f"- {n}" for n in notes)))
    secs += _evidence_section(ev)
    return secs


def _evidence_section(ev) -> list[dict]:
    if not ev.chunks:
        return []
    return [section("evidence", f"Evidence ({min(len(ev.chunks), 8)})", "info", _body(ev.section().split("\n")))]


# ---------------------------------------------------------------- deduction explanation
def _blocks(md: str) -> dict[str, list[str]]:
    """The '### Title' blocks of the full deduction answer (this module's own renderer wrote it, so the shape is known)."""
    blocks, cur = {"": []}, ""
    for line in md.split("\n"):
        if line.startswith("## "):
            continue
        if line.startswith("### "):
            cur = line[4:].strip()
            blocks[cur] = []
        elif not line.startswith(">"):
            blocks[cur].append(line)
    return blocks


def _drop_evidence_lines(lines) -> list[str]:
    return [l for l in lines if not l.startswith("**Evidence:**")]


def deduction(res: dict, full_md: str, ev, audience: str = "officer") -> tuple[str, list[dict]]:
    blocks = _blocks(full_md)
    a = res["amounts"]
    out, secs = [], []
    for title, lines in blocks.items():
        body = _tidy(_drop_evidence_lines(lines)).strip()
        if title == "":
            if body:
                out += [body, ""]
        elif title.startswith("Room rent"):
            out += [f"**{title}**", body, ""]
        elif title.startswith("Non-medical items"):
            first = next((l for l in body.split("\n") if l.strip()), "")
            if audience == "customer":   # no clause numbers or policy jargon for the customer
                nm_lines = [l for l in res["bill"]["lines"] if l["category"] == "non_medical"]
                total = sum(l["billed"] for l in nm_lines)
                first = (f"All {len(nm_lines)} non-medical items ({inr(total)}) are payable because your policy includes the Protect Benefit." if res["bill"]["protect_benefit_in_force"]
                         else f"{len(nm_lines)} billed items are non-medical extras that your policy doesn't pay for, so {inr(total)} is not payable.")
            out += [f"**{title}** — {first}", ""]
            table = [l for l in body.split("\n") if l.startswith("|")]
            if table:
                nm = [l for l in res["bill"]["lines"] if l["category"] == "non_medical"]
                secs.append(section("nonmedical", f"Non-medical items ({len(nm)})", "warning", "\n".join(table)))
        elif title.startswith("Amount held"):
            out += [f"**{title}** — {body.replace('is on hold', 'is held') if audience == 'customer' else body}", ""]
        elif title.startswith("Deductible"):
            calc = a.get("calc") or {}
            if audience == "customer":
                mine = ([f"- You pay the first {inr(calc['aggregate_deductible'])} of the claim yourself (your deductible) before the insurer pays."] if calc.get("aggregate_deductible") else []) + \
                       ([f"- You pay {inr(calc['copay'])} as your share of the claim (co-payment)."] if calc.get("copay") else [])
                body = "\n".join(mine) or body
            out += [f"**{title}**", body, ""]
        elif title == "Result":
            out += [f"**Result** — {body}", ""]
    out += _close(audience)
    secs += _evidence_section(ev)
    return _tidy(out), secs


# ---------------------------------------------------------------- documents answer
def documents(res: dict, final: dict, ev, audience: str = "officer") -> tuple[str, list[dict]]:
    checklist = res["documents"]["checklist"]
    done = sum(1 for d in checklist if d["status"] == "ok")
    out = [one_line(final["headline"], 300), ""]
    out.append(f"**{done} of {len(checklist)} documents complete.**" if done != len(checklist) else f"**All {len(checklist)} documents are complete.**")
    for d in checklist:
        name = DOC_SHORT.get(d["id"], d["name"])
        if d["status"] == "incomplete":
            out.append(f"- ⚠️ {name} — {', '.join(d.get('missing_parts', [])) or 'incomplete'} missing")
        elif d["status"] == "missing":
            out.append(f"- ❌ {name} — not submitted")
    if audience == "customer":   # the model writes next steps for an officer ("Request ... from the insured"); the customer gets the plain request built from the checklist
        steps = [s for s in R._next_steps(res, "customer") if s.startswith("Please send")][:3]
    else:
        steps = list(final.get("next_steps") or [])[:3]
    if steps:
        out += ["", "**Next steps**"] + [f"- {s}" for s in steps]
    out += [""] + _close(audience)
    full = []
    for d in checklist:
        name = DOC_SHORT.get(d["id"], d["name"])
        full.append({"ok": f"- ✅ {name}", "incomplete": f"- ⚠️ {name} — {', '.join(d.get('missing_parts', []))} missing", "missing": f"- ❌ {name} — not submitted"}[d["status"]])
    secs = [section("checklist", f"Full checklist ({done} of {len(checklist)} complete)", "ok" if done == len(checklist) else "warning", "\n".join(full))]
    notes = useful_caveats(final.get("caveats"))
    if notes:
        secs.append(section("notes", "Notes", "info", "\n".join(f"- {n}" for n in notes)))
    return _tidy(out), secs + _evidence_section(ev)


# ---------------------------------------------------------------- waiting period answer
def waiting(data: dict, final: dict, ev, audience: str = "officer") -> tuple[str, list[dict]]:
    out = [one_line(final["headline"], 300), ""] + R._waiting_table_lines(data["context"], data["checks"]) + [""] + _close(audience)
    secs = _qa_extras(final, ev, next_steps_in_summary=False)
    return _tidy(out), secs


# ---------------------------------------------------------------- coverage, definition, insufficient information, general
def _top_points(points: list[dict], n: int = 3) -> list[dict]:
    """The first n points, except that a red-flag (problem) point is never left out."""
    chosen = [i for i, p in enumerate(points) if p.get("status") == "problem"][:n]
    for i in range(len(points)):
        if len(chosen) >= n:
            break
        if i not in chosen:
            chosen.append(i)
    return [points[i] for i in sorted(chosen)]


def _qa_extras(final: dict, ev, next_steps_in_summary: bool) -> list[dict]:
    secs = []
    steps = list(final.get("next_steps") or [])
    if steps and not next_steps_in_summary:
        secs.append(section("next_steps", "What to check next", "info", "\n".join(f"- {s}" for s in steps)))
    notes = useful_caveats(final.get("caveats"))
    if notes:
        secs.append(section("notes", "Notes", "info", "\n".join(f"- {n}" for n in notes)))
    return secs + _evidence_section(ev)


def qa(final: dict, ev, audience: str = "officer") -> tuple[str, list[dict]]:
    t = final["answer_type"]
    title = {"insufficient_information": "⚪ Insufficient information", "definition_answer": "📖 What the policy says",
             "coverage_answer": VERDICT.get(final.get("verdict", "depends"), "🔹 Answer")}.get(t)
    out = [f"**{title}**", ""] if title else []
    out += [final["headline"], ""]
    points = final.get("points") or []
    for p in _top_points(points):
        out.append(f"- {STATUS_ICON.get(p['status'], '🔹')} **{p['label']}** — {one_line(p['detail'])}")
    if points:
        out.append("")
    steps = list(final.get("next_steps") or [])
    in_summary = t == "insufficient_information" and steps      # for "the policy says nothing", where to look IS the answer
    if in_summary:
        out += ["**Where to look next**"] + [f"- {s}" for s in steps[:3]] + [""]
    if t != "general_answer":
        out += _close(audience)
    secs = []
    if len(points) > 3 or any(len(p["detail"]) > 160 for p in points):
        lines = []
        for p in points:
            chs = ev.add_keys(p.get("citations"), hint=f"{p['label']} {p['detail']}")
            ref = f" *({R.Evidence.label(chs)})*" if chs else ""
            lines.append(f"{STATUS_ICON.get(p['status'], '🔹')} **{p['label']}** — {p['detail']}{ref}")
        secs.append(section("points", f"All points ({len(points)})", "info", "\n\n".join(lines)))
    return _tidy(out), secs + _qa_extras(final, ev, next_steps_in_summary=bool(in_summary))
