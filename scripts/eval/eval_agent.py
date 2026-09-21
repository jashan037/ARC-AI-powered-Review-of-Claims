"""Agent-level evaluation: real agent, real tools, real index.

    RETRIEVER=azure AGENT_MODE=foundry python scripts/eval/eval_agent.py                # everything once
    RETRIEVER=azure AGENT_MODE=foundry python scripts/eval/eval_agent.py --repeat 3     # run each case 3 times to find flaky ones
    python scripts/eval/eval_agent.py --only TC07,Q01 --verbose                         # a few cases, show the answers
    python scripts/eval/eval_agent.py --offline                                         # harness self-check with the keyword stand-in (no Azure, no cost)

Two suites:
  claims     the 12 sample claims, message "Assess this claim". Checked against the hand-derived `expected` block in
             data/sample_claims.json: answer type, recommendation, both payable amounts, deductions, flags, required citations.
  questions  claims-officer questions (some with a claim loaded, 3 the wording cannot answer). Checked for answer type, citations,
             a few facts that must appear, and (for what-if and dated questions) numbers from the deterministic engine.
             P01 to P10 are plain questions ("what's my name", "hello") asked with the claim built from demo/documents loaded, as a
             customer: they must get a short general_answer, no assessment, no sections.

Checks that apply to every run: final_answer accepted, no citations stripped by the validator, every citation exists in the
retriever (the deployed index when RETRIEVER=azure). A run that needed a rejected final_answer retry still passes but is counted.
Numbers are never taken from the model: they come from the tool results attached to the AgentResult.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from app.agent.runner import get_agent  # noqa: E402
from app.config import settings  # noqa: E402
from app.rendering.render import inr  # noqa: E402
from app.retrieval.azure_search import get_retriever  # noqa: E402
from app.rendering.scrub import find as find_internal, officer_texts  # noqa: E402
from app.tools import claims_engine as E  # noqa: E402
from app.tools.registry import _DECISION, MAX_HEADLINE_SENTENCES, MAX_NEXT_STEPS, MAX_POINT_CHARS, MAX_POINTS, count_sentences  # noqa: E402

SAMPLES = json.load(open(settings.data_dir / "sample_claims.json", encoding="utf-8"))


def demo_documents_claim() -> dict:
    """The claim a customer gets after uploading demo/documents (it has a hospital and a policy number, which the hand-written samples lack)."""
    from app import intake
    s = {"id": "eval", "history": []}
    for name, data in intake.sample_files():
        intake.store(s, name, intake.process_file(name, data))
    out = intake.build(s)
    assert out["status"] == "ready", out["reasons"]
    return s["claim"]


def case_claim(key: str) -> dict:
    return demo_documents_claim() if key == "DEMO_DOCS" else SAMPLES[key]["claim"]
STRIPPED = "Some citations could not be verified"

# ---------------------------------------------------------------------------------------------------------------
# Question set. `claim` loads that sample into the session. Facts in `mention` were checked against the wording
# (C1-a 36 months, C1-b 24 months, C1-c accident exception, E1.6 30 days from discharge, ANX-C-Lite 1% per day).
# ---------------------------------------------------------------------------------------------------------------
QCOVER = ["coverage_answer", "waiting_period_answer"]
QUESTIONS = [
    dict(id="Q01", msg="Is knee replacement covered and what is the waiting period?", types=QCOVER,
         cite_any=["C1-b", "C1-b-list"], mention=[r"24[\s\-‐-―]*months?|two years", r"accident"], tools_any=["search_policy", "get_clause"],
         first_point=[r"joint replacement"]),   # the entry that names the treatment, not an illness that merely sounds related
    dict(id="Q02", msg="The insured met with a road accident 12 days after buying a fresh policy. Does the 30-day waiting period stop us paying?",
         types=QCOVER, cite_any=["C1-c"], mention=[r"accident"]),
    dict(id="Q03", msg="What is the waiting period for pre-existing diseases?", types=QCOVER + ["definition_answer"],
         cite_any=["C1-a"], mention=[r"36[\s\-‐-―]*months?"]),
    dict(id="Q04", msg="Is maternity payable under this policy?", types=["coverage_answer"], verdict=["not_covered", "covered_with_conditions"],
         cite_any=["C2-o"]),
    dict(id="Q05", msg="Are gloves and masks in the bill payable?", types=QCOVER, cite_any=["C3-k", "ANX-B", "B2.3"],
         mention=[r"non-?\s?medical|not payable|non-?payable"]),
    dict(id="Q06", msg="How many days does the insured have to send us the reimbursement documents after discharge?",
         types=QCOVER + ["documents_answer", "definition_answer"], cite_any=["E1.6"], mention=[r"30[\s\-‐-―]*days?"]),
    dict(id="Q07", msg="Which documents do we need to process a reimbursement claim?", types=["documents_answer", "coverage_answer"], cite_any=["E1.7"]),
    dict(id="Q08", msg="What counts as associated medical expenses for the room rent proportion?", types=["definition_answer", "coverage_answer"],
         cite_any=["A1.2-Def5", "B1.1.1-Note-iii"]),
    dict(id="Q09", msg="What is the room rent limit on the Optima Lite plan?", types=["coverage_answer", "definition_answer"],
         cite_any=["ANX-C-Lite", "B2.13"], mention=[r"1\s*%"]),
    # --- with the demo claim TC07 loaded
    dict(id="Q10", claim="TC07", msg="Why was the room rent deducted on this claim?", types=["deduction_explanation"], needs_claim_result=True,
         mention_amounts=("deductions", "room")),
    dict(id="Q11", claim="TC07", msg="Which documents are still missing for this claim?", types=["documents_answer"], needs_claim_result=True,
         mention=[r"prescription"]),
    dict(id="Q12", claim="TC07", msg="What would the payable amount be if the room rent had been 5,000 a day?", types=["claim_assessment"],
         whatif={"room_rate_per_day": 5000}),
    # --- dates given: the waiting-period tool must do the arithmetic
    dict(id="Q13", msg="Policy started 1 March 2025. The insured was admitted on 15 July 2026 for cataract surgery. Has the waiting period been served?",
         types=["waiting_period_answer"], waiting=dict(first_policy_inception="2025-03-01", admission_date="2026-07-15", diagnosis="cataract", procedure="cataract surgery")),
    # --- the wording cannot answer these: the agent must say so, not guess
    dict(id="U1", msg="What was HDFC ERGO's claim settlement ratio last financial year?", types=["insufficient_information"], must_not=[r"\d+(\.\d+)?\s*%"], unanswerable=True),
    dict(id="U2", msg="Is Manipal Hospital Whitefield in our cashless network list?", types=["insufficient_information"], headline_not=[r"^\W*(yes|no)\b"], unanswerable=True),
    dict(id="U3", msg="How much premium has this policyholder paid so far?", types=["insufficient_information"], must_not=[r"₹\s?[\d,]{4,}"], unanswerable=True),
    # --- plain questions with the claim loaded: one short sentence, never an assessment (asked as a customer, claim from the demo documents)
    dict(id="P01", msg="what's my name", mention=[r"Rohan Verma"], **{"plain": True}),
    dict(id="P02", msg="which hospital was I in", mention=[r"Riverside"], **{"plain": True}),
    dict(id="P03", msg="when was I admitted", mention=[r"10 Sep(tember)? 2025"], **{"plain": True}),
    dict(id="P04", msg="how many days did I stay", mention=[r"\b4\b"], **{"plain": True}),
    dict(id="P05", msg="what is my policy number", mention_field=["policy_number"], **{"plain": True}),
    dict(id="P06", msg="what plan do I have", mention=[r"Optima Lite"], **{"plain": True}),
    dict(id="P07", msg="who are you", **{"plain": True}),
    dict(id="P08", msg="hello", **{"plain": True}),
    dict(id="P09", msg="thanks", **{"plain": True}),
    dict(id="P10", msg="what's the weather", must_not=[r"°|degrees|sunny|rainy|forecast"], **{"plain": True}),
]
for _q in QUESTIONS:
    if _q.get("plain"):     # a customer's plain question is answered in code (or by the model) as a direct_answer; general_answer is the officer's type
        _q.update(claim="DEMO_DOCS", types=["direct_answer", "general_answer"])


# ---------------------------------------------------------------------------------------------------------------
def fmt_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b %Y')}"


def cited_keys(res) -> set[str]:
    keys = {c["chunk_key"] for c in res.citations}
    f = res.final or {}
    keys |= set(f.get("citations") or [])
    for p in f.get("points") or []:
        keys |= set(p.get("citations") or [])
    return keys


def chunk_id(key: str) -> str:
    return key.split(":", 1)[1] if ":" in key else key


def claim_result(res):
    """The last deterministic claim assessment behind this answer (a what-if run counts as the last one)."""
    found = [r["data"] for r in res.results.values() if r["kind"] == "claim"]
    return found[-1] if found else None


def common_checks(res, retriever, fails: list[str], notes: list[str]):
    fa = [t for t in res.trace if t["tool"] == "final_answer"]
    if res.status != "ok":
        fails.append(f"turn ended with status {res.status} ({res.latency_ms // 1000}s)")
        return
    if res.final is None:   # the runner only sets .final when final_answer was accepted
        fails.append("final_answer never accepted")
        return
    rejected = sum(1 for t in fa if not t["ok"])
    if rejected:
        why = "; ".join(p[:110] for t in fa if not t["ok"] for p in t.get("problems", []))
        notes.append(f"final_answer rejected {rejected}x then accepted: {why}")
    # compact answer: present, shorter than the full answer, closes with the officer line
    if not res.summary_markdown.strip():
        fails.append("no summary_markdown")
    else:
        customer_format = res.final["answer_type"] in ("general_answer", "direct_answer")   # a customer's direct answer is the reply itself: no officer line, and it is its own summary
        if not customer_format and "The officer decides." not in res.summary_markdown:
            fails.append("summary lacks the 'The officer decides' line")
        if len(res.summary_markdown) >= len(res.markdown) and res.final["answer_type"] not in ("general_answer", "direct_answer", "insufficient_information"):
            fails.append("summary is not shorter than the full answer")
    # length caps on the accepted answer (the validator only asks once, so the harness checks what actually got through)
    f = res.final
    if f["answer_type"] not in ("claim_assessment", "deduction_explanation") and count_sentences(f.get("headline", "")) > MAX_HEADLINE_SENTENCES:
        fails.append(f"headline has {count_sentences(f['headline'])} sentences (max {MAX_HEADLINE_SENTENCES})")
    if len(f.get("points") or []) > MAX_POINTS:
        fails.append(f"{len(f['points'])} points (max {MAX_POINTS})")
    if any(len(p.get("detail", "")) > MAX_POINT_CHARS for p in f.get("points") or []):
        fails.append(f"a point detail is over {MAX_POINT_CHARS} characters")
    if len(f.get("next_steps") or []) > MAX_NEXT_STEPS:
        fails.append(f"{len(f['next_steps'])} next steps (max {MAX_NEXT_STEPS})")
    shown = find_internal(res.markdown.split("### Evidence")[0])
    if shown:
        fails.append(f"internal terms reached the officer: {shown[:3]}")
    raw = [t for _, text in officer_texts(res.final) for t in find_internal(text)]
    if raw:
        notes.append(f"the model's own text contained internal terms {raw[:3]} (scrubbed)")
    for step in res.final.get("next_steps") or []:   # the accepted answer, even if the validator let a second attempt through
        if _DECISION.search(step):
            fails.append(f"next step reads as a decision: {step[:90]}")
    if any(STRIPPED in c for c in res.final.get("caveats") or []):
        fails.append("validator stripped unverifiable citations")
    for k in sorted(cited_keys(res)):
        if retriever.get_by_key(k) is None:
            fails.append(f"citation not in index: {k}")
    errs = [t["tool"] for t in res.trace if not t["ok"] and t["tool"] != "final_answer"]
    if errs:
        why = "; ".join(f"{t['tool']}({json.dumps(t['args'])[:60]}): {t.get('problems', [''])[0][:80]}" for t in res.trace if not t["ok"] and t["tool"] != "final_answer")
        notes.append("tool errors: " + why)


def check_claim_case(cid: str, res, retriever) -> tuple[list[str], list[str]]:
    exp = SAMPLES[cid]["expected"]
    fails, notes = [], []
    common_checks(res, retriever, fails, notes)
    if res.answer_type != "claim_assessment":
        fails.append(f"answer_type {res.answer_type}, expected claim_assessment")
    r = claim_result(res)
    if r is None:
        fails.append("assess_claim was not used")
        return fails, notes
    if r["claim_id"] != SAMPLES[cid]["claim"]["claim_id"]:
        fails.append(f"assessed {r['claim_id']}, not the loaded claim")
    if r["recommendation"] != exp["recommendation"]:
        fails.append(f"recommendation {r['recommendation']}, expected {exp['recommendation']}")
    a = r["amounts"]
    for key in ("estimated_payable_if_docs_supplied", "payable_confirmed_now"):
        if key in exp:
            if abs(a[key] - exp[key]) > 0.5:
                fails.append(f"{key} {a[key]}, expected {exp[key]}")
            elif exp[key] and inr(exp[key]) not in res.markdown:
                fails.append(f"{inr(exp[key])} missing from the rendered answer")
    for k, v in (exp.get("bill_reductions") or {}).items():
        if abs(a["deductions"].get(k, 0) - v) > 0.5:
            fails.append(f"deduction {k} {a['deductions'].get(k)}, expected {v}")
    flagged = {c["code"] for c in r["checks"] if c["status"] in ("violated", "needs_review")}
    flagged |= {d.get("id") or d.get("name") for d in r["documents"]["missing"] + r["documents"]["incomplete"]}
    flagged |= {i.get("field") or i.get("note") for i in r["inconsistencies"]}
    blob = json.dumps([sorted(map(str, flagged)), r["inconsistencies"], r["documents"]["missing"], r["documents"]["incomplete"]])
    for f in exp.get("must_flag") or []:
        if f not in blob:
            fails.append(f"flag {f} not raised")
    summary = res.summary_markdown
    if exp["recommendation"] == "likely_not_payable" and not ("Not payable" in summary and "Likely not payable" in summary):
        fails.append("red flag hidden: not payable is not in the summary")
    if exp["recommendation"] == "needs_human_review" and "Needs human review" not in summary:
        fails.append("red flag hidden: needs human review is not in the summary")
    for i in r["inconsistencies"]:
        if i["field"].replace("_", " ") not in summary:
            fails.append(f"red flag hidden: document conflict '{i['field']}' is not in the summary")
    for key in ("estimated_payable_if_docs_supplied", "payable_confirmed_now"):
        if exp["recommendation"] != "likely_not_payable" and exp.get(key) and inr(exp[key]) not in summary:
            fails.append(f"{inr(exp[key])} is not in the summary")
    have = {chunk_id(k) for k in cited_keys(res)}
    for c in exp.get("must_cite") or []:
        if c not in have:
            fails.append(f"required citation {c} missing")
    tools = [t["tool"] for t in res.trace]
    if tools.count("assess_claim") > 1:
        notes.append("assess_claim called more than once")
    return fails, notes


def check_question(q: dict, res, retriever) -> tuple[list[str], list[str]]:
    fails, notes = [], []
    common_checks(res, retriever, fails, notes)
    if res.answer_type not in q["types"]:
        fails.append(f"answer_type {res.answer_type}, expected one of {q['types']}")
    if q.get("verdict") and (res.final or {}).get("verdict") not in q["verdict"]:
        fails.append(f"verdict {(res.final or {}).get('verdict')}, expected one of {q['verdict']}")
    tools = {t["tool"] for t in res.trace}
    if q.get("tools_any") and not tools & set(q["tools_any"]):
        fails.append(f"none of {q['tools_any']} used")
    if not q.get("unanswerable") and not q.get("claim") and not q.get("waiting") and not q.get("plain") and not tools & {"search_policy", "get_clause", "lookup_non_medical_item"}:
        fails.append("answered without retrieving policy text")
    have = {chunk_id(k) for k in cited_keys(res)}
    if q.get("cite_any") and not have & set(q["cite_any"]):
        fails.append(f"none of {q['cite_any']} cited (cited: {sorted(have) or 'nothing'})")
    if q.get("unanswerable") and (res.final or {}).get("citations"):
        notes.append("cited passages on an unanswerable question")
    for pat in q.get("mention") or []:
        if not re.search(pat, res.markdown, re.I):
            fails.append(f"answer does not mention /{pat}/")
    if q.get("first_point"):
        pts = (res.final or {}).get("points") or []
        first = f"{pts[0].get('label', '')} {pts[0].get('detail', '')}" if pts else ""
        for pat in q["first_point"]:
            if not re.search(pat, first, re.I):
                fails.append(f"first supporting point does not name /{pat}/: {first[:100]}")
    for pat in q.get("headline_not") or []:
        if re.search(pat, (res.final or {}).get("headline", ""), re.I):
            fails.append(f"headline gives a yes/no answer: {(res.final or {}).get('headline', '')[:80]}")
    for pat in q.get("must_not") or []:
        if re.search(pat, res.markdown, re.I):
            fails.append(f"answer matches forbidden /{pat}/")
    if q.get("plain"):
        claim = case_claim(q["claim"])
        for key in q.get("mention_field") or []:
            if str(claim.get(key)) not in res.markdown:
                fails.append(f"the claim's {key} ({claim.get(key)}) is not in the answer")
        if claim_result(res) is not None:
            fails.append("the claim was assessed for a plain question")
        if res.sections:
            fails.append(f"a plain answer has {len(res.sections)} 'Show more' section(s)")
        summary = res.summary_markdown.strip()
        if len(summary) > 220 or count_sentences(summary) > 2:
            fails.append(f"not a short direct answer ({len(summary)} characters, {count_sentences(summary)} sentences): {summary[:90]}")
        if re.search(r"₹1,22,125|Estimated payment|Likely eligible|Main reasons", res.markdown):
            fails.append("assessment content in a plain answer")
        if (res.final or {}).get("citations") or (res.final or {}).get("points"):
            fails.append("a plain answer carries citations or points")
        if any(t["tool"] == "assess_claim" for t in res.trace):
            notes.append("assess_claim was attempted (refused by the backend)")
        if not tools <= {"get_claim_summary", "final_answer", "assess_claim"}:
            notes.append(f"unnecessary tools: {sorted(tools - {'get_claim_summary', 'final_answer', 'assess_claim'})}")
    r = claim_result(res)
    if q.get("needs_claim_result") and r is None:
        fails.append("no assess_claim result behind the answer")
    if q.get("mention_amounts") and r is not None:
        grp, key = q["mention_amounts"]
        want = r["amounts"][grp][key]
        if want and inr(want) not in res.markdown:
            fails.append(f"{inr(want)} ({grp}.{key}) missing from the answer")
    if q.get("whatif"):
        base = SAMPLES[q["claim"]]["claim"]
        oracle = E.assess(E.apply_what_if(base, q["whatif"]))
        if r is None:
            fails.append("assess_claim was not used")
        else:
            if not r.get("what_if"):
                fails.append("assess_claim was called without the what_if")
            for key in ("estimated_payable_if_docs_supplied", "payable_confirmed_now"):
                if abs(r["amounts"][key] - oracle["amounts"][key]) > 0.5:
                    fails.append(f"{key} {r['amounts'][key]} differs from the engine's {oracle['amounts'][key]} for the same what-if")
    if q.get("waiting"):
        w = q["waiting"]
        mini = dict(first_policy_inception=w["first_policy_inception"], admission_datetime=w["admission_date"] + "T00:00",
                    discharge_datetime=w["admission_date"] + "T23:59", diagnosis=w["diagnosis"], procedure=w["procedure"],
                    is_accident=False, pre_existing=False, prior_continuous_coverage_months=0)
        oracle = E.check_waiting_period(mini)
        waits = [x["data"] for x in res.results.values() if x["kind"] == "waiting"]
        if not waits:
            fails.append("check_waiting_period was not used (dates must not be computed by the model)")
        else:
            got = {c["code"]: c["status"] for c in waits[-1]["checks"]}
            for c in oracle:
                if c["code"] in got and got[c["code"]] != c["status"]:
                    fails.append(f"{c['code']} status {got[c['code']]}, engine says {c['status']}")
        for c in oracle:
            if c.get("eligible_from") and fmt_date(c["eligible_from"]) not in res.markdown:
                fails.append(f"eligible-from date {fmt_date(c['eligible_from'])} missing from the answer")
    return fails, notes


# ---------------------------------------------------------------------------------------------------------------
NEEDS_TRANSCRIPT = ("final_answer rejected", "tool errors", "rate limited")


def build_transcript(label: str, run_no: int, msg: str, res, fails: list[str], notes: list[str], secs: float) -> str:
    """Enough to diagnose a flake without rerunning it. Tool arguments are shown as key names only (claim data can sit in values);
    final_answer arguments are shown in full because they are what the model decided to say. No environment values are included."""
    out = [f"# {label} · run {run_no} · {'FAILED' if fails else 'passed, but needed a retry'}", "",
           f"- **Question:** {msg}", f"- **Status:** {getattr(res, 'status', 'error')} · **answer type:** {getattr(res, 'answer_type', '-')} · **seconds:** {secs}"]
    if fails:
        out += ["", "## Failed checks"] + [f"- {f}" for f in fails]
    if notes:
        out += ["", "## Notes"] + [f"- {n}" for n in notes]
    trace = list(getattr(res, "trace", []) or [])
    out += ["", "## Tools called, in order"]
    for i, t in enumerate(trace, 1):
        keys = f"argument keys: {sorted((t.get('args') or {}).keys())}" if t["tool"] != "final_answer" else "arguments below"
        state = "ok" if t["ok"] else "REJECTED/ERROR"
        out.append(f"{i}. `{t['tool']}` · {state} · {t.get('ms', '?')} ms · {keys}")
        for p in t.get("problems") or []:
            out.append(f"   - problem: {p[:400]}")
    finals = [t for t in trace if t["tool"] == "final_answer"]
    if finals:
        out += ["", "## final_answer arguments, every attempt"]
        for i, t in enumerate(finals, 1):
            out += [f"**Attempt {i}** ({'accepted' if t['ok'] else 'rejected'})", "```json", json.dumps(t.get("args"), ensure_ascii=False, indent=2)[:3000], "```"]
    md = getattr(res, "markdown", "")
    if md:
        out += ["", "## Rendered answer the officer would see", "", "```markdown", md[:6000], "```"]
    return "\n".join(out) + "\n"


def run_case(kind: str, case, agent, retriever, run_no: int) -> dict:
    if kind == "claim":
        cid, msg = case, "Assess this claim"
        claim = SAMPLES[cid]["claim"]
        label = f"{cid} {SAMPLES[cid]['title'][:44]}"
    else:
        cid, msg, claim = case["id"], case["msg"], case_claim(case["claim"]) if case.get("claim") else None
        label = f"{cid} {msg[:52]}"
    session = dict(uin=(claim or {}).get("policy_uin") or settings.default_uin, claim=claim, history=[])
    if kind != "claim" and case.get("plain"):
        session["audience"] = "customer"
    t0, throttled, res = time.time(), 0, None
    try:
        for attempt in range(4):   # a 429 from the model deployment is quota, not agent behaviour: wait and rerun the turn (fresh conversation)
            try:
                res = agent.ask(session, msg)
                if res.status == "unavailable" and attempt < 3:   # the backend already retried 429/5xx with backoff; wait longer and rerun the turn
                    throttled += 1
                    time.sleep(25 * (attempt + 1))
                    continue
                break
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ != "RateLimitError" or attempt == 3:
                    raise
                throttled += 1
                time.sleep(25 * (attempt + 1))
        fails, notes = check_claim_case(cid, res, retriever) if kind == "claim" else check_question(case, res, retriever)
        if throttled:
            notes.append(f"rate limited (429) {throttled}x, retried")
        trace = " > ".join(t["tool"] for t in res.trace)
        atype, md = res.answer_type, res.markdown
    except Exception as e:  # noqa: BLE001 - one bad run must not stop the evaluation
        fails, notes, trace, atype, md = [f"ERROR {type(e).__name__}: {str(e)[:160]}"], [], "", "-", ""
    secs = round(time.time() - t0, 1)
    transcript = build_transcript(label, run_no, msg, res, fails, notes, secs) if (fails or any(n.startswith(NEEDS_TRANSCRIPT) for n in notes)) else None
    return dict(id=cid, label=label, run=run_no, ok=not fails, fails=fails, notes=notes, trace=trace, answer_type=atype,
                secs=secs, markdown=md, transcript=transcript)


def cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def write_report(path: Path, runs: list[dict], repeat: int):
    by: dict[str, list[dict]] = {}
    for r in runs:
        by.setdefault(r["id"], []).append(r)
    order = list(dict.fromkeys(r["id"] for r in runs))
    passed = sum(1 for i in order if all(r["ok"] for r in by[i]))
    flaky = [i for i in order if 0 < sum(r["ok"] for r in by[i]) < len(by[i])]
    failing = [i for i in order if not any(r["ok"] for r in by[i])]
    total_ok, total = sum(r["ok"] for r in runs), len(runs)
    md = ["# Agent evaluation report", "",
          f"Agent `{settings.agent_name}` · model `{settings.model_deployment}` · retriever `{settings.retriever}` · mode `{settings.agent_mode}` · "
          f"{len(order)} cases × {repeat} run(s) · generated {time.strftime('%Y-%m-%d %H:%M')}", "",
          f"**{passed}/{len(order)} cases passed every run · {total_ok}/{total} runs passed**"
          + (f" · flaky: {', '.join(flaky)}" if flaky else " · no flaky cases")
          + (f" · failing every run: {', '.join(failing)}" if failing else ""), "",
          "| Case | Passed | Answer type | Tool trace (last run) | Seconds | Problems |", "|---|---|---|---|---|---|"]
    for i in order:
        rs, last = by[i], by[i][-1]
        probs = sorted({f for r in rs for f in r["fails"]}) + sorted({"(note) " + n for r in rs for n in r["notes"]})
        mark = "✅" if all(r["ok"] for r in rs) else ("⚠️ flaky" if any(r["ok"] for r in rs) else "❌")
        md.append(f"| {cell(last['label'])} | {mark} {sum(r['ok'] for r in rs)}/{len(rs)} | {last['answer_type']} | {cell(last['trace'])} | "
                  f"{sum(r['secs'] for r in rs) / len(rs):.0f} | {cell('; '.join(probs)) or '–'} |")
    md += ["", "How to read this: a case passes a run only if every check passed. Amounts come from the deterministic engine (via the tool results), "
           "citations are looked up in the retriever, and `final_answer` must have been accepted. The 12 claim cases use the hand-derived expectations in "
           "`data/sample_claims.json`. Question cases check answer type, required citations, facts that must appear, and engine numbers for the what-if and "
           "dated questions. Notes marked (note) do not fail a run."]
    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return flaky, failing, passed, len(order)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeat", type=int, default=1, help="runs per case; use 3 to find flaky cases")
    ap.add_argument("--only", help="comma list of case ids, e.g. TC07,Q01")
    ap.add_argument("--suite", choices=["all", "claims", "questions"], default="all")
    ap.add_argument("--workers", type=int, default=3, help="parallel agent turns (lower it if you see 429 errors)")
    ap.add_argument("--report", default=str(ROOT / "docs" / "evidence" / "eval_report.md"))
    ap.add_argument("--failures-dir", default=str(ROOT / "docs" / "evidence" / "eval_failures"),
                    help="a transcript of every failed or retried run is saved here (tools, argument keys, final_answer arguments, rendered answer)")
    ap.add_argument("--verbose", action="store_true", help="print the rendered answer of every failing run")
    ap.add_argument("--offline", action="store_true", help="harness self-check with the keyword stand-in agent (needs RETRIEVER=local AGENT_MODE=offline); not a measure of the real agent")
    args = ap.parse_args()
    if args.offline and settings.agent_mode != "offline":
        sys.exit("--offline needs the stand-in agent: run  RETRIEVER=local AGENT_MODE=offline python scripts/eval/eval_agent.py --offline")
    if not args.offline and (settings.agent_mode != "foundry" or settings.retriever != "azure"):
        sys.exit("Run against the real agent with  RETRIEVER=azure AGENT_MODE=foundry python scripts/eval/eval_agent.py")

    only = set(args.only.split(",")) if args.only else None
    cases = []
    if args.suite in ("all", "claims"):
        cases += [("claim", cid) for cid in SAMPLES]
    if args.suite in ("all", "questions"):
        cases += [("question", q) for q in QUESTIONS]
    cases = [(k, c) for k, c in cases if not only or (c if k == "claim" else c["id"]) in only]
    if not cases:
        sys.exit("No cases selected.")

    from app.observability import configure_logging
    configure_logging()   # one JSON line per turn on stderr: `2> turns.jsonl` keeps latency and retry data for later
    agent, retriever = get_agent(), get_retriever()
    jobs = [(k, c, n) for n in range(1, args.repeat + 1) for k, c in cases]
    print(f"{len(cases)} cases x {args.repeat} run(s), retriever={settings.retriever}, agent={type(agent).__name__}", flush=True)
    runs, saved, stamp = [], 0, time.strftime("%Y%m%d-%H%M")
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for r in ex.map(lambda j: run_case(j[0], j[1], agent, retriever, j[2]), jobs):
            runs.append(r)
            if r["transcript"]:
                fdir = Path(args.failures_dir)
                fdir.mkdir(parents=True, exist_ok=True)
                (fdir / f"{stamp}_{r['id']}_run{r['run']}.md").write_text(r["transcript"], encoding="utf-8")
                saved += 1
            print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['label'][:60]:60} run {r['run']}  {r['secs']:>5}s  {r['trace']}", flush=True)
            for f in r["fails"]:
                print(f"        - {f}")
            for n in r["notes"]:
                print(f"        · {n}")
            if args.verbose and not r["ok"] and r["markdown"]:
                print("        " + r["markdown"][:1500].replace("\n", "\n        "))
    flaky, failing, passed, n = write_report(Path(args.report), runs, args.repeat)
    if saved:
        print(f"\n{saved} transcript(s) of failed or retried runs saved in {args.failures_dir}")
    print(f"\n{passed}/{n} cases passed every run. flaky: {flaky or 'none'}. failing every run: {failing or 'none'}. Report: {args.report}")
    sys.exit(0 if passed == n else 1)


if __name__ == "__main__":
    main()
