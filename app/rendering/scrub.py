"""Internal identifiers must never reach the claims officer.

Text the model writes for the officer (headline, points, next steps, caveats) must not mention result ids, chunk keys, field names from tool
results or the names of our tools. Two layers use this module:
  * `find()` backs a validation guard in tools/registry.py that sends such text back once for rephrasing;
  * `scrub_final()` runs in the renderer and removes what is still there after the second attempt, so the guarantee does not depend on the model.

Nothing here touches citations (chunk keys belong there), tool results, or text the backend writes itself.
"""
from __future__ import annotations

import re

# tool name -> what to say instead when the name has to be removed from a sentence
TOOL_WORDS = {
    "search_policy": "the policy search", "get_clause": "the clause lookup", "check_waiting_period": "the waiting-period check",
    "lookup_non_medical_item": "the non-medical items lookup", "get_claim_summary": "the claim summary",
    "assess_claim": "the claim assessment", "final_answer": "the answer",
}
# other internal words -> plain replacement ("" = delete)
OTHER_WORDS = {"result_id": "check result", "result id": "check result", "chunk_key": "citation", "chunk key": "citation", "chunk_id": "clause",
               "chunk id": "clause", "eligible_from": "eligible-from"}

_IDS = (r"\b(?:claim|waiting)-(?=[0-9a-f]*\d)[0-9a-f]{8}\b"                            # a result id such as claim-1a2b3c4d or waiting-9f8e7d6c
        r"|\b[a-z][a-z0-9]*(?:-[a-z0-9]+)+:[A-Za-z][\w.\-]*")                           # a chunk key such as optima-secure-v062425:C1-b
_WORDS = "|".join(sorted(map(re.escape, [*TOOL_WORDS, *OTHER_WORDS]), key=len, reverse=True))
_AUDIENCE = r"\bAudience:\s*(?:customer|officer)\b\.?"                                   # the line each turn starts with must not be echoed back
INTERNAL = re.compile(rf"\b(?:{_WORDS})\b|{_IDS}|{_AUDIENCE}", re.I)


def find(text: str) -> list[str]:
    """The internal terms in text, in order."""
    return [m.group(0) for m in INTERNAL.finditer(text or "")]


def _plain(m: re.Match) -> str:
    w = m.group(0).lower()
    if w in TOOL_WORDS:
        return TOOL_WORDS[w]
    if w in OTHER_WORDS:
        return OTHER_WORDS[w]
    return ""      # an id value or a chunk key: nothing sensible to say instead


def scrub(text: str) -> str:
    """Remove internal terms and tidy what is left. A parenthetical that mentions one is dropped whole; tool names become plain words."""
    if not text or not INTERNAL.search(text):
        return text
    out = re.sub(rf"\s*\([^()]*(?:{INTERNAL.pattern})[^()]*\)", "", text, flags=re.I)
    out = INTERNAL.sub(_plain, out)
    out = re.sub(r"\(\s*\)|\[\s*\]", "", out)                     # empty brackets left behind
    out = re.sub(r"\s+([,.;:)])", r"\1", out)                     # " ," -> ","
    out = re.sub(r"([(])\s+", r"\1", out)
    out = re.sub(r"\b(the|a|an|of|to|for) \1\b", r"\1", out, flags=re.I)   # "the the" left by a replacement
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;:")
    return out
