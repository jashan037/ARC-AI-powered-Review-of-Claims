"""What an answer must lead with, decided in code from the topic of the question.

The model chooses its words; the topic is not its choice. A question about the extras on the bill is answered with the extras, not with the
room-rent calculation (the old complaint P1-4), and a claim whose policy was not in force leads with that, not with an amount.

  of(question)        -> the topic: non_medical | room | doctor_fees | held | deductible | overall | None
  plan(ctx)           -> {'topic', 'lead', 'lead_pattern', 'must', 'sentence'} with figures the engine produced, or None
  problems(text, ctx) -> what to tell the model when the reply leads with something else or leaves a figure of the topic out
  repair(text, ctx)   -> the reply with the code-built lead put first and the missing figures added

Nothing here writes an answer from scratch: `sentence` is built from the engine's own numbers and is only used when the model's
second attempt still misses them.
"""
from __future__ import annotations

import re

from . import claims_engine as E
from .fmt import inr
from .number_guard import scan
from .totals import derived_totals

# first match wins, so the narrow topics come before "overall"
_TOPICS = [
    ("non_medical", re.compile(r"\bnon[- ]?medical\b|\bextras?\b"
                               r"|\b(?:which|what)\b[^?]{0,40}\b(?:items?|extras?|charges?|things?|lines?)\b[^?]{0,30}\b(?:not|aren'?t|isn'?t|won'?t|excluded|disallowed|deducted|rejected)\b"
                               r"|\bwhat(?:'s| is)? not covered\b|\bwhat (?:won'?t|will not) (?:be )?(?:paid|covered)\b|\bwhich items\b", re.I)),
    ("room", re.compile(r"\broom (?:rent|charge|charges|rate|cost|limit)\b|\bicu (?:rent|charge|limit)\b|\bmy room\b", re.I)),
    ("doctor_fees", re.compile(r"\b(?:doctor|surgeon|anaesthetist|anesthetist|consultant|consultation|theatre|theater|nursing)\b[^?]{0,30}\b(?:fee|fees|charge|charges|cost|reduced|cut|less)\b"
                               r"|\bassociated (?:medical )?expenses?\b", re.I)),
    ("held", re.compile(r"\bheld\b|\bon hold\b|\bwaiting for (?:a|the|my|your) (?:document|prescription)\b|\bwhy is (?:some|part|any)\b[^?]{0,20}\bnot counted\b", re.I)),
    ("deductible", re.compile(r"\bdeductible\b|\bco-?pay\w*\b", re.I)),
    ("overall", re.compile(r"\bhow much\b[^?]{0,30}\b(?:paid|pay|get|receive|payment|payable)\b|\bexplain my claim\b|\bwhat will i get\b|\bmy (?:payment|estimate)\b"
                           r"|\bwhy is my payment\b|\bwill (?:i|my claim)\b[^?]{0,30}\b(?:paid|payable|covered|approved|accepted|rejected|get)\b|\bwhat (?:is|about) my claim\b", re.I)),
]

_NOT_COVERED = re.compile(r"\bnot (?:in force|active|covered|payable|paid)\b|\blikely not\b|\bwould(?:n'?t| not) (?:be|likely)\b|\bisn'?t covered\b|\bwas ?n'?t in force\b|\bexpired\b|\bended\b|\boutside\b|\bno longer\b", re.I)


def of(question: str) -> str | None:
    q = question or ""
    for topic, pattern in _TOPICS:
        if pattern.search(q):
            return topic
    return None


def first_block(text: str) -> str:
    """What the customer reads first: everything down to the first blank line (a table counts as part of it)."""
    return re.split(r"\n\s*\n", (text or "").strip(), maxsplit=1)[0]


def _has(text: str, value: float) -> bool:
    return any(abs(v - value) < 1 for v, _ in scan(text)["nums"])


def plan(ctx) -> dict | None:
    """The figures of this question's topic, from the engine. None when there is no claim, no topic, or the topic has no figure here."""
    res = ctx.results.get("assessment")
    if res is None or res.get("what_if"):        # a what-if has its own required figures (guards._required)
        return None
    topic = of(ctx.question)
    if not topic:
        return None
    t, a = derived_totals(res), res["amounts"]
    if topic == "non_medical":
        if not t["non_medical_count"]:
            return None
        items = t["largest_non_medical_items"]
        must = [t["non_medical_total"], float(t["non_medical_count"])] + [i["amount"] for i in items]
        sentence = (f"{inr(t['non_medical_total'])} of your bill is extras your plan doesn't cover, across {t['non_medical_count']} items. The largest are "
                    + ", ".join(f"{i['item']} {inr(i['amount'])}" for i in items) + ".")
        if t["other_non_medical_count"]:
            must += [float(t["other_non_medical_count"]), t["other_non_medical_total"]]
            sentence += f" The other {t['other_non_medical_count']} come to {inr(t['other_non_medical_total'])}."
        return dict(topic=topic, lead=[t["non_medical_total"]], lead_pattern=None, must=must, sentence=sentence)
    if topic == "room":
        if "room_limit_per_day" not in t:
            return None
        return dict(topic=topic, lead=[], lead_pattern=None, must=[t["room_limit_per_day"], t["billed_room_rate_per_day"]],
                    sentence=(f"Your plan allows {inr(t['room_limit_per_day'])} a day for the room and the hospital billed {inr(t['billed_room_rate_per_day'])} a day, "
                              f"so the room and the doctor, theatre and nursing charges are paid in the same proportion."))
    if topic == "doctor_fees":
        off = a["deductions"]["associated"]
        if not off:
            return None
        return dict(topic=topic, lead=[], lead_pattern=None, must=[off],
                    sentence=f"{inr(off)} of the doctor, theatre and nursing charges is reduced in the same proportion as the room.")
    if topic == "held":
        held = a["held_pending"]
        if not held:
            return None
        return dict(topic=topic, lead=[held], lead_pattern=None, must=[held],
                    sentence=f"{inr(held)} is waiting for a document, so it is not counted yet.")
    if topic == "deductible":
        ded = (a.get("calc") or {}).get("aggregate_deductible") or 0
        cop = (a.get("calc") or {}).get("copay") or 0
        if not ded and not cop:
            return None
        return dict(topic=topic, lead=[], lead_pattern=None, must=[v for v in (ded, cop) if v],
                    sentence=f"Your policy takes off {inr(ded)} as the deductible and {inr(cop)} as your share of the cost.")
    # overall
    if res["recommendation"] == "likely_not_payable":
        why = next((k for k in res["checks"] if k["status"] == "violated"), None)
        sentence = E.policy_in_force_text(res["policy_in_force"]) if (res.get("policy_in_force") and not res["policy_in_force"]["in_force"]) else (why["detail"] if why else "")
        if not sentence:
            return None
        return dict(topic="overall", lead=[], lead_pattern=_NOT_COVERED, must=[], sentence=sentence)
    est = a["estimated_payable_if_docs_supplied"]
    if not est:
        return None
    return dict(topic="overall", lead=[], lead_pattern=None, must=[est], sentence=f"About {inr(est)} of your bill looks payable.")


def problems(text: str, ctx) -> list[str]:
    p = plan(ctx)
    if not p:
        return []
    out, head = [], first_block(text)
    if p["lead"] and not any(_has(head, v) for v in p["lead"]):
        out.append(f"This question is about {p['topic'].replace('_', ' ')}: lead with {', '.join(inr(v) for v in p['lead'])} in the first sentence, not with anything else.")
    if p["lead_pattern"] is not None and not p["lead_pattern"].search(head):
        out.append(f"Lead with what your documents show before any amount: {p['sentence']!r}")
    missing = [v for v in p["must"] if not _has(text, v)]
    if missing:
        out.append(f"This question is about {p['topic'].replace('_', ' ')}: the answer must state {', '.join(inr(v) if v >= 100 else f'{v:g}' for v in missing)} from the tools.")
    return out


def repair(text: str, ctx) -> str:
    """Put the code-built lead first when the reply led with something else, and add the topic's figures when they are missing."""
    p = plan(ctx)
    if not p:
        return text
    head = first_block(text)
    lead_wrong = (p["lead"] and not any(_has(head, v) for v in p["lead"])) or (p["lead_pattern"] is not None and not p["lead_pattern"].search(head))
    if lead_wrong:
        text = f"{p['sentence']}\n\n{text}".strip()
    elif [v for v in p["must"] if not _has(text, v)]:
        text = f"{text}\n\n{p['sentence']}".strip()
    return re.sub(r"\n{3,}", "\n\n", text).strip()
