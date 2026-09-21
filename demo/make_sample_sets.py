#!/usr/bin/env python3
"""Regenerate the ten synthetic demo documents from parameters, in three sets.

    python demo/make_sample_sets.py            # write demo/samples/{on_time,late_filing,expired}
    python demo/make_sample_sets.py --check    # only check that the regenerated 'late_filing' set still says what the original PDFs said

Everything is fictional. Only the DATES and the AGE differ between the sets: the names, the amounts, the bill lines, the wording and the
layout are the same in all three, so `app/intake.py` reads them all the same way.

  on_time       policy 15/03/2026 to 14/03/2027, admitted 10/09/2026, filed inside the 30 days (with ARC_TODAY=2026-09-21)
  late_filing   policy 15/03/2025 to 14/03/2026, admitted 10/09/2025, filed long after the 30 days -> a review flag
  expired       policy 15/03/2025 to 14/03/2026, admitted 20/04/2026, after the policy period -> likely not covered

All three leave the doctor's prescription out on purpose, so the demo shows a real gap. Every page keeps the SYNTHETIC DEMO DOCUMENT footer.

`--check` regenerates `late_filing` (the set the original hand-made PDFs in the repository contained) and compares the text layer of each
file with the original, line by line after stripping each line, which is exactly what intake reads. It is the proof that the layout and the
wording did not drift when the documents became parameters.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo" / "samples"
SETS = ("on_time", "late_filing", "expired")

BANNER_MEDICAL = "SYNTHETIC DEMO DOCUMENT - fictional person, hospital and numbers. Not a real policy or medical record."
BANNER_PLAIN = "SYNTHETIC DEMO DOCUMENT - fictional person, hospital and numbers. Not a real document."

# ---------------------------------------------------------------- the parameters of each set (nothing else changes)
ANCHORS = {
    "on_time":     dict(period=("2026-03-15", "2027-03-14"), admission="2026-09-10T14:30", discharge="2026-09-14T11:00", age=29),
    "late_filing": dict(period=("2025-03-15", "2026-03-14"), admission="2025-09-10T14:30", discharge="2025-09-14T11:00", age=28),
    "expired":     dict(period=("2025-03-15", "2026-03-14"), admission="2026-04-20T14:30", discharge="2026-04-24T11:00", age=28),
}
FIRST_INCEPTION = "2024-03-15"
DATE_OF_BIRTH = "1997-06-12"
POLICY_NUMBER = "SYN-2805-0000-0001"
INSURED = "Rohan Verma"
HOSPITAL = "Riverside Multispeciality Hospital (DEMO)"


def _d(iso: str) -> str:
    return dt.date.fromisoformat(iso[:10]).strftime("%d/%m/%Y")


def params(name: str) -> dict:
    """Every date a document prints, worked out from the set's anchors."""
    a = ANCHORS[name]
    adm, dis = dt.datetime.fromisoformat(a["admission"]), dt.datetime.fromisoformat(a["discharge"])
    return dict(
        set=name, age=a["age"], dob=_d(DATE_OF_BIRTH), first_inception=_d(FIRST_INCEPTION),
        period_start=_d(a["period"][0]), period_end=_d(a["period"][1]),
        adm_date=_d(a["admission"]), adm_time=adm.strftime("%H:%M"), dis_date=_d(a["discharge"]), dis_time=dis.strftime("%H:%M"),
        procedure_date=_d((adm.date() + dt.timedelta(days=1)).isoformat()),
        consult_time="12:40", lab_time="15:10", days=(dis.date() - adm.date()).days,
    )


# ---------------------------------------------------------------- page furniture
MARGIN = 46
TITLE = ParagraphStyle("title", fontName="Helvetica", fontSize=17, leading=21, textColor=colors.HexColor("#1A1A1A"), spaceAfter=8)
NOTE = ParagraphStyle("note", fontName="Helvetica", fontSize=7.5, leading=10, textColor=colors.HexColor("#666666"), spaceAfter=10)
SUB = ParagraphStyle("sub", fontName="Helvetica", fontSize=10.5, leading=13, textColor=colors.HexColor("#1A1A1A"), spaceBefore=12, spaceAfter=5)
CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.HexColor("#1A1A1A"))
CELL_R = ParagraphStyle("cellr", parent=CELL, alignment=TA_RIGHT)
BODY = ParagraphStyle("body", parent=CELL, spaceAfter=4)
LABEL_W, VALUE_W = 163, 350
GRID = colors.HexColor("#AAAAAA")
SHADE = colors.HexColor("#F2F2F2")
_TABLE = [("GRID", (0, 0), (-1, -1), 0.4, GRID), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
          ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
# the itemised bill has 30 rows and still fits on one page
_TIGHT = [x for x in _TABLE if x[0] not in ("TOPPADDING", "BOTTOMPADDING")] + [("TOPPADDING", (0, 0), (-1, -1), 2.75), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.75)]


def _p(text: str, style=CELL) -> Paragraph:
    return Paragraph(str(text).replace("&", "&amp;").replace("\n", "<br/>"), style)


def rows(pairs, tight: bool = False) -> Table:
    """The label/value table every document uses. The line breaks inside a value are written by hand, so the layout never drifts."""
    t = Table([[_p(k), _p(v)] for k, v in pairs], colWidths=[LABEL_W, VALUE_W])
    t.setStyle(TableStyle((_TIGHT if tight else _TABLE) + [("BACKGROUND", (0, 0), (0, -1), SHADE)]))
    return t


def footer(banner: str):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(colors.HexColor("#CC0033"))
        canvas.drawString(MARGIN, 36, banner)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawRightString(A4[0] - MARGIN, 36, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()
    return draw


def write(path: Path, title: str, story: list, banner: str, subject: str) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=44,
                            title=title, author="(anonymous)", subject=subject, creator="ARC demo document generator")
    doc.build([_p(title, TITLE)] + story, onFirstPage=footer(banner), onLaterPages=footer(banner))


# ---------------------------------------------------------------- the ten documents
def policy_schedule(p: dict) -> tuple:
    story = [
        _p("Issued under UIN HDFHLIP25041V062425 (fictional schedule for demo purposes).", NOTE),
        rows([("Policy number", POLICY_NUMBER), ("Plan", "my:Optima Secure - Optima Lite (Individual)"),
              ("Policyholder / Insured person", f"{INSURED} (synthetic), Age {p['age']}"),
              ("First policy inception date", p["first_inception"]), ("Current policy period", f"{p['period_start']} to {p['period_end']}"),
              ("Premium tier", "Tier 2 (Rest of India)"), ("Nominee", "Not applicable for demo")]),
        _p("Schedule of coverage", SUB),
        rows([("Base sum insured", "Rs. 5,00,000"), ("Cumulative bonus (10%,\ncredited on renewal)", "Rs. 50,000"),
              ("Room rent limit", "Up to 1% of base sum insured per day = Rs. 5,000 per day"),
              ("ICU limit", "Up to 2% of base sum insured per day = Rs. 10,000 per day"),
              ("Pre-hospitalization", "30 days"), ("Post-hospitalization", "60 days"),
              ("Aggregate deductible", "Nil"), ("Co-payment", "Nil"),
              ("Protect Benefit (non-medical\nexpenses)", "Not opted"), ("Plus Benefit", "Not opted"),
              ("Automatic restore benefit", "Unlimited times"), ("Emergency air ambulance", "Up to Rs. 5,00,000"),
              ("Daily cash for shared room", "Rs. 800 per day, max Rs. 4,800"), ("Permanent exclusions", "None"),
              ("Pre-existing diseases declared", "None"), ("PED waiting period", "36 months from first policy inception")]),
        Spacer(1, 10),
        _p("This schedule is to be read with the my:Optima Secure policy wording (sections A to E and Annexures A to C).", NOTE),
    ]
    return "policy_schedule.pdf", "Policy Schedule - my:Optima Secure (Optima Lite)", story, BANNER_MEDICAL


def claim_form(p: dict) -> tuple:
    story = [
        _p("Section A - Primary insured", SUB),
        rows([("Policy number", POLICY_NUMBER), ("Name", INSURED), ("Address", "Demo Address, Demo City, India"),
              ("Phone / email", "0000000000 / demo@example.com")]),
        _p("Section B - Insurance history", SUB),
        rows([("Other health insurance", "No"), ("Previous claims in this policy\nyear", "None")]),
        _p("Section C - Hospitalization", SUB),
        rows([("Hospital", f"{HOSPITAL} - network hospital"), ("Admitted on", f"{p['adm_date']} {p['adm_time']} (emergency)"),
              ("Discharged on", f"{p['dis_date']} {p['dis_time']}"), ("Reason for admission", "Illness (not an accident)"),
              ("Diagnosis / ICD-10", "Acute appendicitis / K35.8"), ("Procedure", f"Laparoscopic appendectomy, {p['procedure_date']}"),
              ("Pre-existing condition", "No")]),
        _p("Section D - Claim details", SUB),
        rows([("Hospitalization expenses\nclaimed", "Rs. 1,84,500"), ("Pre-hospitalization expenses", "Nil"),
              ("Post-hospitalization expenses", "Nil"), ("Total claimed", "Rs. 1,84,500"), ("Bank account for NEFT", "Masked in demo (XXXXXXXX)")]),
        _p("Section E - Documents enclosed", SUB),
        rows([("Enclosed", "Claim form, photo ID/age proof, discharge summary, final hospital bill with\nreceipts, lab and imaging reports, pharmacy bills, KYC, NEFT form"),
              ("Not enclosed", "Prescription for pharmacy bills")]),
        Spacer(1, 8),
        _p("Declaration: the information above is true to the best of my knowledge. Signature: (synthetic)", NOTE),
    ]
    return "claim_form.pdf", "Health Claim Form - Part A (to be filled by the insured)", story, BANNER_MEDICAL


def discharge_summary(p: dict) -> tuple:
    blocks = [
        ("Diagnosis", "Acute appendicitis (ICD-10 K35.8)."),
        ("Presenting complaints", "Right lower abdominal pain for 18 hours with nausea and low-grade fever. Emergency admission through casualty."),
        ("Investigations", "Raised white cell count and CRP. Ultrasound and CT abdomen consistent with acute appendicitis without perforation."),
        ("Procedure", f"Laparoscopic appendectomy under general anaesthesia on {p['procedure_date']} by Dr. A. Demo Kumar; anaesthetist Dr. B.\n"
                      "Demo Rao. Uneventful surgery. Appendix sent for histopathology."),
        ("Hospital course", "Post-operative recovery uneventful. IV antibiotics and analgesia for 48 hours, oral diet resumed on day 2, mobilised on\nday 2."),
        ("Condition at discharge", "Stable, afebrile, wound clean and dry."),
        ("Advice", "Oral antibiotics and analgesics for 5 days, wound care, review in surgical OPD after 7 days with histopathology report.\n"
                   "Avoid heavy lifting for 4 weeks."),
    ]
    story = [rows([("Hospital", HOSPITAL), ("Patient", f"{INSURED}, {p['age']} years, male"), ("UHID / IP number", "DEMO-UHID-0001 / IP-0001"),
                   ("Date of admission", f"{p['adm_date']} {p['adm_time']}"), ("Date of discharge", f"{p['dis_date']} {p['dis_time']}"),
                   ("Length of stay", f"{p['days']} days"), ("Room category", "Deluxe room (Rs. 8,000 per day)"),
                   ("Treating doctor", "Dr. A. Demo Kumar, MS (General Surgery), Reg. DEMO-0001")])]
    for head, body in blocks:
        story.append(KeepTogether([_p(head, SUB), _p(body, BODY)]))
    return "discharge_summary.pdf", "Discharge Summary", story, BANNER_MEDICAL


BILL_LINES = [
    ("ROOM AND NURSING", None),
    (1, "Room charges - deluxe room", 4, "8,000", "32,000"),
    (2, "Nursing charges", 4, "1,000", "4,000"),
    ("PROFESSIONAL FEES AND OT", None),
    (3, "Surgeon fee - laparoscopic appendectomy", 1, "55,000", "55,000"),
    (4, "Anaesthetist fee", 1, "12,000", "12,000"),
    (5, "Operation theatre charges", 1, "24,000", "24,000"),
    (6, "Consultant visit charges", 1, "6,000", "6,000"),
    ("PHARMACY", None),
    (7, "Medicines and drugs", 1, "20,500", "20,500"),
    (8, "IV fluids and surgical consumables (sutures, trocar)", 1, "9,500", "9,500"),
    ("INVESTIGATIONS", None),
    (9, "Laboratory tests (CBC, CRP, LFT)", 1, "4,200", "4,200"),
    (10, "Ultrasound abdomen", 1, "1,800", "1,800"),
    (11, "CT abdomen", 1, "3,000", "3,000"),
    ("MISCELLANEOUS", None),
    (12, "Surgical gloves", 1, "2,400", "2,400"),
    (13, "Face masks", 1, "800", "800"),
    (14, "ECG electrodes", 1, "600", "600"),
    (15, "Abdominal binder", 1, "1,200", "1,200"),
    (16, "Kidney tray", 1, "300", "300"),
    (17, "Urine jug", 1, "250", "250"),
    (18, "Nebulisation kit", 1, "900", "900"),
    (19, "Thermometer", 1, "150", "150"),
    (20, "Attendant food charges", 1, "2,800", "2,800"),
    (21, "Service charges", 1, "2,000", "2,000"),
    (22, "Television charges", 1, "700", "700"),
    (23, "Telephone charges", 1, "400", "400"),
]


def hospital_bill(p: dict) -> tuple:
    widths = [36, 290, 45, 71, 71]
    data = [[_p("S.No"), _p("Description"), _p("Qty", CELL_R), _p("Rate (Rs.)", CELL_R), _p("Amount (Rs.)", CELL_R)]]
    style = list(_TIGHT) + [("BACKGROUND", (0, 0), (-1, 0), SHADE)]
    for i, line in enumerate(BILL_LINES, start=1):
        if line[1] is None:
            data.append([_p(""), _p(line[0]), _p(""), _p(""), _p("")])
            style.append(("BACKGROUND", (0, i), (1, i), SHADE))
        else:
            no, desc, qty, rate, amount = line
            data.append([_p(no), _p(desc), _p(qty, CELL_R), _p(rate, CELL_R), _p(amount, CELL_R)])
    data.append([_p(""), _p("TOTAL BILL"), _p(""), _p(""), _p("1,84,500", CELL_R)])
    style.append(("BACKGROUND", (1, len(data) - 1), (1, len(data) - 1), SHADE))
    bill = Table(data, colWidths=widths, repeatRows=1)
    bill.setStyle(TableStyle(style))
    story = [
        rows([("Hospital", HOSPITAL), ("Bill number", "DEMO-BILL-0001"), ("Patient", INSURED), ("Policy number", POLICY_NUMBER),
              ("Admission / discharge", f"{p['adm_date']} to {p['dis_date']}"), ("Room category", "Deluxe room, Rs. 8,000 per day")], tight=True),
        Spacer(1, 6), bill, Spacer(1, 6),
        rows([("Advance deposit (receipt\nDEMO-RCPT-01)", "Rs. 50,000"), ("Final payment (receipt\nDEMO-RCPT-02)", "Rs. 1,34,500"),
              ("Total paid", "Rs. 1,84,500"), ("Balance", "Nil")], tight=True),
    ]
    return "hospital_bill.pdf", "Final Hospital Bill (Itemised)", story, BANNER_MEDICAL


PHARMACY = [(1, "Ceftriaxone injection (6 doses)", "4,800"), (2, "Metronidazole IV (9 doses)", "2,700"), (3, "Pantoprazole injection (6 doses)", "1,800"),
            (4, "Paracetamol IV", "1,500"), (5, "Ondansetron injection", "900"), (6, "Tramadol injection", "1,600"),
            (7, "Enoxaparin injection (2 doses)", "3,400"), (8, "Discharge medication: oral antibiotics and analgesics, 5 days", "3,800")]


def pharmacy_bills(p: dict) -> tuple:
    data = [[_p("S.No"), _p("Medicine"), _p("Amount (Rs.)", CELL_R)]]
    for no, item, amount in PHARMACY:
        data.append([_p(no), _p(item), _p(amount, CELL_R)])
    data.append([_p(""), _p("TOTAL"), _p("20,500", CELL_R)])
    t = Table(data, colWidths=[36, 406, 71])
    t.setStyle(TableStyle(list(_TABLE) + [("BACKGROUND", (0, 0), (-1, 0), SHADE), ("BACKGROUND", (1, len(data) - 1), (1, len(data) - 1), SHADE)]))
    story = [
        rows([("Pharmacy", f"{HOSPITAL} - in-patient pharmacy"), ("Bill number", "DEMO-PH-0001"), ("Patient", INSURED),
              ("Policy number", POLICY_NUMBER), ("Period", f"{p['adm_date']} to {p['dis_date']}")]),
        Spacer(1, 12), t, Spacer(1, 10),
        _p("Doctor's prescription: not attached to this bill.", BODY),
    ]
    return "pharmacy_bills.pdf", "Hospital Pharmacy Bill", story, BANNER_PLAIN


LAB_TESTS = [("WBC count", "15.2 x10^3/uL", "4.0 - 11.0", "High"), ("Neutrophils", "84 %", "40 - 75", "High"),
             ("Haemoglobin", "14.1 g/dL", "13.0 - 17.0", ""), ("C-reactive protein", "86 mg/L", "&lt; 5", "High"),
             ("Serum bilirubin", "0.8 mg/dL", "0.3 - 1.2", "")]


def lab_report(p: dict) -> tuple:
    data = [[Paragraph("Test", CELL), _p("Result"), _p("Reference range"), _p("Flag")]]
    for name, result, ref, flag in LAB_TESTS:
        data.append([_p(name), _p(result), Paragraph(ref, CELL), _p(flag)])
    t = Table(data, colWidths=[150, 120, 130, 113])
    t.setStyle(TableStyle(list(_TABLE) + [("BACKGROUND", (0, 0), (-1, 0), SHADE)]))
    story = [
        rows([("Patient", INSURED), ("Collected", f"{p['adm_date']} {p['lab_time']}"), ("Referred by", "Dr. A. Demo Kumar")]),
        _p("Haematology and biochemistry", SUB), t,
        KeepTogether([_p("Ultrasound abdomen", SUB),
                      _p("Non-compressible blind-ending tubular structure in the right iliac fossa, 9 mm diameter, with peri-appendiceal fat\n"
                         "stranding. Impression: findings consistent with acute appendicitis.", BODY)]),
        KeepTogether([_p("CT abdomen", SUB), _p("Dilated inflamed appendix without perforation or abscess. Impression: acute appendicitis.", BODY)]),
    ]
    return "lab_report.pdf", "Laboratory and Imaging Report", story, BANNER_MEDICAL


def consultation_papers(p: dict) -> tuple:
    story = [
        rows([("Hospital", HOSPITAL), ("Patient", f"{INSURED}, {p['age']} years, male"),
              ("Date and time", f"{p['adm_date']} {p['consult_time']}"), ("Doctor", "Dr. A. Demo Kumar, MS (General Surgery), Reg. DEMO-0001")]),
        KeepTogether([_p("Complaints", SUB),
                      _p("Pain in the right lower abdomen for 18 hours, nausea, low-grade fever. No previous surgeries. No known long-term\nconditions.", BODY)]),
        KeepTogether([_p("Examination", SUB), _p("Tenderness in the right iliac fossa with guarding. Temperature 37.9 C. Pulse 96 per minute.", BODY)]),
        KeepTogether([_p("Impression and advice", SUB),
                      _p("Suspected acute appendicitis. Immediate admission advised for investigations and surgery if confirmed. Patient\nreferred to casualty for admission.", BODY)]),
    ]
    return "consultation_papers.pdf", "Outpatient Consultation Note", story, BANNER_PLAIN


def photo_id_proof(p: dict) -> tuple:
    story = [
        _p("This is a made-up identity card for demonstration. It is not a government document.", NOTE),
        rows([("Name", INSURED), ("Date of birth", p["dob"]), ("Age", f"{p['age']} years"), ("Sex", "Male"),
              ("ID number", "DEMO-ID-0000-0000"), ("Address", "Demo Address, Demo City, India"),
              ("Issued by", "Demo Identity Authority (fictional)")]),
    ]
    return "photo_id_proof.pdf", "Identity Card (Synthetic)", story, BANNER_PLAIN


def kyc_form(p: dict) -> tuple:
    story = [
        rows([("Policyholder", INSURED), ("Policy number", POLICY_NUMBER), ("Date of birth", p["dob"]),
              ("Occupation", "Software engineer (demo)"), ("Address", "Demo Address, Demo City, India"),
              ("Tax ID", "XXXXX0000X (masked for demo)"), ("Phone / email", "0000000000 / demo@example.com"),
              ("Reason for KYC", "Claim above Rs. 1 lakh")]),
        Spacer(1, 8),
        _p("Declaration: the details above are true to the best of my knowledge. Signature: (synthetic)", NOTE),
    ]
    return "kyc_form.pdf", "Know Your Customer (KYC) Form", story, BANNER_PLAIN


def neft_form(p: dict) -> tuple:
    story = [
        rows([("Account holder", INSURED), ("Policy number", POLICY_NUMBER), ("Bank", "Demo Bank (fictional)"), ("Branch", "Demo Branch"),
              ("Account number", "XXXXXXXX0000 (masked for demo)"), ("IFSC", "DEMO0000000"), ("Cancelled cheque", "Attached (placeholder page)")]),
        Spacer(1, 8),
        _p("I authorise the insurer to pay the admissible claim amount to the account above. Signature: (synthetic)", NOTE),
    ]
    return "neft_form.pdf", "NEFT Payment Details Form", story, BANNER_PLAIN


BUILDERS = [policy_schedule, claim_form, photo_id_proof, discharge_summary, hospital_bill, lab_report, consultation_papers, pharmacy_bills, kyc_form, neft_form]
DOC_TYPES = {"policy_schedule.pdf": "policy_schedule", "claim_form.pdf": "claim_form", "photo_id_proof.pdf": "photo_id_age_proof",
             "discharge_summary.pdf": "discharge_summary", "hospital_bill.pdf": "final_bill_receipts", "lab_report.pdf": "diagnostic_reports_bills",
             "consultation_papers.pdf": "previous_consultation_papers", "pharmacy_bills.pdf": "pharmacy_bills_prescription",
             "kyc_form.pdf": "kyc", "neft_form.pdf": "neft_form"}


def build_set(name: str, out: Path) -> list[Path]:
    p = params(name)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for builder in BUILDERS:
        filename, title, story, banner = builder(p)
        path = out / filename
        write(path, title, story, banner, subject=f"ARC synthetic demo document ({name})")
        written.append(path)
    (out / "manifest.json").write_text(json.dumps(dict(
        description=f"Synthetic customer document set '{name}' for the ARC demo. Everything is fictional. The doctor's prescription is left out on purpose.",
        set=name, generated_by="demo/make_sample_sets.py", parameters={k: v for k, v in p.items() if k != "set"},
        files=[dict(file=f.name, document_type=DOC_TYPES[f.name]) for f in written],
        intentionally_missing=["prescription for the pharmacy bills (E.1.7.i)"]), indent=2) + "\n", encoding="utf-8")
    return written


# ---------------------------------------------------------------- the check against the original PDFs
def text_lines(path: Path) -> list[str]:
    from pypdf import PdfReader
    out = []
    for page in PdfReader(str(path)).pages:
        out += [l.strip() for l in (page.extract_text() or "").split("\n") if l.strip()]
    return out


def check(original: Path) -> int:
    """Regenerate 'late_filing' into a temporary folder and compare its text layer with the original PDFs, line by line."""
    import tempfile
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        for path in build_set("late_filing", Path(tmp)):
            old = original / path.name
            if not old.exists():
                print(f"MISSING original {old.name} (nothing to compare)")
                continue
            a, b = text_lines(old), text_lines(path)
            if a == b:
                print(f"OK    {path.name}: {len(a)} lines identical")
                continue
            bad += 1
            print(f"DIFF  {path.name}")
            for i in range(max(len(a), len(b))):
                x, y = a[i] if i < len(a) else "<none>", b[i] if i < len(b) else "<none>"
                if x != y:
                    print(f"      line {i + 1}\n        original:  {x!r}\n        generated: {y!r}")
    return bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", metavar="DIR", nargs="?", const=str(ROOT / "demo" / "documents"),
                    help="compare the regenerated late_filing set with the original PDFs in DIR instead of writing the sets")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    if a.check:
        sys.exit(1 if check(Path(a.check)) else 0)
    for s in SETS:
        files = build_set(s, Path(a.out) / s)
        print(f"{s}: {len(files)} documents in {Path(a.out) / s}")
