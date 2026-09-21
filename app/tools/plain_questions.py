"""The loaded claim's own details, cleaned, in plain-language fields (what get_claim_summary returns and what the model is given each turn)."""
from __future__ import annotations

from datetime import datetime

from .fmt import inr
from .sanitize import clean

NOT_IN_DOCUMENTS = "I don't see that in your documents."

# A question about payment, cover, deductions, documents or the future is never a plain fact question, whatever else it mentions.


def _when(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return d.strftime("%d %b %Y").lstrip("0") + (f" at {d:%H:%M}" if "T" in iso and len(iso) > 10 else "")


def stay_days(claim: dict) -> int | None:
    try:
        return (datetime.fromisoformat(claim["discharge_datetime"]).date() - datetime.fromisoformat(claim["admission_datetime"]).date()).days
    except (KeyError, ValueError, TypeError):
        return None


def claim_facts(claim: dict) -> dict:
    """Everything get_claim_summary tells the model. A missing value is None: it is not in the customer's documents."""
    base = claim.get("base_si_lakh")
    c = lambda k: clean(claim.get(k)) or None   # noqa: E731 - text from the customer's documents: one line, no control characters, capped
    return dict(claim_id=c("claim_id"), insured=c("insured_name"), plan=claim.get("plan"),
                sum_insured=inr(base * 100000) if base else None, policy_number=c("policy_number"), policy_uin=claim.get("policy_uin"),
                hospital=c("hospital"), diagnosis=c("diagnosis"), procedure=c("procedure"),
                admitted=_when(claim.get("admission_datetime")), discharged=_when(claim.get("discharge_datetime")), days_in_hospital=stay_days(claim),
                claimed_amount=inr(claim["claimed_amount"]) if claim.get("claimed_amount") else None)
