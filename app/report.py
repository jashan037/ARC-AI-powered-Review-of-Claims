"""The claim report the insurer's claims team downloads: built entirely in code, no model call, no chat content.

What goes in it: the facts intake read from the customer's documents, the engine's checks and amounts, where each fact came from, and the
policy provisions those checks used. What never goes in it: anything the model wrote, anything from the chat, and any what-if figure. It is
therefore reproducible: the same documents give the same report, down to the bytes, except the generated timestamp.

  report_id(session)          the first 8 hex of the SHA-256 of the canonical facts: the same claim always has the same id
  report_pdf(session, now)    the PDF as bytes (at most 4 pages, under 300 KB)

A1 A4, palette navy #0B2545, teal #0FA3B1, amber #F2A541. Every result is also a WORD (Pass, Review, Not met, Missing), so the page reads
the same in greyscale or for a colour-blind reader; colour is decoration only. The logo is drawn as vector; the text font is the vendored
DejaVu Sans (app/assets/fonts), which has the rupee sign.

Nothing is stored: the bytes are built per request and handed to the customer's browser.
"""
from __future__ import annotations

import datetime as dt
import functools
import hashlib
import math
import io
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle

from . import intake
from .config import ROOT, settings
from .tools import claims_engine as E
from .tools import facts
from .tools.evidence import resolve
from .tools.fmt import DOC_SHORT, inr
from .tools.totals import derived_totals

NAVY = colors.HexColor("#0B2545")
TEAL = colors.HexColor("#0FA3B1")
AMBER = colors.HexColor("#F2A541")
INK = colors.HexColor("#1F2933")
MUTED = colors.HexColor("#5E6A75")
LINE = colors.HexColor("#C9CFD5")
SHADE = colors.HexColor("#F2F4F6")

FONT_DIR = ROOT / "app" / "assets" / "fonts"
MARGIN = 40
MAX_PAGES = 4
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DISCLAIMER = "Prepared by ARC. AI-assisted. Not a claim decision."
CONSENT = ("Prepared at the customer's request from documents the customer uploaded; share only with the customer's consent.")
STATUS_WORDS = {"satisfied": "Pass", "needs_review": "Review", "violated": "Not met", "not_applicable": "n/a",
                "ok": "Pass", "incomplete": "Incomplete", "missing": "Missing"}


@functools.lru_cache(maxsize=1)
def _fonts() -> tuple[str, str]:
    """Register the vendored font once. Returns (regular, bold) font names."""
    pdfmetrics.registerFont(TTFont("ARC", str(FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("ARC-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily("ARC", normal="ARC", bold="ARC-Bold", italic="ARC", boldItalic="ARC-Bold")
    return "ARC", "ARC-Bold"


def _styles() -> dict:
    reg, bold = _fonts()
    return dict(
        h1=ParagraphStyle("h1", fontName=bold, fontSize=15, leading=18, textColor=NAVY, spaceAfter=2),
        sub=ParagraphStyle("sub", fontName=reg, fontSize=8.5, leading=11, textColor=MUTED, spaceAfter=7),
        h2=ParagraphStyle("h2", fontName=bold, fontSize=9.5, leading=12, textColor=NAVY, spaceBefore=8, spaceAfter=3),
        body=ParagraphStyle("body", fontName=reg, fontSize=8, leading=10.5, textColor=INK, spaceAfter=3),
        small=ParagraphStyle("small", fontName=reg, fontSize=7, leading=9, textColor=MUTED, spaceAfter=2),
        cell=ParagraphStyle("cell", fontName=reg, fontSize=7.2, leading=9, textColor=INK),
        cellb=ParagraphStyle("cellb", fontName=bold, fontSize=7.2, leading=9, textColor=INK),
        cellr=ParagraphStyle("cellr", fontName=reg, fontSize=7.2, leading=9, textColor=INK, alignment=TA_RIGHT),
        cellrb=ParagraphStyle("cellrb", fontName=bold, fontSize=7.2, leading=9, textColor=INK, alignment=TA_RIGHT),
        head=ParagraphStyle("head", fontName=bold, fontSize=7, leading=9, textColor=NAVY),
        bullet=ParagraphStyle("bullet", fontName=reg, fontSize=7.6, leading=9.8, textColor=INK, leftIndent=9, bulletIndent=1, spaceAfter=2),
    )


# ---------------------------------------------------------------- the identity of a report
def canonical_facts(session: dict) -> dict:
    """Everything the report is built from, in a fixed shape: the claim as intake built it and the fields read from each document.
    The report id is the hash of this, so two reports with the same id were built from the same documents."""
    docs = session.get("documents") or {}
    return dict(claim=session.get("claim") or {},
                documents={k: dict(filename=v.get("filename"), digest=v.get("digest"), fields=v.get("fields", {})) for k, v in sorted(docs.items())})


def report_id(session: dict) -> str:
    blob = json.dumps(canonical_facts(session), sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:8]


def filename(claim: dict) -> str:
    safe = "".join(c for c in str(claim.get("claim_id") or "claim") if c.isalnum() or c in "-_")[:40] or "claim"
    return f"ARC-claim-report-{safe}.pdf"


# ---------------------------------------------------------------- the policy clauses the checks used
@functools.lru_cache(maxsize=1)
def _clause_index() -> dict:
    """{chunk_id: (clause as printed, plain title, page)} from the clause file the index was built from. Read once, offline, never from Azure."""
    out = {}
    path = settings.data_dir / "policy_clauses.jsonl"
    if not path.exists():
        return out
    for row in path.read_text(encoding="utf-8").splitlines():
        if not row.strip():
            continue
        c = json.loads(row)
        out[c["chunk_id"]] = (c.get("clause") or c["chunk_id"], c.get("title") or c.get("section_title") or "", c.get("page_start"))
    return out


def provisions(res: dict) -> list[tuple[str, str, str]]:
    """(clause as the policy prints it, plain description, page) for every provision this assessment used, in the order it used them."""
    index, out, seen = _clause_index(), [], set()
    for ref in res.get("evidence_refs", []):
        if ref.startswith("PLAN:"):
            continue
        for cid in resolve(ref) or []:
            if cid in seen:
                continue
            seen.add(cid)
            clause, title, page = index.get(cid, (ref, "", None))
            out.append((ref, title or clause, str(page) if page else "-"))
    return out


# ---------------------------------------------------------------- small drawing helpers
def _logo(canvas, x: float, y: float, h: float = 15) -> None:
    """The ARC mark: a teal arc with an amber dot at its end, drawn as vector so it stays sharp and needs no image file."""
    r = h * 0.62
    canvas.saveState()
    canvas.setLineWidth(h * 0.19)
    canvas.setStrokeColor(TEAL)
    canvas.setLineCap(1)
    path = canvas.beginPath()
    path.arc(x, y - r, x + 2 * r, y + r, startAng=20, extent=140)
    canvas.drawPath(path, stroke=1, fill=0)
    canvas.setFillColor(AMBER)
    ang = math.radians(20)
    canvas.circle(x + r + r * math.cos(ang), y + r * math.sin(ang), h * 0.16, stroke=0, fill=1)
    canvas.setFont("ARC-Bold", h * 0.95)
    canvas.setFillColor(NAVY)
    canvas.drawString(x + 2 * r + 5, y - h * 0.33, "ARC")
    canvas.restoreState()


def _table(data, widths, style_extra=(), header: bool = True) -> Table:
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -2), 0.25, LINE),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
             ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), SHADE), ("LINEBELOW", (0, 0), (-1, 0), 0.6, TEAL)]
    t.setStyle(TableStyle(style + list(style_extra)))
    return t


def _d(iso: str | None) -> str:
    try:
        return dt.date.fromisoformat(str(iso)[:10]).strftime("%d %b %Y").lstrip("0")
    except (TypeError, ValueError):
        return "-"


def _dt_local(iso: str | None) -> str:
    try:
        return dt.datetime.fromisoformat(str(iso)).strftime("%d %b %Y, %H:%M").lstrip("0")
    except (TypeError, ValueError):
        return _d(iso)


# ---------------------------------------------------------------- the sections of the report
CATEGORY_NAMES = {"room": "Room rent", "icu_room": "ICU", "associated": "Associated medical expenses (doctor, theatre, nursing)",
                  "pharmacy_medicine": "Pharmacy - medicines", "consumables": "Consumables", "diagnostics": "Diagnostics",
                  "implant": "Implants", "non_medical": "Non-medical items (Annexure B)", "other": "Other"}
LINE_STATUS = {"payable": "Payable", "reduced": "Reduced", "non_payable": "Not payable", "on_hold": "On hold"}


def _at_a_glance(claim: dict, S: dict) -> list:
    stay = f"{_dt_local(claim.get('admission_datetime'))} to {_dt_local(claim.get('discharge_datetime'))}"
    days = (dt.date.fromisoformat(claim["discharge_datetime"][:10]) - dt.date.fromisoformat(claim["admission_datetime"][:10])).days
    rows = [[Paragraph("Insured person", S["head"]), Paragraph(str(claim.get("insured_name") or "-"), S["cell"]),
             Paragraph("Hospital", S["head"]), Paragraph(str(claim.get("hospital") or "-"), S["cell"])],
            [Paragraph("Procedure", S["head"]), Paragraph(str(claim.get("procedure") or "-"), S["cell"]),
             Paragraph("Diagnosis", S["head"]), Paragraph(f"{claim.get('diagnosis') or '-'}" + (f" ({claim['icd10']})" if claim.get("icd10") else ""), S["cell"])],
            [Paragraph("Admission to discharge", S["head"]), Paragraph(f"{stay} ({days} days)", S["cell"]),
             Paragraph("Amount claimed", S["head"]), Paragraph(inr(claim.get("claimed_amount") or 0), S["cellb"])]]
    return [_table(rows, [95, 165, 75, 180], header=False, style_extra=[("BACKGROUND", (0, 0), (0, -1), SHADE), ("BACKGROUND", (2, 0), (2, -1), SHADE)])]


def _evidence(k: dict, res: dict) -> str:
    """The evidence behind one check, written for a reviewer: dates and numbers, not the sentence the customer reads."""
    if k["code"] == "POLICY_PERIOD" and res.get("policy_in_force"):
        r = res["policy_in_force"]
        return f"Admitted {_d(r['admission_date'])}; policy period {_d(r['period_start'])} to {_d(r['period_end'])}; admission {r['relation']} it."
    if k["code"] == "FILING" and res.get("filing"):
        f = res["filing"]
        return (f"Discharged {_d(f['discharge_date'])}; due by {_d(f['due_by'])} ({f['limit_days']} days, E.1.6); "
                f"sent {f['days_since_discharge']} days after discharge" + (". Late filing may be condoned on merit (E.1.7 Note iv)." if f["late"] else "."))
    if k["code"].startswith("Excl0"):
        w = res["waiting"]["context"]
        return f"{k['required']} required from {_d(w['effective_inception'])}; {w['elapsed_months']} months at admission. {k['detail']}." \
               + (f" Served from {_d(k['eligible_from'])}." if k.get("eligible_from") else "")
    return (k["detail"] or (f"Required: {k['required']}" if k.get("required") else "-")).replace("Your policy", "The policy").replace("your documents", "the documents")


def _readiness(res: dict, docs: dict, S: dict) -> list:
    rows = [[Paragraph("Check", S["head"]), Paragraph("Result", S["head"]), Paragraph("Clause", S["head"]), Paragraph("What the result rests on", S["head"])]]
    for k in res["checks"]:
        clause = k.get("clause") or ("Policy schedule" if k["code"] == "POLICY_PERIOD" else "-")
        rows.append([Paragraph(k["name"], S["cell"]), Paragraph(STATUS_WORDS.get(k["status"], k["status"]), S["cellb"]),
                     Paragraph(clause, S["cell"]), Paragraph(_evidence(k, res), S["cell"])])
    screened = [k for k in res["checks"] if k["code"].startswith("Excl") and not k["code"].startswith("Excl0")] + [k for k in res["checks"] if k["code"] not in
                ("POLICY_PERIOD", "FILING", "PATIENT", "HOSP24") and not k["code"].startswith("Excl")]
    rows.append([Paragraph("Exclusions screened", S["cell"]),
                 Paragraph("Review" if any(k["status"] in ("violated", "needs_review") for k in screened) else "Pass", S["cellb"]),
                 Paragraph("C.2, C.3", S["cell"]),
                 Paragraph("; ".join(f"{k['name']}: {k['detail']}" for k in screened)
                           or "No exclusion was triggered by what the documents say; the schedule lists no permanent exclusion. "
                              "ARC screens the exclusions it models (C.2.l, and any review flag on the claim), not IRDAI's longer list.", S["cell"])])
    counts = intake.document_counts(docs)
    rows.append([Paragraph("Documents required by the policy", S["cell"]),
                 Paragraph("Pass" if not counts["outstanding"] else "Missing", S["cellb"]),
                 Paragraph("E.1.7", S["cell"]),
                 Paragraph(f"{counts['received']} of {counts['expected']} received" + (". Outstanding: " + "; ".join(counts["outstanding"]) if counts["outstanding"] else ""), S["cell"])])
    return [_table(rows, [130, 40, 52, 293])]


def _money(res: dict, claim: dict, S: dict) -> list:
    a, t = res["amounts"], derived_totals(res)
    ded = a["deductions"]
    rows = [[Paragraph("Money", S["head"]), Paragraph("Amount", S["head"]), Paragraph("Why", S["head"])]]

    def row(label, value, why, bold=False):
        rows.append([Paragraph(label, S["cellb"] if bold else S["cell"]), Paragraph(inr(value), S["cellrb"] if bold else S["cellr"]), Paragraph(why, S["cell"])])

    row("Hospital bill as billed", a["gross_billed"], "Total of the itemised final bill")
    if ded["room"]:
        row("Less room rent above the plan limit", ded["room"], f"Billed {inr(t.get('billed_room_rate_per_day', 0))} a day against the plan's {inr(t.get('room_limit_per_day', 0))} a day")
    if ded["associated"]:
        row("Less associated medical expenses", ded["associated"], "Reduced in the same proportion as the room rent (B.1.1.1 Note iii)")
    if ded["non_medical"]:
        row("Less non-medical items", ded["non_medical"], f"{t['non_medical_count']} Annexure B items; Protect Benefit not in force")
    calc = a.get("calc") or {}
    if calc.get("aggregate_deductible"):
        row("Less aggregate deductible", calc["aggregate_deductible"], "Applied before the sum insured (D.1.19)")
    if calc.get("copay"):
        row("Less co-payment", calc["copay"], f"{claim.get('copay_percent', 0):g}% on the schedule")
    row("Estimated payment once documents complete", a["estimated_payable_if_docs_supplied"], "Computed by ARC's code, not a decision", bold=True)
    row("Of which counted now", a["payable_confirmed_now"], "Excludes the lines that wait for a document", bold=True)
    if a["held_pending"]:
        row("On hold until a document arrives", a["held_pending"], "Medicines: the prescription is not with the pharmacy bill")
    if res["recommendation"] == "likely_not_payable":
        rows.append([Paragraph("Nothing appears payable", S["cellb"]), Paragraph("-", S["cellr"]), Paragraph(res["coverage"]["detail"], S["cell"])])
    return [_table(rows, [175, 70, 270], style_extra=[("LINEABOVE", (0, len(rows) - 2), (-1, len(rows) - 2), 0.6, NAVY)])]


def _judgement(res: dict, S: dict) -> list:
    """What a person has to decide, kept apart from the facts above."""
    out = []
    for k in res["checks"]:
        if k["status"] == "needs_review":
            out.append(f"{k['name']}: {k['detail']}")
        if k["status"] == "violated":
            out.append(f"{k['name']}: {k['detail']} A person decides whether a renewal changes that.")
    for i in res["inconsistencies"]:
        out.append(f"Documents disagree - {i['note']}: discharge summary '{i['discharge_summary']}' against the bill '{i['bill']}'.")
    held = [l for l in res["bill"]["lines"] if l["status"] == "on_hold"]
    if held:
        out.append("On hold, not refused: " + "; ".join(f"{l['description']} ({inr(l['billed'])})" for l in held) + ".")
    nm = [l for l in res["bill"]["lines"] if l["category"] == "non_medical" and l["status"] == "non_payable"]
    if nm:
        out.append(f"{len(nm)} bill lines were read as Annexure B non-medical items from their wording; confirm the borderline ones.")
    if not out:
        out.append("Nothing on this claim needed a judgement call from the checks ARC runs.")
    return [Paragraph(f"• {x}", S["bullet"]) for x in out]


def _next_steps(res: dict, S: dict) -> list:
    steps = []
    d = res["documents"]
    for x in d["missing"] + d["incomplete"]:
        parts = ", ".join(x.get("missing_parts", []) or [])
        steps.append(f"Request the {DOC_SHORT.get(x['id'], x['name']).lower()}" + (f" ({parts})" if parts else "") + f" - {x['clause']}.")
    if res.get("filing") and res["filing"]["late"]:
        steps.append(f"Refer the {res['filing']['days_since_discharge']}-day delay for a decision on merit (E.1.7 Note iv): the documents were due by {_d(res['filing']['due_by'])}.")
    if res.get("policy_in_force") and not res["policy_in_force"]["in_force"]:
        steps.append("Verify whether a renewal covered the admission date, from the renewal schedule or proof of premium payment.")
    if res["inconsistencies"]:
        steps.append("Verify the conflicting fields against the hospital's records.")
    if res["bill"]["room_ratio"] < 1:
        steps.append("Verify the room category and the hospital's differential-billing tariff before confirming the proportionate deduction.")
    for k in res["checks"]:
        if k["code"] == "PATIENT" and k["status"] == "needs_review":
            steps.append("Verify who the patient is: the name on the papers is not the insured person on the schedule.")
    steps.append("Decide the claim. ARC neither approves nor rejects; the figures above are estimates from the documents.")
    return [Paragraph(f"{i}. {s}", S["bullet"]) for i, s in enumerate(steps, 1)]


def _worked_out(res: dict, S: dict) -> list:
    bill = res["bill"]
    rows = [[Paragraph("Bill line", S["head"]), Paragraph("Billed", S["head"]), Paragraph("Payable", S["head"]),
             Paragraph("Result", S["head"]), Paragraph("Why, and the provision", S["head"])]]
    style, order = [], ["room", "icu_room", "associated", "pharmacy_medicine", "consumables", "diagnostics", "implant", "non_medical", "other"]
    for cat in order:
        lines = [l for l in bill["lines"] if l["category"] == cat]
        if not lines:
            continue
        rows.append([Paragraph(CATEGORY_NAMES[cat], S["cellb"]), Paragraph("", S["cell"]), Paragraph("", S["cell"]), Paragraph("", S["cell"]), Paragraph("", S["cell"])])
        style.append(("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), SHADE))
        for l in lines:
            no = f" (Annexure B #{l['annexure_b_no']})" if l.get("annexure_b_no") else ""
            rows.append([Paragraph(l["description"] + no, S["cell"]), Paragraph(inr(l["billed"]), S["cellr"]), Paragraph(inr(l["payable"]), S["cellr"]),
                         Paragraph(LINE_STATUS.get(l["status"], l["status"]), S["cell"]),
                         Paragraph((l["reason"] or "Payable as billed") + (" [" + "; ".join(l["evidence"]) + "]" if l["evidence"] else ""), S["cell"])])
    a = res["amounts"]
    rows.append([Paragraph("Total", S["cellb"]), Paragraph(inr(a["gross_billed"]), S["cellrb"]), Paragraph(inr(a["admissible_total"]), S["cellrb"]),
                 Paragraph("", S["cell"]), Paragraph("Admissible before the deductible and the sum insured cap", S["cell"])])
    style.append(("LINEABOVE", (0, len(rows) - 1), (-1, len(rows) - 1), 0.6, NAVY))
    out = [_table(rows, [166, 55, 55, 41, 198], style_extra=style)]
    if bill["room_ratio"] < 1:
        out.append(Paragraph(
            f"Room-rent proportion: plan limit {inr(bill['room_limit_per_day'])} a day / billed {inr(bill['room_rate_per_day'])} a day = "
            f"{bill['room_ratio'] * 100:g}%, applied to the room charges and to the associated medical expenses of Def. 5 "
            f"(consultation, operation theatre, nursing, anaesthesia, surgeon's fee, blood, oxygen). Not applied to pharmacy, consumables, "
            f"diagnostics or implants.", S["small"]))
    return out


def _register(session: dict, res: dict, S: dict) -> list:
    docs = session.get("documents") or {}
    cl = {c["id"]: c for c in res["documents"]["checklist"]}
    rows = [[Paragraph("Document", S["head"]), Paragraph("File", S["head"]), Paragraph("Key fields read", S["head"]), Paragraph("State", S["head"])]]
    for d in intake.EXPECTED:
        got = docs.get(d["id"])
        state = STATUS_WORDS.get((cl.get(d["id"]) or {}).get("status", "ok" if got else "missing"), "-")
        if d["id"] == "pharmacy_bills_prescription" and got and not intake._prescription_present(docs):
            state = "Incomplete"
        fields = ""
        if got:
            keys = [k for k in ("policy_number", "insured_name", "patient_name", "plan", "first_inception", "policy_period", "base_si", "bonus",
                                "admission", "discharge", "total", "room_rate_per_day", "room_days", "claimed_amount", "diagnosis", "procedure",
                                "dob", "age", "prescription_attached", "cancelled_cheque", "collected_on", "consulted_on", "reason") if k in got["fields"]]
            fields = ", ".join(f"{k.replace('_', ' ')}" for k in keys[:8]) or f"{len(got['fields'])} fields"
        rows.append([Paragraph(d["label"], S["cell"]), Paragraph(got["filename"] if got else "-", S["cell"]),
                     Paragraph(fields or "-", S["cell"]), Paragraph(state, S["cellb"])])
    out = [_table(rows, [124, 104, 235, 52])]
    if res["inconsistencies"]:
        out.append(Paragraph("Conflicts between documents: " + "; ".join(
            f"{i['field'].replace('_', ' ')} - discharge summary '{i['discharge_summary']}', bill '{i['bill']}'" for i in res["inconsistencies"]) + ".", S["small"]))
    else:
        out.append(Paragraph("No conflict was found between the patient name, the admission date and the discharge date on the discharge summary and the bill.", S["small"]))
    return out


FACT_SOURCES = [
    ("Policy number", "policy_schedule", "policy_number"), ("Plan", "policy_schedule", "plan"), ("Insured person", "policy_schedule", "insured_name"),
    ("First policy inception", "policy_schedule", "first_inception"), ("Current policy period", "policy_schedule", "policy_period"),
    ("Base sum insured", "policy_schedule", "base_si"), ("Cumulative bonus", "policy_schedule", "bonus"),
    ("Room rent limit", "policy_schedule", "room_rent_limit_text"), ("Aggregate deductible", "policy_schedule", "aggregate_deductible_text"),
    ("Co-payment", "policy_schedule", "copay_text"), ("Protect Benefit", "policy_schedule", "protect_benefit_text"),
    ("Date of birth", "photo_id_age_proof", "dob"), ("Age", "photo_id_age_proof", "age"),
    ("Hospital", "claim_form", "hospital"), ("Admission", "claim_form", "admission"), ("Discharge", "claim_form", "discharge"),
    ("Diagnosis", "claim_form", "diagnosis"), ("Procedure", "claim_form", "procedure"), ("Amount claimed", "claim_form", "claimed_amount"),
    ("Accident or illness", "claim_form", "is_accident"), ("Length of stay", "discharge_summary", "length_of_stay"),
    ("Room category", "discharge_summary", "room_category"), ("Room rate per day", "final_bill_receipts", "room_rate_per_day"),
    ("Hospital bill total", "final_bill_receipts", "total"), ("Days of room charges", "final_bill_receipts", "room_days"),
    ("Pharmacy total", "pharmacy_bills_prescription", "total"), ("Prescription with the pharmacy bill", "pharmacy_bills_prescription", "prescription_attached"),
]


def _where_from(session: dict, S: dict) -> list:
    docs = session.get("documents") or {}
    rows = [[Paragraph("Fact", S["head"]), Paragraph("Value as read", S["head"]), Paragraph("Source file", S["head"]), Paragraph("Page", S["head"])]]
    for label, kind, key in FACT_SOURCES:
        d = docs.get(kind)
        if not d or key not in d.get("fields", {}):
            continue
        v = d["fields"][key]
        if isinstance(v, list):
            v = " to ".join(_d(x) for x in v)
        elif isinstance(v, bool):
            v = "Yes" if v else "No"
        elif key in ("first_inception", "dob", "admission", "discharge") or (isinstance(v, str) and len(v) >= 10 and v[4] == "-"):
            v = _dt_local(v) if isinstance(v, str) and "T" in v else _d(v)
        elif isinstance(v, int) and abs(v) >= 1000:
            v = inr(v)
        if key == "length_of_stay":
            v = f"{v} days"
        rows.append([Paragraph(label, S["cell"]), Paragraph(str(v), S["cell"]), Paragraph(d["filename"], S["cell"]),
                     Paragraph(str((d.get("field_pages") or {}).get(key, 1)), S["cellr"])])
    return [_table(rows, [130, 175, 170, 30])]


def _provisions(res: dict, S: dict) -> list:
    rows = [[Paragraph("Clause", S["head"]), Paragraph("What it says, in short", S["head"]), Paragraph("Page", S["head"])]]
    for ref, title, page in provisions(res):
        rows.append([Paragraph(ref, S["cell"]), Paragraph(title, S["cell"]), Paragraph(page, S["cellr"])])
    return [_table(rows, [95, 380, 30])]


LIMITS = [
    "Every amount, date and rule result in this report was computed by ARC's own code from the documents named above. No language model took part in producing it.",
    "The AI part of ARC is used only to explain the claim to the customer in chat. Nothing from that conversation is in this report, and no what-if figure is either.",
    f"Policy wording: HDFC ERGO my:Optima Secure, UIN {settings.default_uin}. Only this version is loaded.",
    "Not modelled: IRDAI's longer list of non-payable items, sub-limits, the restore and cumulative-bonus mechanics beyond the amount on the schedule, "
    "cashless pre-authorisation, network-hospital lookup, other claims in the policy year, and the 2026 wording.",
    "Intake reads text PDFs in the layout of the synthetic demo set. A scan, a photo or an unfamiliar layout is reported back to the customer, never guessed at.",
    "ARC never approves or rejects a claim: an estimate is not a decision.",
]


def _basis(S: dict) -> list:
    return [Paragraph(f"• {x}", S["bullet"]) for x in LIMITS] + [Spacer(1, 4), Paragraph(CONSENT, S["small"])]


# ---------------------------------------------------------------- the document
def _page_furniture(rid: str, generated: str):
    def draw(canvas, doc):
        canvas.saveState()
        if doc.page == 1:
            _logo(canvas, MARGIN, A4[1] - MARGIN - 6)
            canvas.setFont("ARC", 7)
            canvas.setFillColor(MUTED)
            canvas.drawRightString(A4[0] - MARGIN, A4[1] - MARGIN - 2, f"Report {rid}")
            canvas.drawRightString(A4[0] - MARGIN, A4[1] - MARGIN - 11, f"Generated {generated}")
        canvas.setFont("ARC", 6.5)
        canvas.setFillColor(MUTED)
        canvas.setStrokeColor(LINE)
        canvas.line(MARGIN, 44, A4[0] - MARGIN, 44)
        canvas.drawString(MARGIN, 34, f"Report {rid} - {DISCLAIMER}")
        canvas.drawRightString(A4[0] - MARGIN, 34, f"Page {doc.page} of {getattr(doc, 'total_pages', doc.page)}")
        canvas.restoreState()
    return draw


class _Doc(BaseDocTemplate):
    """Plain document template; the report is built twice so that "page x of y" can name y (see report_pdf)."""


def _story(session: dict, res: dict, S: dict) -> list:
    claim = session["claim"]
    ident = [[Paragraph("Claim reference", S["head"]), Paragraph(str(claim.get("claim_id") or "-"), S["cell"]),
              Paragraph("Policy number", S["head"]), Paragraph(str(claim.get("policy_number") or "-"), S["cell"])],
             [Paragraph("Plan", S["head"]), Paragraph(str(claim.get("plan") or "-"), S["cell"]),
              Paragraph("Policy wording (UIN)", S["head"]), Paragraph(str(claim.get("policy_uin") or settings.default_uin), S["cell"])]]
    story = [Paragraph("Claim Review Report", S["h1"]), Paragraph(DISCLAIMER, S["sub"]),
             _table(ident, [95, 165, 95, 160], header=False, style_extra=[("BACKGROUND", (0, 0), (0, -1), SHADE), ("BACKGROUND", (2, 0), (2, -1), SHADE)]),
             Paragraph("At a glance", S["h2"])] + _at_a_glance(claim, S)
    story += [Paragraph("Readiness checks", S["h2"]),
              Paragraph("Results are words, not colours: Pass, Review, Not met, Missing, n/a.", S["small"])] + _readiness(res, session.get("documents") or {}, S)
    story += [Paragraph("Money summary", S["h2"])] + _money(res, claim, S)
    story += [Paragraph("Needs a person's judgement", S["h2"])] + _judgement(res, S)
    story += [Paragraph("Suggested next steps for the reviewer (suggestions, not decisions)", S["h2"])] + _next_steps(res, S)
    story += [PageBreak()]                 # page 1 is the whole summary and nothing else: it is what a reviewer reads first
    story += [Paragraph("How the amount was worked out", S["h2"])] + _worked_out(res, S)
    story += [Paragraph("Document register", S["h2"])] + _register(session, res, S)
    story += [Paragraph("Where each key fact came from", S["h2"])] + _where_from(session, S)
    story += [Paragraph("Policy provisions applied", S["h2"])] + _provisions(res, S)
    story += [Paragraph("Basis and limits", S["h2"])] + _basis(S)
    return story


def report_pdf(session: dict, now: dt.datetime | None = None) -> bytes:
    """The report as bytes. `now` is only the timestamp printed on it: everything else comes from the documents, so two runs are identical."""
    claim = session.get("claim")
    if not claim:
        raise ValueError("no claim in this session")
    res = E.assess(claim)
    facts.build(session)          # the same facts the chat answers from, so a change there cannot go unnoticed here
    S = _styles()
    rid = report_id(session)
    generated = (now or dt.datetime.now(IST)).astimezone(IST).strftime("%d %b %Y, %H:%M IST").lstrip("0")
    buf = io.BytesIO()
    doc = _Doc(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN + 22, bottomMargin=56,
               title=f"ARC claim review report {claim.get('claim_id', '')}".strip(), author="ARC (AI-powered Review of Claims)",
               subject=DISCLAIMER, creator="ARC", invariant=1)
    frame = Frame(MARGIN, 56, A4[0] - 2 * MARGIN, A4[1] - MARGIN - 22 - 56, id="body", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    story = _story(session, res, S)
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=_page_furniture(rid, generated))])
    doc.build(list(story))
    pages = doc.page
    if pages > 1:                 # a second pass now that the page count is known, so every footer can say "of n"
        buf = io.BytesIO()
        doc = _Doc(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN + 22, bottomMargin=56,
                   title=f"ARC claim review report {claim.get('claim_id', '')}".strip(), author="ARC (AI-powered Review of Claims)",
                   subject=DISCLAIMER, creator="ARC", invariant=1)
        doc.total_pages = pages
        frame = Frame(MARGIN, 56, A4[0] - 2 * MARGIN, A4[1] - MARGIN - 22 - 56, id="body", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=_page_furniture(rid, generated))])
        doc.build(_story(session, res, S))
    return buf.getvalue()
