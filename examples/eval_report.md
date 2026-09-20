# Agent evaluation report

Agent `claims-adjudication-agent-v2` · model `gpt-5-mini` · retriever `azure` · mode `foundry` · 28 cases × 1 run(s) · generated 2026-09-20 15:22

**28/28 cases passed every run · 28/28 runs passed** · no flaky cases

| Case | Passed | Answer type | Tool trace (last run) | Seconds | Problems |
|---|---|---|---|---|---|
| TC01 Clean pass, room at actuals | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 16 | – |
| TC02 30-day waiting period not met | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 24 | – |
| TC03 Specified procedure inside 24 months | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 13 | – |
| TC04 Specified procedure after 24 months | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 15 | – |
| TC05 Refractive error below 7.5 dioptres | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 12 | – |
| TC06 Room rent limit and non-medical items (all d | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 14 | – |
| TC07 DEMO CLAIM: prescription missing | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 10 | – |
| TC08 Multiple deductions with aggregate deductibl | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 15 | – |
| TC09 Ambiguous: admission mainly for evaluation | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 13 | – |
| TC10 Conflicting documents | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 14 | – |
| TC11 Under 24 hours and not a day-care procedure | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 11 | – |
| TC12 Optima Lite with Protect Benefit opted | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 13 | – |
| Q01 Is knee replacement covered and what is the waiting  | ✅ 1/1 | coverage_answer | search_policy > search_policy > final_answer | 26 | – |
| Q02 The insured met with a road accident 12 days after b | ✅ 1/1 | waiting_period_answer | search_policy > final_answer | 23 | – |
| Q03 What is the waiting period for pre-existing diseases | ✅ 1/1 | waiting_period_answer | search_policy > final_answer | 20 | – |
| Q04 Is maternity payable under this policy? | ✅ 1/1 | coverage_answer | search_policy > final_answer | 16 | – |
| Q05 Are gloves and masks in the bill payable? | ✅ 1/1 | coverage_answer | search_policy > lookup_non_medical_item > lookup_non_medical_item > final_answer | 19 | – |
| Q06 How many days does the insured have to send us the r | ✅ 1/1 | documents_answer | search_policy > final_answer | 20 | – |
| Q07 Which documents do we need to process a reimbursemen | ✅ 1/1 | documents_answer | search_policy > final_answer | 16 | – |
| Q08 What counts as associated medical expenses for the r | ✅ 1/1 | definition_answer | search_policy > final_answer | 16 | – |
| Q09 What is the room rent limit on the Optima Lite plan? | ✅ 1/1 | coverage_answer | search_policy > final_answer | 15 | – |
| Q10 Why was the room rent deducted on this claim? | ✅ 1/1 | deduction_explanation | assess_claim > final_answer | 16 | – |
| Q11 Which documents are still missing for this claim? | ✅ 1/1 | documents_answer | assess_claim > final_answer | 17 | – |
| Q12 What would the payable amount be if the room rent ha | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 15 | – |
| Q13 Policy started 1 March 2025. The insured was admitte | ✅ 1/1 | waiting_period_answer | search_policy > check_waiting_period > final_answer | 25 | – |
| U1 What was HDFC ERGO's claim settlement ratio last fin | ✅ 1/1 | insufficient_information | search_policy > final_answer | 14 | (note) cited passages on an unanswerable question |
| U2 Is Manipal Hospital Whitefield in our cashless netwo | ✅ 1/1 | insufficient_information | search_policy > final_answer | 12 | (note) cited passages on an unanswerable question |
| U3 How much premium has this policyholder paid so far? | ✅ 1/1 | insufficient_information | search_policy > final_answer | 14 | (note) cited passages on an unanswerable question |

How to read this: a case passes a run only if every check passed. Amounts come from the deterministic engine (via the tool results), citations are looked up in the retriever, and `final_answer` must have been accepted. The 12 claim cases use the hand-derived expectations in `data/sample_claims.json`. Question cases check answer type, required citations, facts that must appear, and engine numbers for the what-if and dated questions. Notes marked (note) do not fail a run.
