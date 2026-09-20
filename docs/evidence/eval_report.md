# Agent evaluation report

Agent `claims-adjudication-agent-v2` · model `gpt-5-mini` · retriever `azure` · mode `foundry` · 38 cases × 1 run(s) · generated 2026-09-20 18:30

**38/38 cases passed every run · 38/38 runs passed** · no flaky cases

| Case | Passed | Answer type | Tool trace (last run) | Seconds | Problems |
|---|---|---|---|---|---|
| TC01 Clean pass, room at actuals | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 17 | – |
| TC02 30-day waiting period not met | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 14 | – |
| TC03 Specified procedure inside 24 months | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 17 | – |
| TC04 Specified procedure after 24 months | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 11 | – |
| TC05 Refractive error below 7.5 dioptres | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 13 | – |
| TC06 Room rent limit and non-medical items (all d | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 14 | – |
| TC07 DEMO CLAIM: prescription missing | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 13 | – |
| TC08 Multiple deductions with aggregate deductibl | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 10 | – |
| TC09 Ambiguous: admission mainly for evaluation | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 12 | – |
| TC10 Conflicting documents | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 15 | – |
| TC11 Under 24 hours and not a day-care procedure | ✅ 1/1 | claim_assessment | assess_claim > final_answer > final_answer | 17 | (note) final_answer rejected 1x then accepted: Text the claims officer reads (headline, points, next_steps, caveats) must never mention result ids, chunk key |
| TC12 Optima Lite with Protect Benefit opted | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 13 | – |
| Q01 Is knee replacement covered and what is the waiting  | ✅ 1/1 | coverage_answer | search_policy > final_answer | 14 | – |
| Q02 The insured met with a road accident 12 days after b | ✅ 1/1 | waiting_period_answer | search_policy > search_policy > final_answer | 21 | – |
| Q03 What is the waiting period for pre-existing diseases | ✅ 1/1 | waiting_period_answer | search_policy > final_answer | 12 | – |
| Q04 Is maternity payable under this policy? | ✅ 1/1 | coverage_answer | search_policy > final_answer | 16 | – |
| Q05 Are gloves and masks in the bill payable? | ✅ 1/1 | coverage_answer | search_policy > final_answer | 18 | – |
| Q06 How many days does the insured have to send us the r | ✅ 1/1 | documents_answer | search_policy > final_answer | 16 | – |
| Q07 Which documents do we need to process a reimbursemen | ✅ 1/1 | documents_answer | search_policy > final_answer | 13 | – |
| Q08 What counts as associated medical expenses for the r | ✅ 1/1 | definition_answer | search_policy > final_answer > final_answer | 18 | (note) final_answer rejected 1x then accepted: Keep the answer short for the claims officer: point detail over 150 characters in: 'Proportionate deduction'.  |
| Q09 What is the room rent limit on the Optima Lite plan? | ✅ 1/1 | coverage_answer | search_policy > final_answer | 15 | – |
| Q10 Why was the room rent deducted on this claim? | ✅ 1/1 | deduction_explanation | assess_claim > final_answer | 13 | – |
| Q11 Which documents are still missing for this claim? | ✅ 1/1 | documents_answer | assess_claim > final_answer | 19 | – |
| Q12 What would the payable amount be if the room rent ha | ✅ 1/1 | claim_assessment | assess_claim > final_answer | 12 | – |
| Q13 Policy started 1 March 2025. The insured was admitte | ✅ 1/1 | waiting_period_answer | search_policy > check_waiting_period > final_answer | 23 | – |
| U1 What was HDFC ERGO's claim settlement ratio last fin | ✅ 1/1 | insufficient_information | search_policy > final_answer | 14 | (note) cited passages on an unanswerable question |
| U2 Is Manipal Hospital Whitefield in our cashless netwo | ✅ 1/1 | insufficient_information | search_policy > final_answer | 12 | (note) cited passages on an unanswerable question |
| U3 How much premium has this policyholder paid so far? | ✅ 1/1 | insufficient_information | search_policy > final_answer | 14 | (note) cited passages on an unanswerable question |
| P01 what's my name | ✅ 1/1 | general_answer | get_claim_summary > final_answer | 10 | – |
| P02 which hospital was I in | ✅ 1/1 | general_answer | get_claim_summary > final_answer | 6 | – |
| P03 when was I admitted | ✅ 1/1 | general_answer | get_claim_summary > final_answer | 7 | – |
| P04 how many days did I stay | ✅ 1/1 | general_answer | get_claim_summary > final_answer | 6 | – |
| P05 what is my policy number | ✅ 1/1 | general_answer | get_claim_summary > final_answer | 6 | – |
| P06 what plan do I have | ✅ 1/1 | general_answer | get_claim_summary > final_answer | 8 | – |
| P07 who are you | ✅ 1/1 | general_answer | final_answer | 5 | – |
| P08 hello | ✅ 1/1 | general_answer | final_answer | 4 | – |
| P09 thanks | ✅ 1/1 | general_answer | final_answer | 6 | – |
| P10 what's the weather | ✅ 1/1 | general_answer | final_answer | 4 | – |

How to read this: a case passes a run only if every check passed. Amounts come from the deterministic engine (via the tool results), citations are looked up in the retriever, and `final_answer` must have been accepted. The 12 claim cases use the hand-derived expectations in `data/sample_claims.json`. Question cases check answer type, required citations, facts that must appear, and engine numbers for the what-if and dated questions. Notes marked (note) do not fail a run.
