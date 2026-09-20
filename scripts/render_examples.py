"""Format demonstrations for each answer type.

The final_answer payloads below are written by hand to show what a well-behaved model sends. Everything else
(retrieval, waiting-period maths, claim numbers, citations, formatting) is produced by the real code path.
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings
from app.retrieval.azure_search import get_retriever
from app.tools.registry import TurnContext, call_tool, render_final

samples = json.load(open(settings.data_dir / "sample_claims.json"))
out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "demo" / "examples"   # optional argument: another output folder
out_dir.mkdir(parents=True, exist_ok=True)


def new_ctx(claim=None):
    ctx = TurnContext(session=dict(uin=settings.default_uin, claim=claim, history=[]), retriever=get_retriever())
    ctx.length_caps_rejected = True   # these hand-written payloads are longer than the caps a live model is held to; show the full layout anyway
    return ctx


def key(ctx, clause_ref):
    return call_tool("get_clause", {"clause_ref": clause_ref}, ctx)["clauses"][0]["chunk_key"]


def finish(name, ctx, final):
    res = call_tool("final_answer", final, ctx)
    assert res == {"status": "accepted"}, res
    (out_dir / f"{name}.md").write_text(render_final(ctx.final, ctx).markdown, encoding="utf-8")
    print("wrote", name)


# 1. coverage question: knee replacement
c = new_ctx()
finish("Q_coverage_knee_replacement", c, dict(
    answer_type="coverage_answer", verdict="covered_with_conditions",
    headline="Knee replacement is covered in principle, but it sits on the specified-procedure list, so it is not payable until 24 months of continuous cover are complete unless it results from an accident.",
    points=[dict(label="Listed procedure", status="warning", detail="Joint replacement surgeries are on the specified list, which carries a 24-month waiting period (Excl02).", citations=[key(c, "C.1.b.vi")]),
            dict(label="Accident exception", status="ok", detail="The waiting period does not apply to claims arising from an accident.", citations=[key(c, "C.1.b")]),
            dict(label="Pre-existing disease", status="warning", detail="If the knee condition is pre-existing, the longer of the two waiting periods applies (36 months by default).", citations=[key(c, "C.1.a")]),
            dict(label="Room rent", status="info", detail="Room rent is payable up to the limit in the Policy Schedule for the plan.", citations=[key(c, "B.1.1")])],
    next_steps=["Ask for the policy's first inception date and the admission date, then run the waiting-period check.", "Ask whether the knee condition was diagnosed or treated before the policy started."],
    caveats=["Which plan applies (and its room-rent limit) comes from the Policy Schedule."]))

# 2. waiting period with dates (deterministic table)
c = new_ctx()
w = call_tool("check_waiting_period", dict(first_policy_inception="2024-11-01", admission_date="2025-09-20", procedure="Laparoscopic cholecystectomy"), c)
finish("Q_waiting_period_cholecystectomy", c, dict(
    answer_type="waiting_period_answer", result_id=w["result_id"],
    headline="Not yet. Cholecystectomy is on the specified list, which needs 24 months of continuous cover, and only 10 months have been completed.",
    next_steps=["If the surgery follows an accident, the waiting period does not apply."],
    citations=[key(c, "C.1.b")]))

# 3. deduction explanation
c = new_ctx(samples["TC07"]["claim"])
r = call_tool("assess_claim", {}, c)
finish("Q_why_room_rent_deducted", c, dict(answer_type="deduction_explanation", result_id=r["result_id"], focus="room", headline="x"))

# 4. what-if
c = new_ctx(samples["TC07"]["claim"])
r = call_tool("assess_claim", {"what_if": {"protect_benefit_opted": True}}, c)
finish("Q_whatif_protect_benefit", c, dict(answer_type="claim_assessment", result_id=r["result_id"], headline="x",
                                             caveats=["The Protect Benefit would need to be opted at inception or renewal, so confirm the Policy Schedule."]))

# 5. documents
c = new_ctx(samples["TC07"]["claim"])
r = call_tool("assess_claim", {}, c)
finish("Q_documents_missing", c, dict(answer_type="documents_answer", result_id=r["result_id"],
                                       headline="One document is incomplete: the prescription that supports the pharmacy bills.",
                                       next_steps=["Ask the claimant for the prescription. The medicines line stays on hold until it arrives."]))

# 6. definition
c = new_ctx()
finish("Q_definition_room_rent", c, dict(
    answer_type="definition_answer",
    headline="Room rent is what the hospital charges for the room and boarding, and the policy counts the associated medical expenses with it.",
    points=[dict(label="Definition", status="info", detail="Room Rent means the amount charged by a hospital for room and boarding, including the associated medical expenses.", citations=[key(c, "A.1.1 Def. 41")]),
            dict(label="Associated medical expenses", status="info", detail="Consultation fees, operation theatre, surgical appliances and nursing, anaesthesia, blood and oxygen. Pharmacy, consumables, implants and diagnostics are excluded from this group.", citations=[key(c, "A.1.2 Def. 5")]),
            dict(label="Why it matters", status="warning", detail="If the patient takes a room above the plan limit, room rent and the associated expenses are reduced in the same proportion.", citations=[key(c, "B.1.1.1 Note iii")])]))

# 7. insufficient information
c = new_ctx()
call_tool("search_policy", {"query": "claim settlement ratio"}, c)
finish("Q_insufficient_information", c, dict(
    answer_type="insufficient_information", verdict="insufficient_information",
    headline="The policy wording does not say what the insurer's claim settlement ratio is.",
    next_steps=["Check the insurer's public disclosures (the Form NL-37 payout ratio) or ask the claims officer."],
    caveats=["I only answer from the policy wording and the claim documents provided."]))
