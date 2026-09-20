"""Runs the 8 steps of docs/DEMO.md through the real agent, in the same sessions the demo uses, and checks the key numbers and citations.

    RETRIEVER=azure AGENT_MODE=foundry python scripts/eval/demo_check.py            # run it before you present
    RETRIEVER=azure AGENT_MODE=foundry python scripts/eval/demo_check.py --show 4   # also print the full answer of step 4

Steps 1 to 3 share one session with no claim; steps 4 to 7 share one session with TC07 loaded (so the follow-ups use chat history, as in the
demo); step 8 is a fresh session with TC02. Nothing here changes Azure. Numbers are what the deterministic engine produced, not the model.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from app.agent.runner import get_agent  # noqa: E402
from app.config import settings  # noqa: E402
from app.rendering.scrub import find as find_internal, officer_texts  # noqa: E402
from app.tools.registry import _DECISION, MAX_HEADLINE_SENTENCES, MAX_NEXT_STEPS, MAX_POINT_CHARS, MAX_POINTS, count_sentences  # noqa: E402

SAMPLES = json.load(open(settings.data_dir / "sample_claims.json", encoding="utf-8"))

# session -> claim loaded (None = no claim). `contains` are regexes that must match the rendered answer; `cites_any` are chunk_ids of which at least one must be
# cited; `cites_all` must all be cited.
STEPS = [
    dict(n=1, session="policy", claim=None, q="Is knee replacement covered and what is the waiting period?",
         types=["coverage_answer", "waiting_period_answer"], contains=[r"24[\s\-‐-―]*months?", r"accident"], cites_any=["C1-b", "C1-b-list"],
         first_point=r"joint replacement"),
    dict(n=2, session="policy", claim=None, q="What was HDFC ERGO's claim settlement ratio last financial year?",
         types=["insufficient_information"], contains=[], forbid=[r"\d+(\.\d+)?\s*%"], cites_any=[]),
    dict(n=3, session="policy", claim=None, q="Policy started 1 March 2025. The insured was admitted on 15 July 2026 for cataract surgery. Has the waiting period been served?",
         types=["waiting_period_answer"], contains=[r"1 Mar 2027", r"16 months"], cites_any=["C1-b"]),
    dict(n=4, session="tc07", claim="TC07", q="Assess this claim", types=["claim_assessment"],
         contains=[r"Likely eligible — pending documents", r"1,22,125", r"1,01,625", r"20,500", r"12,000", r"37,875", r"12,500", r"prescription"],
         summary_has=[r"1,22,125", r"1,01,625", r"20,500", r"49,875", r"12,500", r"12 non-medical items"], cites_all=["B1.1.1-Note-iii", "E1.7"]),
    dict(n=5, session="tc07", claim="TC07", q="Why was the room rent deducted on this claim?", types=["deduction_explanation"],
         contains=[r"62\.5\s*%", r"5,000", r"8,000", r"12,000", r"37,875"], summary_has=[r"62\.5\s*%", r"12,000", r"37,875", r"1,22,125"], cites_all=["B1.1.1-Note-iii"]),
    dict(n=6, session="tc07", claim="TC07", q="Which documents are still missing for this claim?", types=["documents_answer"],
         contains=[r"prescription"], summary_has=[r"8 of 9", r"prescription"], cites_all=["E1.7"]),
    dict(n=7, session="tc07", claim="TC07", q="What would the payable amount be if the room rent had been 5,000 a day?", types=["claim_assessment"],
         contains=[r"1,72,000", r"1,51,500", r"What-if", r"Within the plan limit"], summary_has=[r"1,22,125\s*→\s*₹1,72,000", r"Room rent per day = ₹5,000"],
         cites_all=["C3-k", "E1.7"]),   # no room deduction left, so no Note iii
    dict(n=8, session="tc02", claim="TC02", q="Assess this claim", types=["claim_assessment"],
         contains=[r"Likely not payable", r"Excl03", r"14 days", r"₹0\b"], summary_has=[r"Not payable", r"30-day waiting period", r"14 days"], cites_all=["C1-c"]),
]


def cid(key: str) -> str:
    return key.split(":", 1)[1] if ":" in key else key


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", type=int, action="append", default=[], help="print the full answer of this step number (repeatable)")
    args = ap.parse_args()
    if settings.agent_mode != "foundry" or settings.retriever != "azure":
        sys.exit("Run against the real agent with  RETRIEVER=azure AGENT_MODE=foundry python scripts/eval/demo_check.py")
    from app.observability import configure_logging
    configure_logging()
    agent, sessions, bad, total = get_agent(), {}, 0, 0.0
    for st in STEPS:
        s = sessions.setdefault(st["session"], dict(id=f"demo-{st['session']}", uin=settings.default_uin, history=[],
                                                    claim=SAMPLES[st["claim"]]["claim"] if st["claim"] else None))
        t0 = time.time()
        r = agent.ask(s, st["q"])
        secs = time.time() - t0
        total += secs
        if r.status == "ok":
            s["history"].append(dict(user=st["q"], answer_type=r.answer_type, headline=(r.final or {}).get("headline") or r.markdown.splitlines()[0]))
        cited = {cid(c["chunk_key"]) for c in r.citations}
        problems = []
        if r.status != "ok":
            problems.append(f"status {r.status}")
        if r.answer_type not in st["types"]:
            problems.append(f"answer_type {r.answer_type}, expected {st['types']}")
        problems += [f"missing /{p}/" for p in st["contains"] if not re.search(p, r.markdown, re.I)]
        problems += [f"forbidden /{p}/ present" for p in st.get("forbid", []) if re.search(p, r.markdown, re.I)]
        if st.get("cites_any") and not cited & set(st["cites_any"]):
            problems.append(f"none of {st['cites_any']} cited")
        problems += [f"{c} not cited" for c in st.get("cites_all", []) if c not in cited]
        pts = (r.final or {}).get("points") or []
        if st.get("first_point") and not (pts and re.search(st["first_point"], f"{pts[0].get('label', '')} {pts[0].get('detail', '')}", re.I)):
            problems.append(f"first supporting point does not name /{st['first_point']}/")
        problems += [f"next step reads as a decision: {x[:80]}" for x in (r.final or {}).get("next_steps") or [] if _DECISION.search(x)]
        if not r.summary_markdown.strip():
            problems.append("no summary_markdown")
        else:
            if "The officer decides." not in r.summary_markdown:
                problems.append("summary lacks the 'The officer decides' line")
            if len(r.summary_markdown) >= len(r.markdown):
                problems.append("summary is not shorter than the full answer")
            problems += [f"missing from the summary: /{p}/" for p in st.get("summary_has", []) if not re.search(p, r.summary_markdown, re.I)]
        f = r.final or {}
        if f and f["answer_type"] not in ("claim_assessment", "deduction_explanation") and count_sentences(f.get("headline", "")) > MAX_HEADLINE_SENTENCES:
            problems.append("headline is longer than 2 sentences")
        if len(f.get("points") or []) > MAX_POINTS or any(len(p.get("detail", "")) > MAX_POINT_CHARS for p in f.get("points") or []):
            problems.append("more than 3 points, or a point over 150 characters")
        if len(f.get("next_steps") or []) > MAX_NEXT_STEPS:
            problems.append("more than 3 next steps")
        shown = find_internal(r.markdown.split("### Evidence")[0])
        if shown:
            problems.append(f"internal terms reached the officer: {shown[:3]}")
        raw = [t for _, text in officer_texts(r.final or {}) for t in find_internal(text)]
        rejections = sum(1 for t in r.trace if t["tool"] == "final_answer" and not t["ok"])
        bad += bool(problems)
        print(f"Step {st['n']}  {'PASS' if not problems else 'FAIL'}  {secs:4.0f}s  {r.answer_type:24} {' > '.join(t['tool'] for t in r.trace)}")
        print(f"        \"{st['q'][:90]}\"\n        cited: {sorted(cited)}")
        if raw or rejections:
            print(f"        note: the model's own text contained {raw or 'nothing internal'}; final_answer rejected {rejections}x (the guard and scrub handled it)")
        for p in problems:
            print(f"        - {p}")
        if st["n"] in args.show:
            print("\n" + r.markdown + "\n")
    print(f"\n{len(STEPS) - bad}/{len(STEPS)} steps passed. Agent time in total: {total:.0f}s.")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
