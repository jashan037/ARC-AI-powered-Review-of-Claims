# Demo script: the claims-officer assistant in 5 minutes

**One sentence for the audience:** an AI assistant that answers policy questions with exact clause citations and assesses a claim in a fixed layout, while every number comes from code and the claims officer always decides.

**Verified** on 20 Sep 2026 against the real Azure agent (`claims-adjudication-agent-v2`, version 5, gpt-5-mini, index `claims-kb-v2`): all 8 steps below passed in `scripts/demo_check.py` after the last change (129 s of total agent time; 13 to 24 seconds per answer), so talk while it works.

## Before you start (2 minutes, do it beforehand)

```bash
az login                       # same account as the subscription, if your session expired
source .venv/bin/activate
RETRIEVER=azure AGENT_MODE=foundry python scripts/demo_check.py     # about 3 minutes; you want "8/8 steps passed"
```

Open three terminals in the repo folder, each with the real agent switched on (the prefix is redundant while `.env` says `azure` and `foundry`, and protects you if it does not):

| Terminal | Command | Used for |
|---|---|---|
| A | `RETRIEVER=azure AGENT_MODE=foundry python scripts/chat_cli.py` | steps 1 to 3 (no claim loaded) |
| B | `RETRIEVER=azure AGENT_MODE=foundry python scripts/chat_cli.py --claim TC07` | steps 4 to 7 (the demo claim) |
| C | `RETRIEVER=azure AGENT_MODE=foundry python scripts/chat_cli.py --claim TC02` | step 8 |

After each answer the CLI prints `[tools: ...]`, the tools the agent called. Point at it: for a claim it is always `assess_claim > final_answer`, so the numbers came from the engine.

Wording of question-and-answer replies changes a little between runs. The claim numbers, the recommendation and the cited clauses do not.

## The 8 steps

| # | Terminal | You type | What the audience should see | Key numbers and citations |
|---|---|---|---|---|
| 1 | A | `Is knee replacement covered and what is the waiting period?` | **Covered, with conditions.** The first point says **"Joint replacement surgeries"** are listed among the specified procedures excluded for 24 months. Then the **accident exception**, the effect of a sum-insured enhancement, portability, and the 30-day initial period. "What to check next" asks the officer to verify inception date, accident, and enhancement. | **24 months** (Excl02), 30 days (Excl03), accidents exempt. The first point cites **C.1.b and C.1.b.vi**, later points C.1.c and A.1.2 Def. 30, each with its page. The Evidence quote for C.1.b.vi is centred on the entry: "...Hydrocele/Rectocele - **Joint replacement surgeries** - Surgery for nasal septum deviation...". |
| 2 | A | `What was HDFC ERGO's claim settlement ratio last financial year?` | **Insufficient information.** It says the wording does not contain it and points to the annual report and IRDAI disclosures. It does not make up a percentage. | No percentage anywhere. This is the "it knows what it does not know" moment. |
| 3 | A | `Policy started 1 March 2025. The insured was admitted on 15 July 2026 for cataract surgery. Has the waiting period been served?` | **Not served.** A table of the three waiting periods: only the specified-procedure one applies and it is not yet satisfied. "What to check next" lists things to verify (inception date, portability, accident, endorsements), never a decision. | **16 months (501 days)** of 24 required. Served from **1 Mar 2027**. The dates come from the waiting-period tool, not the model. |
| 4 | B | `Assess this claim` | The full fixed-layout assessment for the appendectomy claim. Scroll to the box at the bottom. | **Likely eligible, pending documents.** Bill **₹1,84,500**. Room-rent adjustment **−₹12,000**, associated-expense adjustment **−₹37,875**, non-payable Annexure B items **−₹12,500** (12 items). Estimated payment **₹1,22,125**, of which **₹20,500 held** for documents, so **₹1,01,625 confirmed today**. Next step: request the prescription. Cites B.1.1.1 Note iii, A.1.2 Def. 5, C.3.k with Annexure B, the plan's Annexure C entry, E.1.7. |
| 5 | B | `Why was the room rent deducted on this claim?` | A numbered walk-through of the proportionate deduction. | Limit 1% of ₹5,00,000 = **₹5,000/day**. Billed **₹8,000/day for 4 days**. Proportion **62.5%**. Room ₹32,000 → ₹20,000 (**−₹12,000**). Associated ₹1,01,000 → ₹63,125 (**−₹37,875**). Not reduced: ₹39,000 of pharmacy, consumables and diagnostics. Ends at ₹1,22,125 (₹1,01,625 confirmed). Cites B.1.1.1 Note iii, A.1.2 Def. 5. |
| 6 | B | `Which documents are still missing for this claim?` | A checklist with eight ticks and one warning. | Only **pharmacy bills with prescription: prescription missing**. Cites E.1.7. Ties back to the ₹20,500 held in step 4. |
| 7 | B | `What would the payable amount be if the room rent had been 5,000 a day?` | The same layout under a banner **"What-if scenario, not the claim as submitted"**, with room rent now within the limit. | Estimated **₹1,72,000**, still **₹20,500 held**, so **₹1,51,500 confirmed**. The ₹49,875 difference from step 4 is exactly the room (₹12,000) plus associated (₹37,875) deductions. The ₹12,500 of non-payable items remains. |
| 8 | C | `Assess this claim` | A clearly negative outcome, and the assistant refuses to make it final. | **Likely not payable.** Bill **₹28,000 → ₹0**. Only **14 days** since first inception against the 30-day waiting period (Excl03, cites C.1.c). Bill review not performed. Next step: **"Confirm the exclusion with the claims officer before communicating a rejection."** |

### What to say at the end (20 seconds)
The model chooses tools and writes the explanation. Python does all dates and money. A validator rejects any citation the tools did not actually return, and a fixed template prints the answer, so the format never drifts. Retrieval is Azure AI Search (hybrid plus semantic ranking) over 186 clause-level chunks of the policy wording. Every answer says it is AI-assisted and that the officer decides.

## If something goes wrong on stage

- **"This is taking longer than expected" or "temporarily unavailable"** is the timeout and rate-limit path working as designed (60-second turn deadline). Nothing was assessed. Just ask again. The gpt-5-mini deployment has a small per-minute quota, so do not rush the steps.
- **A sentence is worded differently from this script.** Expected. Check the numbers and citations, not the prose.
- **No internet or Azure problem.** Show the saved outputs instead: `examples/TC07_demo_claim_prescription_missing.md` (step 4), `examples/Q_why_room_rent_deducted.md` (step 5), `examples/Q_whatif_protect_benefit.md` (a what-if), `examples/TC02_30-day_waiting_period_not_met.md` (step 8). `RETRIEVER=local AGENT_MODE=offline python scripts/chat_cli.py --claim TC07` runs the offline stand-in, which is a keyword router and not the real agent, so say so if you use it. (Your `.env` currently selects the real agent, so the prefix matters.)

## Known rough edges (say them before someone asks)

- **Wording varies between runs.** Over the last full passes a few runs drifted on policy questions (an omitted accident exception, and once the accident-within-30-days question typed "Insufficient information"; the latter was fixed by the prompt change in agent version 5 and passed 10 of 10 afterwards). On Q01 the first point names the joint-replacement entry in 9 of 10 runs; in the tenth the rule comes first and the entry second. The 8 demo steps themselves passed every time. Transcripts are in `examples/eval_failures/`.
- **Step 3** can end a next step with "Refer to the waiting-period check result (result_id) ...", which mentions an internal id. Ignore it if it appears; the date in the table is what to show.
- **Next steps are checks, not decisions.** The model is told to phrase them as things to check, verify, confirm, request or flag, and the backend sends back any next step that reads as "do not pay", "reject", "approve" or "mark as non-payable" once for a rephrase. The demo runs showed none.
- **Step 2** names where the ratio is published (annual report, IRDAI) from general knowledge. It is a pointer, not a policy fact, and it carries no citation.
- Not modelled yet: IRDAI's longer non-payable list, sub-limits, bonus and restore benefits, network lookup, and the newer 2026 wording (`HDFHLIP26058V082526`). Only wording `HDFHLIP25041V062425` is indexed.
- All claims in the demo are synthetic. Never load a real person's claim.

## Re-verifying the script

`RETRIEVER=azure AGENT_MODE=foundry python scripts/demo_check.py --show 4` runs these exact 8 steps in the same sessions and checks the numbers and citations above, printing the full answer of any step you list with `--show`. If the agent's prompt or the policy index changes, run it again and update this file.
