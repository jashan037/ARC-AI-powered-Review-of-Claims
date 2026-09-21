"""Claim intake: read the customer's documents and build the claim the engine needs.

Scope, stated plainly: this reads TEXT PDFs in the format of the synthetic sample documents (demo/documents). It is a small rule-based
reader, not OCR and not a general document understanding service. Scans, photos and unfamiliar layouts are reported back to the customer as
"we couldn't read this", never guessed at. Roadmap step 8 (Azure AI Content Understanding) can replace `read_pdf` / `extract` behind the same
`process_file` / `build_claim` interface.

Nothing here computes money or decides eligibility. Amounts are copied from the documents, then the deterministic engine does the work.
Nothing here logs file names or document contents.
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import date

from .config import ROOT
from .tools import claims_engine as E
from .tools.fmt import inr

SAMPLE_DIR = ROOT / "demo" / "documents"
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_PAGES = 20
MAX_FILES_PER_UPLOAD = 15

# What the customer is asked for, in the order shown. `id` matches the E.1.7 document ids the engine already uses (plus the policy schedule).
EXPECTED = [
    dict(id="policy_schedule", label="Policy schedule"),
    dict(id="claim_form", label="Claim form"),
    dict(id="photo_id_age_proof", label="Photo ID and age proof"),
    dict(id="discharge_summary", label="Discharge summary"),
    dict(id="final_bill_receipts", label="Final hospital bill with receipts"),
    dict(id="diagnostic_reports_bills", label="Lab and imaging reports"),
    dict(id="previous_consultation_papers", label="Consultation papers"),
    dict(id="pharmacy_bills_prescription", label="Pharmacy bills with prescription"),
    dict(id="kyc", label="KYC form (claims above ₹1 lakh)"),
    dict(id="neft_form", label="NEFT form with cancelled cheque"),
]
EXPECTED_IDS = [d["id"] for d in EXPECTED]
LABEL = {d["id"]: d["label"] for d in EXPECTED} | {
    "prescription": "Doctor's prescription", "hospital_registration": "Hospital registration certificate", "implant_invoice_stickers": "Implant invoice and stickers",
    "mlc_fir": "MLC / FIR copy", "alcohol_history": "Alcohol history certificate", "ambulance_invoice": "Ambulance invoice"}
ESSENTIAL = {"policy_schedule": "your policy schedule (it shows your plan and cover)", "final_bill_receipts": "the final itemised hospital bill",
             "claim_form": "your claim form (it shows your hospital dates and diagnosis)"}

# what each file said about itself, in plain words for the customer
FILE_PROBLEMS = {
    "not_pdf": "This isn't a PDF. Please upload PDF files for now.",
    "too_large": "This file is larger than 5 MB. Please upload a smaller copy.",
    "no_text": "We couldn't read any text in this file. It may be a scan or a photo. Please upload a text PDF for now.",
    "encrypted": "This file is password-protected. Please upload an unlocked copy.",
    "unreadable": "We couldn't open this file. Please check that it opens on your device and try again.",
    "too_many_pages": "This file has too many pages for now (20 at most).",
    "unrecognised": "We couldn't tell what this document is. Is it one of the documents in the checklist?",
}


def doc_name(kind: str) -> str:
    """How a document is named inside a sentence: 'final hospital bill with receipts', 'KYC form' (no parenthetical, acronyms keep their capitals)."""
    label = re.sub(r"\s*\(.*?\)", "", LABEL[kind])
    return label if label[:2].isupper() else label[0].lower() + label[1:]


class IntakeError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- reading a PDF
def read_pdf(data: bytes) -> str:
    """The text of a PDF, or an IntakeError with a code from FILE_PROBLEMS. Size, page and type limits are enforced here."""
    if len(data) > MAX_FILE_BYTES:
        raise IntakeError("too_large")
    if not data.lstrip()[:5].startswith(b"%PDF"):
        raise IntakeError("not_pdf")
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise IntakeError("encrypted")
        if len(reader.pages) > MAX_PAGES:
            raise IntakeError("too_many_pages")
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except IntakeError:
        raise
    except Exception as e:  # noqa: BLE001 - any parser failure means "we couldn't open it"; details are not shown to the customer
        raise IntakeError("unreadable") from e
    if len(re.sub(r"\s+", "", text)) < 40:
        raise IntakeError("no_text")
    return text


def clean(text: str) -> str:
    """Drop the demo banner and page markers so the first real line is the document title."""
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')   # PDFs often store typographic quotes
    lines = [l.strip() for l in text.replace("\r", "").split("\n")]
    return "\n".join(l for l in lines if l and not l.upper().startswith("SYNTHETIC DEMO DOCUMENT") and not re.fullmatch(r"page \d+( of \d+)?", l, re.I))


# ---------------------------------------------------------------- recognising the document
_TITLE_RULES = [   # (type, regex on the title line). First match wins.
    ("policy_schedule", r"policy schedule"), ("claim_form", r"claim form"), ("discharge_summary", r"discharge (summary|card)|day care summary|transfer summary"),
    ("final_bill_receipts", r"(final|itemi[sz]ed).{0,20}bill|hospital bill"), ("pharmacy_bills_prescription", r"pharmacy|medicine bill|chemist"),
    ("prescription", r"^(doctor'?s )?prescription|prescription (note|slip)"), ("diagnostic_reports_bills", r"laboratory|imaging report|diagnostic|pathology|radiology"),
    ("previous_consultation_papers", r"consultation|outpatient|opd note"), ("kyc", r"know your customer|\bkyc\b"), ("neft_form", r"\bneft\b|cancelled cheque"),
    ("photo_id_age_proof", r"identity card|photo id|id proof|passport|driving licen[cs]e|aadhaar|voter"),
    ("hospital_registration", r"registration certificate|hospital registration"), ("implant_invoice_stickers", r"implant"),
    ("mlc_fir", r"medico[- ]legal|\bmlc\b|\bfir\b|first information report"), ("alcohol_history", r"alcohol|intoxication"), ("ambulance_invoice", r"ambulance")]


def classify(text: str) -> str | None:
    lines = clean(text).split("\n")
    title = " ".join(lines[:2]).lower()          # the title line, plus a second line in case it wrapped
    for kind, pattern in _TITLE_RULES:
        if re.search(pattern, title):
            return kind
    return None


# ---------------------------------------------------------------- small parsing helpers
def _m(text: str, pattern: str, group: int = 1, flags: int = re.M | re.I):
    m = re.search(pattern, text, flags)
    return m.group(group).strip() if m else None


def _label(text: str, label: str, tail: str = r"([^\n]+)"):
    """The value on the line after a label line."""
    return _m(text, rf"^{label}\s*\n{tail}")


def money(s) -> int | None:
    m = re.search(r"([\d,]+(?:\.\d+)?)", str(s or ""))
    return int(round(float(m.group(1).replace(",", "")))) if m else None


def iso_date(s: str | None) -> str | None:
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", s or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def iso_dt(date_s: str | None, time_s: str | None) -> str | None:
    d = iso_date(date_s)
    return f"{d}T{time_s}" if d and time_s else None


def norm_name(s: str | None) -> str:
    s = re.sub(r"\(.*?\)", "", s or "")
    s = re.split(r",|\bage\b|\d", s, flags=re.I)[0]
    return re.sub(r"\s+", " ", re.sub(r"\b(mr|mrs|ms|shri|smt|dr)\b\.?", "", s, flags=re.I)).strip().lower()


def nice_date(iso: str) -> str:
    return date.fromisoformat(iso[:10]).strftime("%d %b %Y").lstrip("0")


# ---------------------------------------------------------------- extracting the fields each document type carries
def _bill_lines(text: str) -> list[dict]:
    body = text.split("Amount (Rs.)", 1)[-1].split("TOTAL BILL", 1)[0]
    lines, section, i, L = [], "", 0, [l.strip() for l in body.split("\n") if l.strip()]
    while i < len(L):
        ln = L[i]
        if re.fullmatch(r"[A-Z][A-Z &/,\-]+", ln) and not re.search(r"\d", ln):
            section = ln
            i += 1
            continue
        if re.fullmatch(r"\d{1,3}", ln):                      # a numbered row: S.No, description (may wrap), qty, rate, amount
            for j in range(i + 2, min(i + 6, len(L) - 2)):
                if re.fullmatch(r"\d+", L[j]) and re.fullmatch(r"[\d,]+", L[j + 1]) and re.fullmatch(r"[\d,]+", L[j + 2]):
                    lines.append(dict(section=section, description=" ".join(L[i + 1:j]), qty=int(L[j]), rate=money(L[j + 1]), amount=money(L[j + 2])))
                    i = j + 3
                    break
            else:
                i += 1
            continue
        i += 1
    return lines


def extract(kind: str, raw: str) -> dict:
    t = clean(raw)
    f: dict = {}
    if kind == "policy_schedule":
        f = dict(policy_number=_label(t, "Policy number"), insured_name=_m(t, r"Insured person\s*\n([A-Za-z][A-Za-z .'-]+?)\s*(?:\(|,|\n)"),
                 first_inception=iso_date(_label(t, "First policy inception date")), base_si=money(_m(t, r"^Base sum insured\s*\n\s*Rs\.\s*([\d,]+)")),
                 bonus=money(_m(t, r"Cumulative bonus[\s\S]{0,60}?Rs\.\s*([\d,]+)")), policy_uin=_m(t, r"\bUIN\s+([A-Z0-9]{8,})", flags=re.I))
        period = re.search(r"Current policy period\s*\n(\d{2}/\d{2}/\d{4}) to (\d{2}/\d{2}/\d{4})", t)
        f["policy_period"] = [iso_date(period.group(1)), iso_date(period.group(2))] if period else None
        # "my:Optima Secure - Optima Lite" names the product and then the plan: the plan is the LAST name mentioned (and the longest one at that spot)
        where = _label(t, "Plan") or t
        hits = [(where.lower().rfind(p.lower()), len(p), p) for p in E.PLANS if p.lower() in where.lower()]
        f["plan"] = max(hits)[2] if hits else None
        ded = _label(t, "Aggregate deductible")
        f["aggregate_deductible"] = 0 if ded and re.match(r"nil|none|not", ded, re.I) else money(ded)
        cop = _label(t, "Co-payment")
        f["copay_percent"] = 0 if cop and re.match(r"nil|none|not", cop, re.I) else (float(re.search(r"([\d.]+)\s*%", cop).group(1)) if cop and "%" in cop else None)
        pb = _m(t, r"Protect Benefit[\s\S]{0,40}?expenses\)\s*\n([^\n]+)")
        age = re.search(r"Insured person\s*\n[^\n]*?\bAge\s+(\d{1,3})", t)
        per_day = lambda label: money(_m(t, rf"^{label}\s*\n[^\n]*?=\s*Rs\.\s*([\d,]+)"))   # noqa: E731 - "Up to 1% of base sum insured per day = Rs. 5,000 per day"
        pct_of = lambda label: (lambda v: float(v) if v else None)(_m(t, rf"^{label}\s*\n\s*Up to\s*([\d.]+)\s*%"))   # noqa: E731
        plus = _m(t, r"^Plus Benefit\s*\n([^\n]+)")
        f.update(age=int(age.group(1)) if age else None, premium_tier=_label(t, "Premium tier"), nominee=_label(t, "Nominee"),
                 room_rent_limit_text=_label(t, "Room rent limit"), room_rent_limit_per_day=per_day("Room rent limit"), room_rent_limit_percent=pct_of("Room rent limit"),
                 icu_limit_text=_label(t, "ICU limit"), icu_limit_per_day=per_day("ICU limit"), icu_limit_percent=pct_of("ICU limit"),
                 pre_hospitalization_days=money(_label(t, "Pre-hospitalization")), post_hospitalization_days=money(_label(t, "Post-hospitalization")),
                 aggregate_deductible_text=ded, copay_text=cop, protect_benefit_text=pb, plus_benefit_text=plus, plus_benefit_opted=bool(plus) and bool(re.match(r"opted|included|covered|yes", plus, re.I)),
                 restore_benefit_text=_label(t, "Automatic restore benefit"), air_ambulance_text=_label(t, "Emergency air ambulance"), daily_cash_text=_label(t, "Daily cash for shared room"),
                 permanent_exclusions_text=_label(t, "Permanent exclusions"), pre_existing_declared_text=_label(t, "Pre-existing diseases declared"), ped_waiting_text=_label(t, "PED waiting period"))
        f["protect_benefit_opted"] = bool(pb) and bool(re.search(r"opted|included|covered|yes", pb, re.I)) and not re.search(r"not\s+opted|not\s+included|no\b", pb, re.I)
    elif kind == "claim_form":
        hosp = _label(t, "Hospital") or ""
        dx = re.search(r"Diagnosis / ICD-10\s*\n([^\n/]+?)\s*/\s*([A-Z]\d{2}(?:\.\d+)?)", t)
        proc = re.search(r"^Procedure\s*\n([^\n,]+)(?:,\s*(\d{2}/\d{2}/\d{4}))?", t, re.M)
        why = _label(t, "Reason for admission") or ""
        adm, dis = re.search(r"Admitted on\s*\n(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})", t), re.search(r"Discharged on\s*\n(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})", t)
        f = dict(policy_number=_label(t, "Policy number"), patient_name=_label(t, "Name"), hospital=re.sub(r"\s+-\s+(non-)?network hospital\s*$", "", hosp, flags=re.I),
                 hospital_network=bool(re.search(r"\bnetwork hospital", hosp, re.I)) and not re.search(r"non-?network", hosp, re.I),
                 admission=iso_dt(adm.group(1), adm.group(2)) if adm else None, discharge=iso_dt(dis.group(1), dis.group(2)) if dis else None,
                 is_accident=bool(re.search(r"accident", why, re.I)) and not re.search(r"not an accident|non-accident", why, re.I),
                 diagnosis=dx.group(1).strip() if dx else None, icd10=dx.group(2) if dx else None, procedure=proc.group(1).strip() if proc else None,
                 pre_existing=(_label(t, "Pre-existing condition") or "").lower().startswith("y"),
                 claimed_amount=money(_m(t, r"Total claimed\s*\n\s*Rs\.\s*([\d,]+)")), not_enclosed=_label(t, "Not enclosed"),
                 procedure_date=iso_date(proc.group(2)) if proc and proc.group(2) else None, admission_type="emergency" if adm and re.search(r"emergency", t[adm.start():adm.end() + 20], re.I) else None,
                 other_insurance=_label(t, "Other health insurance"), previous_claims=_m(t, r"Previous claims in this policy\s*\n(?:year\s*\n)?([^\n]+)"),
                 hospitalization_claimed=money(_m(t, r"Hospitalization expenses\s*\n\s*claimed\s*\n\s*Rs\.\s*([\d,]+)")),
                 pre_hospitalization_claimed=money(_m(t, r"Pre-hospitalization expenses\s*\n\s*Rs\.\s*([\d,]+)")), post_hospitalization_claimed=money(_m(t, r"Post-hospitalization expenses\s*\n\s*Rs\.\s*([\d,]+)")),
                 enclosed=_m(t, r"^Enclosed\s*\n([\s\S]+?)\n\s*Not enclosed"))
    elif kind == "discharge_summary":
        adm, dis = re.search(r"Date of admission\s*\n(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})", t), re.search(r"Date of discharge\s*\n(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})", t)
        dx = re.search(r"^Diagnosis\s*\n([^\n(]+?)\s*(?:\(ICD-10\s*([A-Z]\d{2}(?:\.\d+)?)\))?\.?\s*$", t, re.M)
        f = dict(patient_name=_m(t, r"^Patient\s*\n([A-Za-z][A-Za-z .'-]+?)\s*(?:,|\n)"), admission=iso_dt(adm.group(1), adm.group(2)) if adm else None,
                 discharge=iso_dt(dis.group(1), dis.group(2)) if dis else None, diagnosis=dx.group(1).strip() if dx else None,
                 procedure=_m(t, r"^Procedure\s*\n([A-Za-z][^\n]*?)\s+(?:under|on|by)\b"), room_rate_per_day=money(_m(t, r"Room category\s*\n[^\n]*?Rs\.\s*([\d,]+)\s*per day")),
                 length_of_stay=money(_m(t, r"Length of stay\s*\n(\d+)")), room_category=_m(t, r"Room category\s*\n([^\n(]+?)\s*(?:\(|$)"),
                 procedure_date=iso_date(_m(t, r"^Procedure\s*\n[\s\S]{0,120}?\bon\s+(\d{2}/\d{2}/\d{4})")))
    elif kind == "final_bill_receipts":
        lines, span = _bill_lines(t), re.search(r"Admission / discharge\s*\n(\d{2}/\d{2}/\d{4}) to (\d{2}/\d{2}/\d{4})", t)
        f = dict(patient_name=_label(t, "Patient"), policy_number=_label(t, "Policy number"), hospital=_label(t, "Hospital"), lines=lines,
                 total=money(_m(t, r"TOTAL BILL\s*\n([\d,]+)")), admission_date=iso_date(span.group(1)) if span else None, discharge_date=iso_date(span.group(2)) if span else None,
                 room_rate_per_day=money(_m(t, r"Room category\s*\n[^\n]*?Rs\.\s*([\d,]+)\s*per day")), bill_number=_label(t, "Bill number"),
                 room_category=_m(t, r"Room category\s*\n([^\n,]+)"), advance_paid=money(_m(t, r"Advance deposit[\s\S]{0,40}?\)\s*\nRs\.\s*([\d,]+)")),
                 final_payment=money(_m(t, r"Final payment[\s\S]{0,40}?\)\s*\nRs\.\s*([\d,]+)")), total_paid=money(_m(t, r"Total paid\s*\nRs\.\s*([\d,]+)")), balance_text=_label(t, "Balance"))
        room = next((l for l in lines if l["section"].startswith("ROOM") and re.search(r"room charges", l["description"], re.I)), None)
        f["room_days"] = room["qty"] if room else None
    elif kind == "pharmacy_bills_prescription":
        f = dict(patient_name=_label(t, "Patient"), policy_number=_label(t, "Policy number"), total=money(_m(t, r"^TOTAL\s*\n([\d,]+)")),
                 prescription_attached=bool(re.search(r"prescription[^\n]*(attached|enclosed|included)", t, re.I)) and not re.search(r"prescription[^\n]*not\s+(attached|enclosed|included)", t, re.I))
    elif kind == "photo_id_age_proof":
        age = _label(t, "Age")
        f = dict(patient_name=_label(t, "Name"), dob=iso_date(_label(t, "Date of birth")), age=money(age) if age else None, sex=_label(t, "Sex"))
    elif kind == "kyc":
        f = dict(patient_name=_label(t, "Policyholder"), policy_number=_label(t, "Policy number"), dob=iso_date(_label(t, "Date of birth")), reason=_label(t, "Reason for KYC"))
    elif kind == "neft_form":
        f = dict(patient_name=_label(t, "Account holder"), policy_number=_label(t, "Policy number"), cancelled_cheque=_label(t, "Cancelled cheque"))
    elif kind in ("diagnostic_reports_bills", "previous_consultation_papers", "prescription"):
        f = dict(patient_name=_m(t, r"^Patient\s*\n([A-Za-z][A-Za-z .'-]+?)\s*(?:,|\n)"))
        if kind == "previous_consultation_papers":
            f["consulted_on"] = iso_date(_m(t, r"^Date and time\s*\n(\d{2}/\d{2}/\d{4})"))
        if kind == "diagnostic_reports_bills":
            f["collected_on"] = iso_date(_m(t, r"^Collected\s*\n(\d{2}/\d{2}/\d{4})"))
    return {k: v for k, v in f.items() if v is not None}


# ---------------------------------------------------------------- one file
def process_file(name: str, data: bytes) -> dict:
    """Read and recognise one file. Returns {status, type?, fields?, message?}. Never raises. Nothing about the content is logged."""
    try:
        text = read_pdf(data)
    except IntakeError as e:
        return dict(status=e.code, message=FILE_PROBLEMS[e.code])
    kind = classify(text)
    if not kind:
        return dict(status="unrecognised", message=FILE_PROBLEMS["unrecognised"])
    return dict(status="recognised", type=kind, label=LABEL[kind], fields=extract(kind, text), digest=hashlib.sha256(data).hexdigest()[:12])


def store(session: dict, name: str, result: dict) -> dict:
    """Keep a recognised document in the session (the newest file of a type replaces the older one) and return what the customer sees about this file."""
    shown = dict(filename=name, status=result["status"])
    if result["status"] != "recognised":
        return shown | dict(message=result["message"])
    docs = session.setdefault("documents", {})
    old = docs.get(result["type"])
    docs[result["type"]] = dict(filename=name, fields=result["fields"], digest=result["digest"])
    return shown | dict(type=result["type"], label=result["label"], replaced=bool(old and old["digest"] != result["digest"]),
                        message=f"Recognised as: {result['label']}." + (" This replaced the earlier file." if old and old["digest"] != result["digest"] else ""))


def sample_files() -> list[tuple[str, bytes]]:
    return [(p.name, p.read_bytes()) for p in sorted(SAMPLE_DIR.glob("*.pdf"))]


# ---------------------------------------------------------------- putting it together
def _prescription_present(docs: dict) -> bool:
    return "prescription" in docs or bool(docs.get("pharmacy_bills_prescription", {}).get("fields", {}).get("prescription_attached"))


def checklist(docs: dict) -> list[dict]:
    out = []
    for d in EXPECTED:
        got = docs.get(d["id"])
        state, note = ("received", None) if got else ("missing", None)
        if d["id"] == "pharmacy_bills_prescription" and got and not _prescription_present(docs):
            state, note = "partial", "Bills received. The doctor's prescription is still missing."
        out.append(dict(id=d["id"], label=d["label"], state=state, note=note, filename=got["filename"] if got else None))
    return out


def _claim_id(policy_number: str, admission: str) -> str:
    return f"CLM-{admission[:10].replace('-', '')}-{re.sub(r'[^0-9]', '', policy_number)[-4:] or '0000'}"


def build(session: dict) -> dict:
    """Decide the intake status and, when there is enough, the claim. Returns {status, reasons[{message, documents}], checklist, missing, claim?}."""
    docs = session.get("documents", {})
    F = lambda k: docs.get(k, {}).get("fields", {})   # noqa: E731
    reasons: list[dict] = []
    add = lambda msg, *ids: reasons.append(dict(message=msg, documents=list(ids)))   # noqa: E731

    for kind, what in ESSENTIAL.items():
        if kind not in docs:
            add(f"We couldn't find {what}. Please add it.", kind)
    sched, form, ds, bill = F("policy_schedule"), F("claim_form"), F("discharge_summary"), F("final_bill_receipts")
    if "policy_schedule" in docs and (not sched.get("plan") or not sched.get("base_si") or not sched.get("first_inception")):
        add("We couldn't read your plan, sum insured or start date from the policy schedule. Please upload the schedule exactly as it was issued.", "policy_schedule")
    if "claim_form" in docs and not (form.get("admission") and form.get("discharge")):
        add("We couldn't read your hospital dates from the claim form. Please upload a clearer copy.", "claim_form")
    if "final_bill_receipts" in docs and not bill.get("lines"):
        add("We couldn't read the items on the hospital bill. Please upload a clearer copy.", "final_bill_receipts")
    elif bill.get("lines") and bill.get("total") is not None:
        added = sum(l["amount"] for l in bill["lines"])
        if added != bill["total"]:
            add(f"The items on your hospital bill add up to {inr(added)}, but its total says {inr(bill['total'])}. Please upload a clearer copy of the bill.", "final_bill_receipts")

    # the documents must belong to the same person, policy and stay
    ref_name = norm_name(sched.get("insured_name") or form.get("patient_name"))
    for kind, d in docs.items():
        n = d["fields"].get("patient_name") or d["fields"].get("insured_name")
        if ref_name and n and norm_name(n) != ref_name and kind != "policy_schedule":
            add(f"The name on your {doc_name(kind)} ({n}) doesn't match your policy ({sched.get('insured_name') or form.get('patient_name')}). Please check you uploaded the right document.", kind)
    if sched.get("policy_number"):
        for kind, d in docs.items():
            pn = d["fields"].get("policy_number")
            if pn and pn != sched["policy_number"] and kind != "policy_schedule":
                add(f"The policy number on your {doc_name(kind)} ({pn}) is different from your policy schedule ({sched['policy_number']}).", kind, "policy_schedule")
    for label, a, b, ka, kb, ida, idb in (
            ("admission", form.get("admission"), ds.get("admission"), "claim form", "discharge summary", "claim_form", "discharge_summary"),
            ("discharge", form.get("discharge"), ds.get("discharge"), "claim form", "discharge summary", "claim_form", "discharge_summary"),
            ("admission", (ds.get("admission") or "")[:10] or None, bill.get("admission_date"), "discharge summary", "hospital bill", "discharge_summary", "final_bill_receipts")):
        if a and b and a[:10] != b[:10]:
            add(f"The {label} date on your {ka} ({nice_date(a)}) is different from your {kb} ({nice_date(b)}). Please check both.", ida, idb)
    if form.get("claimed_amount") and bill.get("total") and form["claimed_amount"] != bill["total"]:
        add(f"The amount on your claim form ({inr(form['claimed_amount'])}) is different from your hospital bill ({inr(bill['total'])}).", "claim_form", "final_bill_receipts")

    cl = checklist(docs)
    missing = [c["label"] if c["state"] == "missing" else "The doctor's prescription for your pharmacy bills" for c in cl if c["state"] != "received"]
    if reasons:
        session["claim"] = None
        return dict(status="needs_attention", reasons=reasons, checklist=cl, missing=missing)

    adm, dis = form.get("admission") or ds.get("admission"), form.get("discharge") or ds.get("discharge")
    documents = {}
    for kind in EXPECTED_IDS[1:]:
        if kind in docs:
            documents[kind] = dict(present=True, complete=True)
    if "pharmacy_bills_prescription" in docs and not _prescription_present(docs):
        documents["pharmacy_bills_prescription"] = dict(present=True, complete=False, missing_parts=["prescription"])
    category = _categorise(bill["lines"])
    claim = dict(
        claim_id=_claim_id(sched.get("policy_number", ""), adm), insured_name=sched.get("insured_name") or form.get("patient_name"), plan=sched["plan"],
        base_si_lakh=sched["base_si"] / 100000, sum_insured_available=sched["base_si"] + sched.get("bonus", 0), first_policy_inception=sched["first_inception"],
        policy_period=sched.get("policy_period"), admission_datetime=adm, discharge_datetime=dis, is_accident=bool(form.get("is_accident")), is_day_care_procedure=False,
        pre_existing=bool(form.get("pre_existing")), diagnosis=form.get("diagnosis") or ds.get("diagnosis") or "", icd10=form.get("icd10"),
        procedure=form.get("procedure") or ds.get("procedure") or "", hospital=form.get("hospital") or bill.get("hospital"), hospital_network=bool(form.get("hospital_network", True)),
        differential_billing=bool(bill.get("room_rate_per_day") or ds.get("room_rate_per_day")), room_rate_per_day=bill.get("room_rate_per_day") or ds.get("room_rate_per_day"),
        room_days=bill.get("room_days") or ds.get("length_of_stay"), claimed_amount=form.get("claimed_amount") or bill["total"],
        aggregate_deductible_remaining=sched.get("aggregate_deductible", 0), copay_percent=sched.get("copay_percent") or 0,
        protect_benefit_opted=bool(sched.get("protect_benefit_opted")), documents=documents, prescription_missing=not _prescription_present(docs) and "pharmacy_bills_prescription" in docs,
        policy_uin=sched.get("policy_uin"), policy_number=sched.get("policy_number"), bill_lines=category,
        documents_data=dict(discharge_summary={k: v for k, v in dict(patient_name=ds.get("patient_name"), admission_date=(ds.get("admission") or "")[:10] or None,
                                                                    discharge_date=(ds.get("discharge") or "")[:10] or None).items() if v},
                            bill={k: v for k, v in dict(patient_name=bill.get("patient_name"), admission_date=bill.get("admission_date"), discharge_date=bill.get("discharge_date")).items() if v}))
    if not claim["policy_uin"]:
        claim.pop("policy_uin")
    session["claim"] = claim
    session["uin"] = claim.get("policy_uin") or session.get("uin")
    return dict(status="ready", reasons=[], checklist=cl, missing=missing, claim=claim)


def _categorise(lines: list[dict]) -> list[dict]:
    """The bill sections and item names decide the category. Annexure B is looked up with the same function the engine's tools use."""
    out = []
    for l in lines:
        sec, desc = l["section"], l["description"]
        if sec.startswith("ROOM"):
            cat = "room" if re.search(r"\broom\b", desc, re.I) and not re.search(r"nurs", desc, re.I) else "associated"
        elif sec.startswith(("PROFESSIONAL", "OT", "SURGE")):
            cat = "associated"
        elif sec.startswith("PHARMACY"):
            cat = "consumables" if re.search(r"consumable|suture|trocar|syringe|dressing|catheter", desc, re.I) else "pharmacy_medicine"
        elif sec.startswith(("INVESTIGATION", "LAB", "DIAGNOSTIC", "RADIOLOGY")):
            cat = "diagnostics"
        else:
            cat = "other"
        row = dict(description=desc, category=cat, amount=l["amount"])
        if cat == "other":   # miscellaneous: non-medical if it is listed in Annexure B
            hit = E.lookup_non_medical_item(desc)["matches"]
            if hit:
                row["category"], row["annexure_b_no"] = "non_medical", hit[0]["sr_no"]
        out.append(row)
    return out


# ---------------------------------------------------------------- what the customer is told
ESTIMATE_NOTE = "Amounts I give are estimates; your insurer's team makes the final decision."


def _plain(label: str) -> str:
    """A document's name inside a sentence: 'the doctor's prescription for your pharmacy bills', 'policy schedule'."""
    label = re.sub(r"\s*\(.*?\)", "", label).strip()
    return label if label[:2].isupper() else label[:1].lower() + label[1:]


def first_message(claim: dict, missing: list[str], first: bool = True) -> str:
    """The first chat message after the documents are read: plain sentences built in code from the claim's facts, no table."""
    what = claim.get("procedure") or "a hospital stay"
    what = what[:1].lower() + what[1:] if what[:2] != what[:2].upper() else what
    if claim.get("diagnosis"):
        what += f" for {claim['diagnosis'].lower()}"
    where = f" at {claim['hospital']}" if claim.get("hospital") else ""
    text = (f"**{claim['insured_name']}**, here's what I've read: {what}{where}, {nice_date(claim['admission_datetime'])} to {nice_date(claim['discharge_datetime'])}, "
            f"bill {inr(claim['claimed_amount'])}.")
    period = claim.get("policy_period")
    if period and all(period):
        text += f" Your policy runs from {nice_date(period[0])} to {nice_date(period[1])}."
    names = [_plain(m) for m in missing]
    if len(names) == 1:
        text += f"\n\n**One document is still missing:** {names[0]}. You can drop it anywhere on this page."
    elif names:
        text += f"\n\n**{len(names)} documents are still missing:** {', '.join(names[:-1])} and {names[-1]}. You can drop them anywhere on this page."
    else:
        text += "\n\nYour documents look complete."
    return text + "\n\nAsk me anything about your claim." + (f" {ESTIMATE_NOTE}" if first else "")
