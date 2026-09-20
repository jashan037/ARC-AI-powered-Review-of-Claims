"""Clause reference -> chunk_id(s).

The deterministic tools cite clauses in the policy's own notation ("C.1.b", "B.1.1.1 Note iii",
"A.1.2 Def. 5", "Annexure B"). Citations must be exact, so instead of searching for them we
resolve them to the chunk IDs produced by tools/chunk_policy.py and fetch those chunks directly.
"""
from __future__ import annotations

import re

PLAN_TOKEN = "PLAN:"


def plan_chunk_id(plan: str) -> str:
    return "ANX-C-" + plan.replace("Optima ", "").replace(" ", "-")


def _one(ref: str) -> list[str]:
    r = ref.strip().rstrip(".")
    if not r:
        return []
    if r.startswith(PLAN_TOKEN):
        return [plan_chunk_id(r[len(PLAN_TOKEN):].strip())]
    low = r.lower()
    if low.startswith("annexure b"):
        return ["ANX-B"]
    if low.startswith("annexure c"):
        return ["ANX-C-notes"]
    m = re.match(r"A\.?(1\.[12])\s*Def\.?\s*(\d+)", r)
    if m:
        return [f"A{m.group(1)}-Def{m.group(2)}"]
    m = re.match(r"C\.(\d)\.([a-q])(\.[ivx]+)?$", r)
    if m:
        ids = [f"C{m.group(1)}-{m.group(2)}"]
        if (m.group(1), m.group(2), m.group(3)) == ("1", "b", ".vi"):
            ids.append("C1-b-list")
        return ids
    m = re.match(r"B\.(\d+(?:\.\d+)*)(?:\.[a-z])?(?:\s+Note\s+(iii|ii|i))?$", r)
    if m:
        base = f"B{m.group(1)}"
        return [f"{base}-Note-iii"] if m.group(2) == "iii" else [base]
    m = re.match(r"([DE])\.(\d+(?:\.\d+)*)(?:\.[a-z])?$", r)
    if m:
        return [f"{m.group(1)}{m.group(2)}"]
    return []


def resolve(refs) -> list[str]:
    """Accepts a string ('A.1.1 Def. 19; B.1.1.1 Note i') or a list of them. Keeps order, drops duplicates."""
    if isinstance(refs, str):
        refs = [refs]
    out: list[str] = []
    for ref in refs or []:
        for part in re.split(r";", ref):
            for cid in _one(part):
                if cid not in out:
                    out.append(cid)
    return out
