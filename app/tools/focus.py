"""Which part of a deduction explanation a question is about, decided in code and not by the model.

"Which items are not payable?" answered with the room-rent working first because the model left the focus at "all". The words of the question
decide it here; when the model chose a different focus, registry._render corrects it. No match means the question is not clearly about one part, and the
model's own choice (or "all") stands. A question that names two parts, or asks why the payment is lower than the bill, gets "all".
"""
from __future__ import annotations

import re

_PARTS = [   # (focus, pattern on the lower-cased question), each a part of the estimate that the engine reports separately
    ("hold", r"\b(?:held|hold|on hold|prescriptions?)\b"),
    ("deductible", r"\bdeductible\b|\bco-?pay(?:ment)?\b"),
    ("non_medical", r"\bnot payable\b|\bnon-?payable\b|\bnon-?medical\b|\bnot covered\b|\bnot paid\b|\bwon'?t be paid\b|\bwhich items\b|\bgloves?\b|\bmasks?\b|\bconsumables?\b"),
    ("room", r"\broom\b|\bproportion(?:ate)?\b"),
    ("associated", r"\bdoctors?\b|\bconsultation\b|\bsurgeons?\b|\bprofessional fees?\b|\bfees?\b|\banaesthe\w*\b|\bnursing\b|\bassociated\b"),
]
_WHOLE = re.compile(r"\blower than\b|\bless than\b|\bsmaller than\b|\bdifference\b|\bwhy (?:is|was|did|are|were)\b.*\b(?:bill|paid|payment|amount|estimate)\b|\breduced\b|\bdeduct\w*\b|\bcut\b")


def focus_for(question: str) -> str | None:
    """room | associated | non_medical | hold | deductible | all, or None when the question does not point at one of them."""
    q = (question or "").lower()
    hits = [name for name, pat in _PARTS if re.search(pat, q)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        return "all"
    return "all" if _WHOLE.search(q) else None
