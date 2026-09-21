"""The complete claim facts: everything intake read from ALL the customer's documents, as plain labelled lines.

The model gets this block on every turn and get_claim_summary returns it, so a question about a fact that is in the documents (policy expiry, sum insured,
co-pay, room rent limit ...) is answered from it and never with "I don't see that". Values are only ever copied from the documents (or computed by the
engine: days stayed, policy in force); text from documents is cleaned (one line, capped) and is data, never instructions.

A field intake could not read is listed under "Not found in the documents", never left out silently and never guessed.
"""
from __future__ import annotations

from datetime import date

from . import claims_engine as E
from .fmt import inr
from .plain_questions import stay_days
from .sanitize import clean
from .totals import derived_totals

EXPIRY = "Policy expiry (end of current policy period)"
START = "Policy start (current policy period)"
FIRST = "First policy inception (the customer's cover began here; waiting periods count from it)"


def _d(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return None
    return d.strftime("%d %b %Y").lstrip("0")


def _dt(iso: str | None) -> str | None:
    return f"{_d(iso)} at {iso[11:16]}" if iso and len(iso) >= 16 else _d(iso)


def _t(v, n: int = 160) -> str | None:
    return clean(v, n) or None if v is not None else None


def build(session: dict) -> dict:
    """{'sections': [(title, [(label, value)])], 'not_found': [label]}. Sources: the loaded claim and the fields intake read from each document."""
    claim = session.get("claim") or {}
    docs = session.get("documents") or {}
    F = lambda k: docs.get(k, {}).get("fields", {})   # noqa: E731
    sched, form, ds, bill, pharm, pid = F("policy_schedule"), F("claim_form"), F("discharge_summary"), F("final_bill_receipts"), F("pharmacy_bills_prescription"), F("photo_id_age_proof")
    kyc = F("kyc")
    missing: list[str] = []

    def need(label, value):
        if value is None or value == "":
            missing.append(label)
        return value

    base = claim.get("base_si_lakh")
    period = claim.get("policy_period") or sched.get("policy_period") or [None, None]
    ded = sched.get("aggregate_deductible")
    ded = claim.get("aggregate_deductible_remaining") if ded is None and "policy_schedule" not in docs else ded
    cop = sched.get("copay_percent")
    cop = claim.get("copay_percent") if cop is None and "policy_schedule" not in docs else cop
    protect = _t(sched.get("protect_benefit_text")) or (None if "protect_benefit_opted" not in claim else ("Opted" if claim["protect_benefit_opted"] else "Not opted"))

    policy = [
        ("Policy number", need("Policy number", _t(claim.get("policy_number") or sched.get("policy_number")))),
        ("Plan", need("Plan", claim.get("plan") or sched.get("plan"))),
        ("Insured person", need("Insured person", _t(claim.get("insured_name") or sched.get("insured_name")))),
        ("Age", need("Age", sched.get("age") or pid.get("age"))),
        ("Date of birth", _d(pid.get("dob") or kyc.get("dob"))),
        (FIRST, need("First policy inception date", _d(claim.get("first_policy_inception") or sched.get("first_inception")))),
        (START, need("Policy start (current policy period)", _d(period[0]))),
        (EXPIRY, need("Policy expiry (end of current policy period)", _d(period[1]))),
        ("Premium tier", need("Premium tier", _t(sched.get("premium_tier")))),
        ("Base sum insured", need("Sum insured", inr(base * 100000) if base else None)),
        ("Cumulative bonus", need("Cumulative bonus", inr(sched["bonus"]) if sched.get("bonus") is not None else None)),
        ("Sum insured available (base plus bonus)", inr(claim["sum_insured_available"]) if claim.get("sum_insured_available") else None),
        ("Room rent limit", need("Room rent limit", _t(sched.get("room_rent_limit_text")))),
        ("ICU limit", need("ICU limit", _t(sched.get("icu_limit_text")))),
        ("Pre-hospitalization cover", need("Pre-hospitalization days", f"{sched['pre_hospitalization_days']} days" if sched.get("pre_hospitalization_days") is not None else None)),
        ("Post-hospitalization cover", need("Post-hospitalization days", f"{sched['post_hospitalization_days']} days" if sched.get("post_hospitalization_days") is not None else None)),
        ("Aggregate deductible", need("Aggregate deductible", "None (Nil)" if ded == 0 else inr(ded) if ded else None)),
        ("Co-payment", need("Co-payment", "None (Nil)" if cop == 0 else f"{cop:g}%" if cop else None)),
        ("Protect Benefit (non-medical expenses)", need("Protect Benefit", protect)),
        ("Plus Benefit", need("Plus Benefit", _t(sched.get("plus_benefit_text")))),
        ("Automatic restore benefit", need("Restore benefit", _t(sched.get("restore_benefit_text")))),
        ("Emergency air ambulance", _t(sched.get("air_ambulance_text"))),
        ("Daily cash for shared room", _t(sched.get("daily_cash_text"))),
        ("Permanent exclusions", need("Permanent exclusions", _t(sched.get("permanent_exclusions_text")))),
        ("Pre-existing diseases declared", need("Pre-existing diseases declared", _t(sched.get("pre_existing_declared_text")))),
        ("PED waiting period", need("PED waiting period", _t(sched.get("ped_waiting_text")))),
        ("Nominee", _t(sched.get("nominee"))),
    ]
    adm = claim.get("admission_datetime") or form.get("admission") or ds.get("admission")
    dis = claim.get("discharge_datetime") or form.get("discharge") or ds.get("discharge")
    days = stay_days(claim) if claim else None
    inforce = E.policy_in_force(claim) if claim.get("admission_datetime") and claim.get("policy_period") else None
    stay = [
        ("Policy in force on the admission date", E.policy_in_force_text(inforce) if inforce else None),
        ("Hospital", need("Hospital", _t(claim.get("hospital") or form.get("hospital") or bill.get("hospital")))),
        ("Network hospital", None if "hospital_network" not in form else ("Yes" if form["hospital_network"] else "No")),
        ("Admission", need("Admission date", _dt(adm))),
        ("Admission type", _t(form.get("admission_type"))),
        ("Discharge", need("Discharge date", _dt(dis))),
        ("Days stayed", days if days is not None else None),
        ("Diagnosis", need("Diagnosis", _t(claim.get("diagnosis") or form.get("diagnosis") or ds.get("diagnosis")))),
        ("Diagnosis code (ICD-10)", _t(claim.get("icd10") or form.get("icd10"))),
        ("Procedure", need("Procedure", _t(claim.get("procedure") or form.get("procedure") or ds.get("procedure")))),
        ("Procedure date", _d(form.get("procedure_date") or ds.get("procedure_date"))),
        ("Room category", _t(ds.get("room_category") or bill.get("room_category"))),
        ("Room rate billed", f"{inr(claim['room_rate_per_day'])} per day" if claim.get("room_rate_per_day") else None),
        ("Accident", None if "is_accident" not in claim else ("Yes" if claim["is_accident"] else "No, illness")),
        ("Pre-existing condition stated on the claim form", None if "pre_existing" not in claim else ("Yes" if claim["pre_existing"] else "No")),
        ("Other health insurance", _t(form.get("other_insurance"))),
        ("Previous claims in this policy year", _t(form.get("previous_claims"))),
    ]
    money = [
        ("Amount claimed", need("Amount claimed", inr(claim["claimed_amount"]) if claim.get("claimed_amount") else None)),
        ("Hospital bill total", need("Hospital bill total", inr(bill["total"]) if bill.get("total") else None)),
        ("Hospital bill number", _t(bill.get("bill_number"))),
        ("Advance deposit paid to the hospital", inr(bill["advance_paid"]) if bill.get("advance_paid") else None),
        ("Final payment to the hospital", inr(bill["final_payment"]) if bill.get("final_payment") else None),
        ("Balance with the hospital", _t(bill.get("balance_text"))),
        ("Pharmacy bills total", inr(pharm["total"]) if pharm.get("total") else None),
    ]
    if docs:
        from .. import intake
        cl = intake.checklist(docs)
        recv = [c["label"] for c in cl if c["state"] == "received"]
        gaps = [c["label"] if c["state"] == "missing" else "Doctor's prescription for the pharmacy bills" for c in cl if c["state"] != "received"]
        files = [("Documents received", "; ".join(recv) or "none"), ("Documents still missing", "; ".join(gaps) or "none"),
                 ("Documents the claim form says are not enclosed", _t(form.get("not_enclosed")))]
    else:
        files = []
    est = []
    if claim.get("bill_lines"):
        t = derived_totals(E.assess(claim))
        est = [("Hospital bill", inr(t["bill_total"])), ("Reduced because of the room limit (room plus doctor, theatre and nursing charges)", inr(t["room_related_reduction"])),
               ("Extras not payable (non-medical items)", f"{t['non_medical_count']} items, {inr(t['non_medical_total'])}"),
               ("Largest extras", "; ".join(f"{x['item']} {inr(x['amount'])}" for x in t["largest_non_medical_items"])),
               ("Other extras", f"{t['other_non_medical_count']} items, {inr(t['other_non_medical_total'])}" if t["other_non_medical_count"] else None),
               ("Waiting for a document", inr(t["waiting_for_a_document_total"])), ("Estimated payment", inr(t["estimate_total"])), ("Counted so far", inr(t["counted_so_far"])),
               ("Plan room limit per day", inr(t["room_limit_per_day"]) if "room_limit_per_day" in t else None), ("Room billed per day", inr(t["billed_room_rate_per_day"]) if "billed_room_rate_per_day" in t else None),
               ("Share of the room charges paid", f"{t['share_paid_percent']:g}%" if "share_paid_percent" in t else None)]
    sections = [("Policy", policy), ("Hospital stay and claim", stay), ("Amounts", money), ("Payment estimate, worked out in code", est), ("Documents", files)]
    sections = [(t, [(k, v) for k, v in rows if v is not None and v != ""]) for t, rows in sections]
    return dict(sections=[(t, rows) for t, rows in sections if rows], not_found=missing)


def facts_text(session: dict) -> str:
    """The block the model reads on every turn."""
    f = build(session)
    out = []
    for title, rows in f["sections"]:
        out.append(f"{title}:")
        out += [f"- {k}: {v}" for k, v in rows]
    out.append("Not found in the documents: " + ("; ".join(f["not_found"]) if f["not_found"] else "nothing required is missing"))
    return "\n".join(out)


def summary(session: dict) -> dict:
    """What get_claim_summary returns."""
    f = build(session)
    return dict(loaded=True, claim_facts=facts_text(session), not_found_in_the_documents=f["not_found"])
