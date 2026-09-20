"""Deterministic claim checks. The LLM never does dates or money: it calls these functions.

Assumptions (state them in the limitations slide):
- Surgeon/anaesthetist/consultant fees, OT and nursing count as 'associated medical expenses' (Def. 5).
  Pharmacy, consumables, diagnostics and implants are not reduced by the proportionate deduction.
- The proportionate deduction applies only where the hospital bills differently by room category.
- Aggregate deductible is applied before the sum insured (D.1.19); co-pay comes from the schedule (0 if absent).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
from difflib import SequenceMatcher

from ..config import settings

RULES = settings.data_dir / "rules"


def _load(name):
    return json.loads((RULES / name).read_text(encoding="utf-8"))


PLANS = _load("plan_config.json")["plans"]
NON_MEDICAL = {i["sr_no"]: i["item"] for i in _load("non_medical_items.json")["items"]}
DOC_RULES = _load("claim_documents.json")["documents"]

SPECIFIED_KEYWORDS = {
    "cholecystectomy": "Cholecystectomy", "cholecystitis": "Diseases of gall bladder including cholecystitis",
    "gall bladder": "Diseases of gall bladder including cholecystitis", "gallbladder": "Diseases of gall bladder including cholecystitis",
    "gallstone": "Diseases of gall bladder including cholecystitis", "cholelithiasis": "Diseases of gall bladder including cholecystitis",
    "cataract": "Cataract and other disorders of lens and retina", "hernia": "Hernia",
    "tonsillectomy": "Adenoidectomy, tonsillectomy", "adenoidectomy": "Adenoidectomy, tonsillectomy",
    "tonsillitis": "Tonsillitis", "hysterectomy": "Hysterectomy", "haemorrhoid": "Fissure/fistula in anus, haemorrhoids",
    "hemorrhoid": "Fissure/fistula in anus, haemorrhoids", "piles": "Fissure/fistula in anus, haemorrhoids",
    "fistula": "Fissure/fistula in anus, haemorrhoids", "kidney stone": "Calculus diseases of urogenital system",
    "calculus": "Calculus diseases of urogenital system", "fibroid": "Fibroids (fibromyoma)",
    "prostate": "Benign hyperplasia of prostate", "varicocele": "Varicocele", "hydrocele": "Hydrocele/Rectocele",
    "joint replacement": "Joint replacement surgeries", "knee replacement": "Joint replacement surgeries",
    "hip replacement": "Joint replacement surgeries", "sinusitis": "Sinusitis, Rhinitis", "glaucoma": "Glaucoma",
    "pancreatitis": "Pancreatitis", "cirrhosis": "All forms of cirrhosis", "meniscal": "Ligament, tendon and meniscal tear",
    "septum deviation": "Surgery for nasal septum deviation", "varicose": "Surgery for varicose veins and varicose ulcers",
}

WHAT_IF_KEYS = {"first_policy_inception", "admission_datetime", "discharge_datetime", "plan", "base_si_lakh", "room_rate_per_day",
                "protect_benefit_opted", "aggregate_deductible_remaining", "is_accident", "pre_existing", "copay_percent",
                "prior_continuous_coverage_months", "diagnosis", "procedure", "hospital_network", "differential_billing"}


def _d(s):
    return dt.date.fromisoformat(s[:10])


def _months_between(a, b):
    m = (b.year - a.year) * 12 + (b.month - a.month)
    return m - 1 if b.day < a.day else m


def _sub_months(d, n):
    y, m = divmod(d.year * 12 + (d.month - 1) - n, 12)
    dim = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m]
    return dt.date(y, m + 1, min(d.day, dim))


def _add_months(d, n):
    return _sub_months(d, -n)


def match_specified(text):
    t = (text or "").lower()
    for k, v in SPECIFIED_KEYWORDS.items():
        if k in t:
            return v
    return None


def apply_what_if(claim: dict, overrides: dict | None) -> dict:
    c = copy.deepcopy(claim)
    for k, v in (overrides or {}).items():
        if k in WHAT_IF_KEYS:
            c[k] = v
        elif k == "documents" and isinstance(v, dict):
            c.setdefault("documents", {}).update(v)
    return c


# ------------------------------------------------------------------ waiting periods (C.1)
def waiting_context(c):
    adm = _d(c["admission_datetime"])
    credit = c.get("prior_continuous_coverage_months", 0)
    eff = _sub_months(_d(c["first_policy_inception"]), credit)
    return dict(first_inception=c["first_policy_inception"], effective_inception=eff.isoformat(), admission_date=adm.isoformat(),
                prior_credit_months=credit, elapsed_days=(adm - eff).days, elapsed_months=_months_between(eff, adm))


def check_waiting_period(c):
    ctx = waiting_context(c)
    days, months, accident = ctx["elapsed_days"], ctx["elapsed_months"], c.get("is_accident", False)
    eff = _d(ctx["effective_inception"])
    out = []
    if accident:
        out.append(dict(code="Excl03", name="30-day waiting period", clause="C.1.c", evidence=["C.1.c"], status="not_applicable", required="30 days", detail="Accidents are exempt"))
    elif months > 12:
        out.append(dict(code="Excl03", name="30-day waiting period", clause="C.1.c", evidence=["C.1.c"], status="not_applicable", required="30 days", detail="Continuous coverage of more than 12 months"))
    else:
        out.append(dict(code="Excl03", name="30-day waiting period", clause="C.1.c", evidence=["C.1.c"], status="satisfied" if days >= 30 else "violated",
                        required="30 days", detail=f"{days} days since first inception"))
    listed = match_specified(f"{c.get('diagnosis', '')} {c.get('procedure', '')}")
    if not listed:
        out.append(dict(code="Excl02", name="Specified disease/procedure", clause="C.1.b", evidence=["C.1.b"], status="not_applicable", required="24 months", detail="Not on the specified list"))
    elif accident:
        out.append(dict(code="Excl02", name="Specified disease/procedure", clause="C.1.b", evidence=["C.1.b"], status="not_applicable", required="24 months", detail="Accidents are exempt"))
    else:
        req = 36 if c.get("pre_existing") else 24
        out.append(dict(code="Excl02", name="Specified disease/procedure", clause="C.1.b", evidence=["C.1.b", "C.1.b.vi"],
                        status="satisfied" if months >= req else "violated", required=f"{req} months",
                        detail=f"'{listed}' is on the specified list; {months} months completed"))
    if c.get("pre_existing"):
        out.append(dict(code="Excl01", name="Pre-existing disease", clause="C.1.a", evidence=["C.1.a", "A.1.1 Def. 35"],
                        status="satisfied" if months >= 36 else "violated", required="36 months", detail=f"{months} months completed"))
    else:
        out.append(dict(code="Excl01", name="Pre-existing disease", clause="C.1.a", evidence=["C.1.a"], status="not_applicable", required="36 months", detail="No pre-existing disease declared"))
    for k in out:                      # earliest date on which a not-yet-served waiting period is served
        if k["status"] == "violated":
            k["eligible_from"] = ((eff + dt.timedelta(days=30)) if k["code"] == "Excl03"
                                  else _add_months(eff, int(k["required"].split()[0]))).isoformat()
    return out


# ------------------------------------------------------------------ admissibility and exclusions
def check_hospitalization(c):
    a, b = dt.datetime.fromisoformat(c["admission_datetime"]), dt.datetime.fromisoformat(c["discharge_datetime"])
    hours = (b - a).total_seconds() / 3600
    ok = hours >= 24 or c.get("is_day_care_procedure")
    return [dict(code="HOSP24", name="Hospitalization (24 hours or day care)", clause="A.1.1 Def. 19; B.1.1.1 Note i",
                 evidence=["A.1.1 Def. 19", "B.1.1.1 Note i"], status="satisfied" if ok else "violated", required="24 hours or a day-care procedure",
                 detail=f"{hours:.0f} hours admitted" + ("" if ok else " and not a day-care procedure"))]


def check_exclusions(c):
    out = []
    if c.get("refractive_error_dioptres") is not None:
        bad = c["refractive_error_dioptres"] < 7.5
        out.append(dict(code="Excl15", name="Refractive error", clause="C.2.l", evidence=["C.2.l"], status="violated" if bad else "satisfied",
                        required="7.5 dioptres or more", detail=f"Refractive error {c['refractive_error_dioptres']} D"))
    for f in c.get("review_flags", []):
        out.append(dict(code=f["code"], name="Needs judgement", clause=f.get("clause", ""), evidence=[f["clause"]] if f.get("clause") else [],
                        status="needs_review", required="", detail=f["reason"]))
    return out


# ------------------------------------------------------------------ room rent and bill
def _limit_per_day(c, kind):
    cfg = PLANS[c["plan"]][kind]
    if cfg["type"] == "at_actuals":
        return None
    if cfg["type"] == "percent_of_base_si_per_day":
        return c["base_si_lakh"] * 100000 * cfg["percent"] / 100
    return c.get("single_private_room_rate_per_day")


def protect_in_force(c):
    p = PLANS[c["plan"]]["protect_benefit"]
    return p == "covered" or (p == "optional" and c.get("protect_benefit_opted", False))


def _prescription_missing(c):
    """The documents checklist is the source of truth; the legacy flag is only used when it has no entry."""
    d = c.get("documents", {}).get("pharmacy_bills_prescription")
    if d is not None:
        return (not d.get("present")) or "prescription" in d.get("missing_parts", [])
    return bool(c.get("prescription_missing"))


def analyze_bill(c):
    room_lim, icu_lim = _limit_per_day(c, "room_rent"), _limit_per_day(c, "icu")
    r_rate, i_rate = c.get("room_rate_per_day"), c.get("icu_rate_per_day")
    room_ratio = min(1.0, room_lim / r_rate) if room_lim and r_rate else 1.0
    icu_ratio = min(1.0, icu_lim / i_rate) if icu_lim and i_rate else 1.0
    differential, protect, presc_missing = c.get("differential_billing", True), protect_in_force(c), _prescription_missing(c)
    lines = []
    for ln in c["bill_lines"]:
        amt, cat = ln["amount"], ln["category"]
        pay, status, reason, ev = amt, "payable", "", []
        if cat == "room" and room_ratio < 1:
            pay, status, ev = amt * room_ratio, "reduced", ["B.1.1.1 Note iii"]
            reason = f"Room billed {r_rate:,.0f}/day against a limit of {room_lim:,.0f}/day"
        elif cat == "icu_room" and icu_ratio < 1:
            pay, status, ev = amt * icu_ratio, "reduced", ["B.1.1.1 Note iii"]
            reason = f"ICU billed {i_rate:,.0f}/day against a limit of {icu_lim:,.0f}/day"
        elif cat == "associated" and room_ratio < 1 and differential:
            pay, status, ev = amt * room_ratio, "reduced", ["A.1.2 Def. 5", "B.1.1.1 Note iii"]
            reason = "Associated medical expense reduced in the same proportion as room rent"
        elif cat == "non_medical" and not protect:
            pay, status, ev = 0.0, "non_payable", ["C.3.k", "Annexure B"]
            reason = f"Annexure B item #{ln.get('annexure_b_no', '?')}; Protect Benefit is not in force"
        elif cat == "pharmacy_medicine" and presc_missing:
            status, ev, reason = "on_hold", ["B.1.1.e", "E.1.7.i"], "Prescription missing; payable once supplied"
        lines.append(dict(description=ln["description"], category=cat, billed=amt, payable=round(pay, 2), status=status,
                          reason=reason, evidence=ev, annexure_b_no=ln.get("annexure_b_no")))
    return dict(room_ratio=round(room_ratio, 4), icu_ratio=round(icu_ratio, 4), room_limit_per_day=room_lim, room_rate_per_day=r_rate,
                room_days=c.get("room_days"), icu_limit_per_day=icu_lim, icu_rate_per_day=i_rate, plan=c["plan"],
                room_rule=PLANS[c["plan"]]["room_rent"], protect_benefit_in_force=protect, prescription_missing=presc_missing, lines=lines)


def lookup_non_medical_item(text: str, protect: bool | None = None):
    t = text.lower().strip()
    scored = []
    for no, item in NON_MEDICAL.items():
        il = item.lower()
        s = 1.0 if t in il or il in t else SequenceMatcher(None, t, il).ratio()
        scored.append((s, no, item))
    scored.sort(reverse=True)
    matches = [dict(sr_no=no, item=item, match=round(s, 2)) for s, no, item in scored[:3] if s >= 0.6]
    return dict(query=text, listed_in_annexure_b=bool(matches), matches=matches,
                note="Annexure B items are non-payable (C.3.k) unless the Protect Benefit is in force (B.2.3)." if matches else
                     "Not in HDFC's Annexure B. IRDAI's standard list (not yet loaded) may still treat it as non-payable or part of another charge.")


# ------------------------------------------------------------------ documents
def _required(doc, c):
    r = doc["required"]
    return (r == "always" or (r == "always_for_reimbursement" and c.get("claim_type", "reimbursement") == "reimbursement")
            or (r == "if_non_network_hospital" and not c.get("hospital_network", True))
            or (r == "if_implants_used" and c.get("implants_used")) or (r == "if_accident" and c.get("is_accident"))
            or (r == "if_claim_above_inr_100000" and c["claimed_amount"] > 100000)
            or (r == "if_ambulance_claimed" and c.get("ambulance_claimed")))


def check_required_documents(c):
    sub, checklist, missing, incomplete = c.get("documents", {}), [], [], []
    for d in DOC_RULES:
        if not _required(d, c):
            continue
        s = sub.get(d["id"])
        base = dict(id=d["id"], name=d["name"], clause=d["clause"], evidence=["E.1.7"])
        if not s or not s.get("present"):
            checklist.append({**base, "status": "missing"}); missing.append(base)
        elif not s.get("complete", True):
            parts = s.get("missing_parts", [])
            checklist.append({**base, "status": "incomplete", "missing_parts": parts}); incomplete.append({**base, "missing_parts": parts})
        else:
            checklist.append({**base, "status": "ok"})
    return dict(checklist=checklist, missing=missing, incomplete=incomplete)


def check_consistency(c):
    found, dd = [], c.get("documents_data", {})
    ds, bill = dd.get("discharge_summary", {}), dd.get("bill", {})
    for key, label in (("patient_name", "Patient name (bills must be in the insured person's name, E.1.7 Note i)"),
                       ("admission_date", "Admission date"), ("discharge_date", "Discharge date")):
        if key in ds and key in bill and ds[key] != bill[key]:
            found.append(dict(field=key, discharge_summary=ds[key], bill=bill[key], note=label, evidence=["E.1.7"]))
    return found


# ------------------------------------------------------------------ amount
def calculate_claim_amount(c, payable_total):
    ded = min(c.get("aggregate_deductible_remaining", 0), payable_total)
    after = payable_total - ded
    copay = round(after * c.get("copay_percent", 0) / 100, 2)
    si_cap = c.get("sum_insured_available", c["base_si_lakh"] * 100000)
    payable = round(min(after - copay, si_cap), 2)
    return dict(admissible_before_deductible=round(payable_total, 2), aggregate_deductible=round(ded, 2), copay=copay,
                sum_insured_cap_applied=payable < after - copay, payable=payable)


# ------------------------------------------------------------------ orchestrator
def assess(c: dict) -> dict:
    checks = check_waiting_period(c) + check_hospitalization(c) + check_exclusions(c)
    bill, docs, conflicts = analyze_bill(c), check_required_documents(c), check_consistency(c)
    lines = bill["lines"]
    gross = sum(l["billed"] for l in lines)
    held = sum(l["payable"] for l in lines if l["status"] == "on_hold")
    payable_all = sum(l["payable"] for l in lines)
    ded = {"room": 0.0, "associated": 0.0, "non_medical": 0.0}
    for l in lines:
        gap = l["billed"] - l["payable"]
        key = {"room": "room", "icu_room": "room", "associated": "associated", "non_medical": "non_medical"}.get(l["category"])
        if key and gap:
            ded[key] += gap
    violated = [k for k in checks if k["status"] == "violated"]
    review = [k for k in checks if k["status"] == "needs_review"]

    if violated:
        rec, est, now, amt_est = "likely_not_payable", 0.0, 0.0, None
    else:
        amt_est, amt_now = calculate_claim_amount(c, payable_all), calculate_claim_amount(c, payable_all - held)
        est, now = amt_est["payable"], amt_now["payable"]
        rec = ("needs_human_review" if (review or conflicts) else
               "likely_eligible_pending_documents" if (docs["missing"] or docs["incomplete"]) else "likely_eligible")

    if violated:
        cov = dict(status="not_covered", detail="; ".join(f"{v['name']}: {v['detail']}" for v in violated), evidence=sum((v["evidence"] for v in violated), []))
    elif review:
        cov = dict(status="needs_review", detail="; ".join(r["detail"] for r in review), evidence=sum((r["evidence"] for r in review), []))
    else:
        cov = dict(status="covered", detail=f"Inpatient hospitalization for {c.get('diagnosis', 'the stated condition')} appears admissible under the base cover",
                   evidence=["B.1.1"])
    amount_refs = []   # clauses behind the money steps, so a deductible or Protect Benefit payment never goes uncited
    if amt_est and amt_est["aggregate_deductible"] > 0:
        amount_refs += ["B.2.7", "D.1.19"]
    if bill["protect_benefit_in_force"] and any(l["category"] == "non_medical" for l in lines):
        amount_refs += ["B.2.3", "C.3.k"]
    evidence_refs = []
    for src in (cov["evidence"], *[k["evidence"] for k in checks if k["status"] != "not_applicable"], *[l["evidence"] for l in lines],
                *[x["evidence"] for x in conflicts], amount_refs, ["PLAN:" + c["plan"]]):
        for r in src:
            if r not in evidence_refs:
                evidence_refs.append(r)
    return dict(
        claim_id=c["claim_id"], recommendation=rec,
        claim=dict(insured_name=c.get("insured_name"), plan=c["plan"], base_si_lakh=c["base_si_lakh"], policy_uin=c.get("policy_uin"),
                   diagnosis=c.get("diagnosis"), procedure=c.get("procedure"), hospital=c.get("hospital"),
                   admission=c["admission_datetime"], discharge=c["discharge_datetime"], claimed_amount=c.get("claimed_amount")),
        coverage=cov, waiting=dict(context=waiting_context(c), checks=[k for k in checks if k["code"].startswith("Excl0") and k["code"] in ("Excl01", "Excl02", "Excl03")]),
        checks=checks, bill=bill, documents=docs, inconsistencies=conflicts,
        amounts=dict(gross_billed=round(gross, 2), deductions={k: round(v, 2) for k, v in ded.items()}, held_pending=round(held, 2),
                     admissible_total=round(payable_all, 2), calc=amt_est,
                     estimated_payable_if_docs_supplied=est, payable_confirmed_now=now),
        evidence_refs=evidence_refs)


def compact_summary(result: dict) -> dict:
    """What the LLM gets back from the assess_claim tool. Small, and every number is final."""
    issues = [f"{k['name']}: {k['detail']}" for k in result["checks"] if k["status"] in ("violated", "needs_review")]
    issues += [f"Missing/incomplete: {d['name']}" for d in result["documents"]["missing"] + result["documents"]["incomplete"]]
    issues += [f"{i['note']}: '{i['discharge_summary']}' vs '{i['bill']}'" for i in result["inconsistencies"]]
    a = result["amounts"]
    return dict(claim_id=result["claim_id"], recommendation=result["recommendation"], coverage=result["coverage"]["status"],
                deductions=a["deductions"], held_pending=a["held_pending"],
                estimated_payable_if_docs_supplied=a["estimated_payable_if_docs_supplied"], payable_confirmed_now=a["payable_confirmed_now"],
                issues=issues)
