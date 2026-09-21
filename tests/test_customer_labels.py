"""P1-1: a customer sees plain names instead of Excl codes, annexure letters, clause numbers and "Def. n"; the officer wording (locked by golden files) is unchanged."""
import json
import re

import pytest

from app.agent.runner import OfflineAgent
from app.config import settings
from app.rendering.customer_labels import clause_name, customerize
from tests.test_plain_questions import Script, agent

SAMPLES = json.load(open(settings.data_dir / "sample_claims.json", encoding="utf-8"))
# the labels a customer must never see: Excl01..18, Annexure A..D, a clause number (C.1.c, B.1.1.1 Note iii, A1.2 Def. 5), "Def. n", and the insurer wording
CODE = re.compile(r"\bExcl\d{2}\b|Annexure\s*[A-D]\b|(?<![\w.])[A-E]\.\d+|(?<![\w.])[A-E]\d+(?:\.\d+)*\s+(?:Def|Note)\b|\bDef\.|insurer payment", re.I)
AMOUNT = re.compile(r"₹[\d,]+")


def visible(res) -> list[tuple[str, str]]:
    """Every text a customer can be shown for an answer: summary, full answer, each Show more section, each reference popup."""
    out = [("summary", res.summary_markdown), ("answer", res.markdown)]
    out += [(f"section {s['id']}", s["title"] + "\n" + s["markdown"]) for s in res.sections]
    out += [(f"reference {c['label']}", " | ".join(str(c[k]) for k in ("label", "clause", "citation", "excerpt"))) for c in res.citations]
    return out


def ask(audience, claim_id, message):
    s = {"id": "s", "claim": SAMPLES[claim_id]["claim"] if claim_id else None, "uin": settings.default_uin, "history": [], "audience": audience}
    return OfflineAgent().ask(s, message)


def assert_no_codes(res, where):
    for name, text in visible(res):
        assert not CODE.search(text), f"{where}: {name}: {CODE.findall(text)[:3]} in ...{text[max(CODE.search(text).start() - 40, 0):CODE.search(text).end() + 40]!r}"


# ---------------------------------------------------------------- the rewriting itself
@pytest.mark.parametrize("before,after", [
    ("Policy B.1.1.1 Note iii, p.12; A.1.2 Def. 5, p.8; Annexure C, p.50",
     "Policy rule: room rent, p.12; Policy definition: associated medical expenses, p.8; Your plan's benefit chart, p.50"),
    ("**Evidence:** Policy C.1.c, p.30", "**Evidence:** Policy rule: 30-day waiting period, p.30"),
    ("- 30-day waiting period (Excl03): not applicable.", "- 30-day waiting period: not applicable."),
    ("- Specified disease/procedure (Excl02): not applicable", "- Specified disease/procedure: not applicable"),
    ("- Pre-existing disease (Excl01): 36 months required", "- Pre-existing disease: 36 months required"),
    ("Specified illnesses and surgical procedures (24-month waiting period, Excl02)", "Specified illnesses and surgical procedures (24-month waiting period)"),
    ("30-day waiting period: Code – Excl03 i. Expenses related to the treatment", "30-day waiting period: i. Expenses related to the treatment"),
    ("These are listed in Annexure B as non-medical items, and the Protect Benefit is not in force.", "These are on your policy's list of non-medical items, and the Protect Benefit is not in force."),
    ("| Item | Billed | Annexure B |", "| Item | Billed | List no. |"),
    ("Estimated insurer payment: **₹1,22,125**", "Estimated payment: **₹1,22,125**"),
    ("Policy Annexure B, p.49", "Your policy's list of non-medical items, p.49"),
    ("1. **Policy B.1.1, p.11** — “quote”", "1. **Policy section: hospitalization cover, p.11** — “quote”"),
    ("A1.2 Def. 5", "Policy definition: associated medical expenses"),
    ("B.1.1.1 Note iii p.12", "Policy rule: room rent p.12"),
])
def test_labels_become_plain_names(before, after):
    assert customerize(before) == after


def test_unlisted_clauses_are_named_after_their_section():
    assert clause_name("C.1.z") == "Policy section: waiting periods" and clause_name("C.2.l") == "Policy section: exclusions"
    assert clause_name("D.1.19") == "Policy section: general conditions" and clause_name("E.1.9") == "Policy section: claims"
    assert clause_name("A.1.2 Def. 77") == "Policy definition"


def test_medical_codes_and_ordinary_text_are_left_alone():
    text = "Diagnosis E11.9 and C34.1, claim CLM-20250910-0001, policy SYN-2805-0000-0001, room ₹8,000/day, 10 Sep 2025 at 14:30."
    assert customerize(text) == text and customerize("") == "" and customerize("Your name on this claim is Rohan Verma.") == "Your name on this claim is Rohan Verma."


def test_it_is_idempotent():
    once = customerize("Policy C.1.c, p.30; Annexure B; Excl03; Estimated insurer payment")
    assert customerize(once) == once and not CODE.search(once)


def test_the_full_estimate_block_keeps_its_columns():
    block = "```text\nHospital bill                            ₹1,84,500\nNon-payable items (Annexure B)            −₹12,500\nEstimated insurer payment                ₹1,22,125\n```"
    lines = [l for l in customerize(block).split("\n") if "₹" in l]
    assert len(lines) == 3 and len({len(l) for l in lines}) == 1                     # amounts still end in the same column
    assert "Annexure" not in "".join(lines) and "Estimated payment" in "".join(lines)


# ---------------------------------------------------------------- whole answers, customer against officer
@pytest.mark.parametrize("claim_id", list(SAMPLES))
@pytest.mark.parametrize("message", ["Assess this claim", "Why was my room rent reduced?", "What documents are missing?"])
def test_a_customer_answer_has_no_code_labels_in_any_text(claim_id, message):
    assert_no_codes(ask("customer", claim_id, message), f"{claim_id} / {message}")


@pytest.mark.parametrize("message", ["What does room rent mean?", "Is knee replacement covered?", "Are gloves and masks payable?", "waiting period for pre-existing diseases",
                                     "Which documents do I need for a reimbursement claim?"])
def test_a_customer_policy_answer_has_no_code_labels(message):
    res = ask("customer", None, message)
    assert res.citations, message                                                    # the references and their excerpts are what is being checked
    assert_no_codes(res, message)


def test_a_scripted_coverage_and_waiting_answer_has_no_code_labels():
    coverage = {"answer_type": "coverage_answer", "headline": "Covered with conditions (Excl02, see C.1.b; accidents are excepted).", "verdict": "covered_with_conditions",
                "points": [dict(label="Waiting period", status="warning", detail="24 months under Excl02, Annexure B not relevant, Def. 30", citations=[])]}
    chunk = "optima-secure-v062425:C1-b"
    coverage["points"][0]["citations"] = [chunk]
    s = {"id": "s", "claim": SAMPLES["TC03"]["claim"], "uin": settings.default_uin, "history": [], "audience": "customer"}
    model = Script([[("get_clause", {"clause_ref": "C.1.b"})], [("final_answer", coverage)]])
    res = agent(model).ask(s, "Is cataract surgery covered?")
    assert res.answer_type == "coverage_answer" and res.citations
    assert_no_codes(res, "scripted coverage")
    waiting = {"answer_type": "waiting_period_answer", "headline": "Not yet served.", "result_id": None}
    model = Script([[("check_waiting_period", {"first_policy_inception": "2025-03-01", "admission_date": "2026-07-15", "procedure": "cataract surgery"})],
                    [("final_answer", lambda m: dict(waiting, result_id=m.rid))]])
    res = agent(model).ask(dict(s, claim=None), "Policy started 1 March 2025, admitted 15 July 2026 for cataract surgery. Has the waiting period been served?")
    assert res.answer_type == "waiting_period_answer" and res.sections
    assert_no_codes(res, "scripted waiting period")


@pytest.mark.parametrize("claim_id", ["TC02", "TC03", "TC07", "TC12"])
def test_customer_and_officer_answers_differ_only_in_labels(claim_id):
    off, cus = ask("officer", claim_id, "Assess this claim"), ask("customer", claim_id, "Assess this claim")
    assert [s["id"] for s in off.sections] == [s["id"] for s in cus.sections] and len(off.citations) == len(cus.citations)
    assert [c["chunk_key"] for c in off.citations] == [c["chunk_key"] for c in cus.citations]      # the internal keys are untouched
    for (n1, a), (n2, b) in zip(visible(off), visible(cus)):
        assert AMOUNT.findall(a) == AMOUNT.findall(b), (claim_id, n1)                             # relabelled only: no amount changes
        assert re.findall(r"\d+ months?|\d+ days?|\d+(?:\.\d+)?%", a) == re.findall(r"\d+ months?|\d+ days?|\d+(?:\.\d+)?%", b), (claim_id, n1)
    assert "Estimated payment" in "".join(t for _, t in visible(cus)) or cus.answer_type != "claim_assessment"


def test_the_officer_wording_is_unchanged():
    res = ask("officer", "TC07", "Assess this claim")
    text = "\n".join(t for _, t in visible(res))
    for label in ("Excl03", "Annexure B", "C.1.c", "A.1.2 Def. 5", "Estimated insurer payment"):
        assert label in text, label
    assert "Policy rule:" not in text and "List no." not in text


def test_an_officer_session_never_goes_through_the_rewriting(monkeypatch):
    import app.tools.registry as registry
    monkeypatch.setattr(registry, "customerize_rendered", lambda r: (_ for _ in ()).throw(AssertionError("called for an officer")))
    assert ask("officer", "TC07", "Assess this claim").answer_type == "claim_assessment"


def test_the_customer_summary_says_estimated_payment():
    cus = ask("customer", "TC07", "Why was my room rent reduced?")
    assert "Estimated payment" in cus.summary_markdown and "insurer" not in cus.summary_markdown.lower()
