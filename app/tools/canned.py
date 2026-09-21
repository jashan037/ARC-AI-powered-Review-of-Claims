"""Replies decided in code, before any model call: the claim is not ready, a request to change the rules or to decide a claim, an Azure content-filter refusal.

None of these ever decides a claim, reveals instructions or reaches the model, so they cannot be talked out of it.
"""
from __future__ import annotations

import re

NOT_READY = "Please upload your documents first. Once they have been checked, I can answer your questions about your claim."
DECLINE_RULES = "I can't change how I work or share my instructions. I can explain your claim, what your policy covers, or which documents you still need."
DECLINE_DECISION = "I can't approve, reject or pay a claim. Your insurer's team makes that decision. I can explain what I found on your claim and what happens next."
DECLINE_FILTERED = "I can't help with that request. I can explain your claim, what your policy covers, or which documents you still need."

_RULES = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass)\b.{0,40}\b(?:instructions?|rules?|prompt|guidelines?|polic(?:y|ies)|restrictions?)\b"
    r"|\b(?:system|hidden|initial|original|secret)\s+(?:prompt|instructions?|message)\b|\b(?:your|the)\s+(?:instructions?|prompt|rules)\b.{0,30}\b(?:say|are|is|were)\b"
    r"|\b(?:reveal|show|print|repeat|tell me|give me|share|leak)\b.{0,30}\b(?:prompt|instructions?|configuration|rules)\b"
    r"|\byou are now\b|\bact as\b|\bpretend (?:to be|you)\b|\bjailbreak\b|\bdeveloper mode\b|\bdan mode\b|\bnew instructions?\b", re.I)
_DECISION = re.compile(
    r"\b(?:approve|reject|deny|decline|settle|pay out|pay me|release)\b.{0,25}\b(?:my|the|this|our)\b.{0,15}\b(?:claim|request|payment|money)\b"
    r"|\b(?:mark|set|change|update)\b.{0,25}\b(?:claim|it|this)\b.{0,25}\b(?:as )?(?:approved|payable|paid|rejected|settled)\b"
    r"|\bpay me\b|\bapprove (?:it|this)\b|\bwill you (?:approve|pay)\b.{0,30}\bclaim\b", re.I)


def hostile_kind(message: str) -> str | None:
    """"rules" (change or reveal how ARC works), "decision" (approve, reject or pay a claim) or None."""
    m = message or ""
    if _RULES.search(m):
        return "rules"
    if _DECISION.search(m):
        return "decision"
    return None


def canned_reply(message: str) -> str | None:
    kind = hostile_kind(message)
    return {"rules": DECLINE_RULES, "decision": DECLINE_DECISION}.get(kind)
