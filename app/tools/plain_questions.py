"""Plain questions: "what's my name", "which hospital was I in", "hello", "what's the weather".

These are not claim assessments. With a claim loaded, a model that reaches for assess_claim turns a one-line question into a full assessment
(reproduced with the real agent: about 3 runs in 12). This module gives the backend a cheap, conservative way to recognise them so that
  * assess_claim is refused for them (registry._dispatch),
  * any answer type other than general_answer is sent back once, and a persistent one is replaced by fact_answer() (registry._final),
  * the offline stand-in can answer them without a model.
It only ever reads the claim's own fields. Nothing here computes money or decides anything.
"""
from __future__ import annotations

import re
from datetime import datetime

from ..rendering.render import inr

NOT_IN_DOCUMENTS = "I don't see that in your documents."

# A question about payment, cover, deductions, documents or the future is never a plain fact question, whatever else it mentions.
_NOT_FACT = re.compile(r"waiting|cover|payable|paid|\bpay\b|deduct|reduc|eligib|document|missing|approv|limit|\bwhy\b|exclu|reimburs|settle|\bwill\b|\bwould\b|\bshould\b|\bcan i\b|\bcould\b|what if|how much (?:is|was|will)? ?(?:it|the claim)", re.I)

_FACTS = [   # (field, pattern on the lower-cased question)
    ("name", r"\b(?:my|the insured'?s?|the patient'?s?) (?:full )?name\b|\bwho am i\b|\bwhat am i called\b|\bwhat'?s my name\b"),
    ("hospital", r"\b(?:which|what) hospital\b|\bhospital name\b|\bname of (?:the|my) hospital\b|\bwhere was i (?:admitted|treated|hospitali[sz]ed)\b"),
    ("admission", r"\bwhen (?:was|did) i (?:get )?admitted\b|\bwhen did i go (?:in|into)\b|\badmission date\b|\bdate of admission\b|\bwhen was i admitted\b"),
    ("discharge", r"\bwhen (?:was|did) i (?:get )?discharged\b|\bwhen did i (?:leave|go home)\b|\bdischarge date\b|\bdate of discharge\b"),
    ("stay", r"\bhow many (?:days|nights)\b|\blength of (?:my )?stay\b|\bhow long (?:was|did) i (?:stay|in hospital|admitted)\b"),
    ("policy_number", r"\bpolicy (?:number|no\b|id\b|#)|\bmy policy id\b"),
    ("plan", r"^\W*(?:what|which) (?:plan|policy|product)(?: do i have| am i on| am i covered under| is it| is this)?\W*$|\bmy plan\b(?! (?:cover|pay))"),
    ("diagnosis", r"\bwhat (?:was|is) (?:my|the) diagnosis\b|\bwhat was i diagnosed with\b|\bdiagnosed with\b|\bwhat (?:was|is) (?:my|the) (?:illness|condition|disease)\b"),
    ("procedure", r"\bwhat (?:procedure|surgery|operation)\b|\bwhich (?:procedure|surgery|operation)\b|\bwhat (?:treatment )?did i (?:have|get|undergo)\b"),
    ("amount", r"\bamount (?:i )?claimed\b|\bclaimed amount\b|\bhow much did i claim\b|\bhow much (?:was|is) (?:my|the) (?:hospital )?bill\b|\b(?:hospital )?bill (?:amount|total)\b|\btotal bill\b"),
    ("elsewhere", r"\b(?:my |the )?(?:home |postal |residential )?address\b|\b(?:phone|mobile|telephone|contact) (?:number|no)\b|\bemail(?: id| address)?\b|\be-mail\b"),
]
_CHAT = re.compile(
    r"^\W*(?:hi|hello|hey|hii+|namaste|good (?:morning|afternoon|evening)|thanks|thank you|thank u|thx|ok thanks|okay thanks|bye|goodbye|"
    r"who are you|what are you|who is this|what can you do|how are you|what'?s? the weather.*|how'?s the weather.*|tell me a joke|what'?s? the (?:time|date|news).*)\W*$", re.I)


def fact_fields(message: str) -> list[str]:
    """The claim details a plain question asks for, in the order they are matched. Empty if it is not a plain fact question."""
    m = (message or "").strip().lower()
    if not m or len(m.split()) > 14 or _NOT_FACT.search(m):
        return []
    return [f for f, pat in _FACTS if re.search(pat, m)]


def plain_kind(message: str, claim: dict | None) -> str | None:
    """"fact" (a detail of the loaded claim), "chat" (greeting, thanks, who are you, off topic) or None (a real question)."""
    if _CHAT.match(message or ""):
        return "chat"
    if claim and fact_fields(message):
        return "fact"
    return None


# ---------------------------------------------------------------- the facts, worded for the customer
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
    return dict(claim_id=claim.get("claim_id"), insured=claim.get("insured_name"), plan=claim.get("plan"),
                sum_insured=inr(base * 100000) if base else None, policy_number=claim.get("policy_number"), policy_uin=claim.get("policy_uin"),
                hospital=claim.get("hospital"), diagnosis=claim.get("diagnosis") or None, procedure=claim.get("procedure") or None,
                admitted=_when(claim.get("admission_datetime")), discharged=_when(claim.get("discharge_datetime")), days_in_hospital=stay_days(claim),
                claimed_amount=inr(claim["claimed_amount"]) if claim.get("claimed_amount") else None)


def _sentence(field: str, f: dict) -> str:
    n = f.get("days_in_hospital")
    text = {
        "name": f["insured"] and f"Your name on this claim is {f['insured']}.",
        "hospital": f["hospital"] and f"You were treated at {f['hospital']}.",
        "admission": f["admitted"] and f"You were admitted on {f['admitted']}.",
        "discharge": f["discharged"] and f"You were discharged on {f['discharged']}.",
        "stay": n is not None and f["admitted"] and f["discharged"] and f"You stayed {n} day{'s' if n != 1 else ''} in hospital, from {f['admitted'].split(' at')[0]} to {f['discharged'].split(' at')[0]}.",
        "policy_number": f["policy_number"] and f"Your policy number is {f['policy_number']}.",
        "plan": f["plan"] and f"You have the {f['plan']} plan" + (f", with a sum insured of {f['sum_insured']}." if f["sum_insured"] else "."),
        "diagnosis": f["diagnosis"] and f"The diagnosis on this claim is {f['diagnosis']}.",
        "procedure": f["procedure"] and f"The procedure on this claim is {f['procedure']}.",
        "amount": f["claimed_amount"] and f"The amount claimed is {f['claimed_amount']}.",
        "elsewhere": None,
    }[field]
    return text or NOT_IN_DOCUMENTS


def fact_answer(message: str, claim: dict) -> str | None:
    """One or two short sentences answering a plain fact question from the claim, or None if the message is not one."""
    fields = fact_fields(message)
    if not fields:
        return None
    f = claim_facts(claim)
    out = []
    for s in (_sentence(x, f) for x in fields):
        if s not in out:
            out.append(s)
    if len(out) > 1 and NOT_IN_DOCUMENTS in out:
        out = [s for s in out if s != NOT_IN_DOCUMENTS] + [NOT_IN_DOCUMENTS]
    return " ".join(out)


def chat_answer(message: str) -> str:
    """The offline stand-in's reply to small talk. (The real agent writes its own; this is only the deterministic fallback.)"""
    m = (message or "").lower()
    if re.search(r"thank|thx", m):
        return "You're welcome. Ask me anything about your claim."
    if re.search(r"weather|joke|news|time|date", m):
        return "I can only help with your claim and your policy."
    if re.search(r"who are you|what are you|who is this|what can you do", m):
        return "I'm ARC. I can explain your claim and your policy, using your documents and the policy wording."
    if re.search(r"bye", m):
        return "Goodbye. Come back any time you have a question about your claim."
    return "Hello. Ask me anything about your claim."
