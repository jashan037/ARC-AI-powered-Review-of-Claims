"""Which tool a customer's question needs first, decided in code so that the model can answer in one call.

  policy_kind(message)    "coverage" | "definition" | "documents" for a question about the policy wording itself (what is covered or excluded, how long a waiting
                          period is, what a term means, which documents a claim needs); None for anything about the customer's own claim.
  claim_related(...)      True when the answer needs the assessment of the loaded claim (payment, deductions, documents missing, eligibility, a what-if, a short follow-up).

A policy question gets `search_policy` run before the model's first call; a claim question gets `assess_claim`. The model still has both tools for what the code did not foresee.
"""
from __future__ import annotations

import re

# words that make a question about the customer's own claim, whatever else it asks
_OWN = re.compile(r"\bmy (?:claim|bill|payment|estimate|admission|treatment|hospital|stay|room|policy start)\b|\bthis claim\b|\bfor my claim\b|\bserved\b|\bhas my\b|\bwas i\b|\bhow much\b|\bpaid\b"
                  r"|\bdeduct\w*|\bwhat if\b|\bwould i get\b|\bi was admitted\b|\bpolicy started\b|\bpolicy began\b|\bdid i\b|\bam i\b(?! covered)", re.I)
_COVERAGE = re.compile(r"\b(?:covered|covers?|coverage|exclud\w+|exclusions?|waiting period|permitted|does (?:the|my|this) (?:policy|plan)|what does the (?:policy|plan))\b|\bis there a (?:waiting|limit|sub-?limit)\b", re.I)
_DEFINITION = re.compile(r"^\W*what (?:does|do) .{1,40}\bmean\b|\bmeaning of\b|\bdefinition of\b|^\W*what (?:is|are) (?:a |an |the )?(?:room rent|pre-?existing\b.*|hospitali[sz]ation|associated medical expenses?|sum insured|day ?care\b.*|waiting period)\??$", re.I)
_DOCUMENTS = re.compile(r"\bwhich documents\b.*\b(?:need|require\w*)\b|\bdocuments (?:do i|are|is) (?:need\w*|require\w*)|\brequired documents\b|\bwhat documents\b.*\b(?:need|require\w*)\b"
                        r"|\bhow long do i have to (?:send|submit)\b|\btime limit\b|\bdeadline\b|\bby when (?:must|do) i (?:send|submit)\b", re.I)
_CLAIM = re.compile(r"\b(?:paid|payable|payment|pay|deduct\w*|reduc\w*|room|non-?medical|held|hold|prescription|missing|documents?|estimate|assess\w*|bill|claim|what if|doctor|fees?|consultation|"
                    r"total|lower|less|served|waiting|eligible|deductible|co-?pay|found|find|next|send|proceed)\b", re.I)


def policy_kind(message: str) -> str | None:
    m = message or ""
    if _OWN.search(m):
        return None
    if _DOCUMENTS.search(m):
        return "documents"
    if _DEFINITION.search(m.strip()):
        return "definition"
    if _COVERAGE.search(m):
        return "coverage"
    return None


def claim_related(message: str, history: list) -> bool:
    """A question that needs the loaded claim's assessment: it names a payment or claim topic, or it is a short follow-up to an earlier answer."""
    m = message or ""
    if _CLAIM.search(m):
        return True
    return bool(history) and len(m.split()) <= 12
