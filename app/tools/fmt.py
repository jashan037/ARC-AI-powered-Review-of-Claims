"""Small formatting helpers shared by the tools and the reply guards: rupees, dates, percentages, the room-rent rule in words."""
from __future__ import annotations

import datetime as dt

DOC_SHORT = {
    "claim_form": "Claim form", "photo_id_age_proof": "Photo ID and age proof", "hospital_registration": "Hospital registration certificate",
    "discharge_summary": "Discharge summary", "final_bill_receipts": "Final hospital bill with receipts",
    "implant_invoice_stickers": "Implant invoice and stickers", "previous_consultation_papers": "Previous consultation papers",
    "diagnostic_reports_bills": "Diagnostic reports and bills", "pharmacy_bills_prescription": "Pharmacy bills with prescription",
    "mlc_fir": "MLC / FIR copy", "alcohol_history": "Alcohol / intoxication certificate", "kyc": "KYC documents (claims above Rs. 1 lakh)",
    "neft_form": "NEFT form with cancelled cheque", "ambulance_invoice": "Ambulance invoice"}


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


def pct(x: float) -> str:
    v = round(x * 100, 2)
    return f"{v:g}%"


def rule_text(rule: dict, base_si_lakh: float) -> str:
    if rule["type"] == "percent_of_base_si_per_day":
        return f"{rule['percent']:g}% of the {inr(base_si_lakh * 100000)} base sum insured per day"
    if rule["type"] == "single_private_room":
        return "the single private room rate of the hospital"
    return "actuals (no limit)"


def sum_lines(lines, cat, key):
    return sum(l[key] for l in lines if l["category"] in cat)
