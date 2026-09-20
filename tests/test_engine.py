import json, pytest
from app.config import settings
from app.tools.claims_engine import assess, lookup_non_medical_item, apply_what_if
from app.tools.evidence import resolve

SAMPLES = json.load(open(settings.data_dir / "sample_claims.json"))


@pytest.mark.parametrize("sid", sorted(SAMPLES))
def test_sample_matches_hand_derived_expectation(sid):
    s = SAMPLES[sid]
    r, exp = assess(s["claim"]), s["expected"]
    assert r["recommendation"] == exp["recommendation"]
    for k in ("estimated_payable_if_docs_supplied", "payable_confirmed_now"):
        if k in exp:
            assert r["amounts"][k] == pytest.approx(exp[k], abs=0.01)
    if "bill_reductions" in exp:
        d = r["amounts"]["deductions"]
        for k, v in exp["bill_reductions"].items():
            assert d[k] == pytest.approx(v, abs=0.01)


def test_what_if_prescription_supplied_releases_hold():
    base = SAMPLES["TC07"]["claim"]
    fixed = apply_what_if(base, {"documents": {"pharmacy_bills_prescription": {"present": True, "complete": True}}, "prescription_missing": False})
    r = assess(fixed)
    assert r["recommendation"] == "likely_eligible" and r["amounts"]["payable_confirmed_now"] == 122125


def test_non_medical_lookup():
    assert lookup_non_medical_item("surgical gloves")["listed_in_annexure_b"]
    assert not lookup_non_medical_item("MRI scan")["listed_in_annexure_b"]


def test_every_evidence_ref_resolves_to_a_real_chunk():
    ids = {json.loads(l)["chunk_id"] for l in open(settings.data_dir / "policy_clauses.jsonl")}
    for sid, s in SAMPLES.items():
        for ref in assess(s["claim"])["evidence_refs"]:
            got = resolve(ref)
            assert got, f"{sid}: unresolved reference {ref}"
            assert all(g in ids for g in got), f"{sid}: {ref} -> {got} not in chunk file"


@pytest.mark.parametrize("sid", sorted(SAMPLES))
def test_rendered_assessment_cites_every_clause_the_sample_says_it_must(sid):
    """The backend renders the evidence itself, so this holds whatever the model puts in its own citations."""
    from app.rendering.render import render_claim_assessment
    from app.retrieval.azure_search import get_retriever
    out = render_claim_assessment(assess(SAMPLES[sid]["claim"]), get_retriever())
    have = {c["chunk_key"].split(":", 1)[1] for c in out.citations}
    missing = [c for c in SAMPLES[sid]["expected"].get("must_cite", []) if c not in have]
    assert not missing, f"{sid}: {missing} not cited in the rendered answer"
