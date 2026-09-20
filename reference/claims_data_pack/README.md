# Claims copilot data pack (synthetic PoC)

Built from the `my: Optima Secure` policy wording (UIN HDFHLIP25041V062425). Everything about people, hospitals and amounts is fictional.

## What is inside

| Path | What it is | Used for |
|---|---|---|
| `policy/policy_clauses.jsonl` | 186 clause-level chunks of the policy wording with `chunk_id`, `clause`, `citation`, `page_start`, `code` | Azure AI Search index (RAG) |
| `rules/plan_config.json` | Plan chart (Annexure C): room/ICU limits, pre/post days, Protect Benefit status per plan | `analyze_bill()` |
| `rules/waiting_periods.json` | Excl01/02/03 plus the 20 specified illnesses and 25 procedures | `check_waiting_period()` |
| `rules/exclusions.json` | Excl04-Excl18 and specific exclusions C.3.a-q, each tagged deterministic / lookup / llm_judgement | exclusion checks |
| `rules/non_medical_items.json` | The 68 Annexure B items | `analyze_bill()` |
| `rules/claim_documents.json` | E.1.7 document list with conditions, time limits | `check_required_documents()` |
| `demo_claim/*.pdf` | Policy schedule, claim form, discharge summary, itemised bill, lab report for the demo claim | upload and extraction demo |
| `demo_claim/expected_extraction.json` | Ground truth for those PDFs | score Content Understanding |
| `test_cases/test_cases.json` | 12 claim cases with hand-derived expected results | correctness testing |
| `test_cases/rag_eval_questions.json` | 19 questions with expected chunk IDs (3 unanswerable on purpose) | retrieval and grounding testing |
| `tools/reference_calculator.py` | Deterministic checks and `assess()` | your agent's Python tools |
| `tools/run_tests.py` | Runs the 12 cases and prints PASS/FAIL | CI / demo evidence |
| `tools/chunk_policy.py` | Regenerates the chunks from the policy PDF | re-run if the wording changes |

## Quick start

```bash
python tools/run_tests.py                      # 12/12 should pass
python tools/chunk_policy.py optima-secure-revision-pw.pdf   # rebuild policy/policy_clauses.jsonl
```

Suggested Azure AI Search fields: `chunk_id` (key), `text` (searchable, semantic), `citation`, `clause`, `title`, `section`, `code`, `chunk_type`, `page_start` (filterable). Add a vector field on `text` if you use hybrid search.

## The demo claim (TC07)

Optima Lite, Rs. 5,00,000. Appendectomy, 4 days, bill Rs. 1,84,500. Room Rs. 8,000/day against a Rs. 5,000/day limit gives a 62.5% proportionate deduction.

| Line | Billed | Payable |
|---|---|---|
| Room | 32,000 | 20,000 |
| Associated medical expenses | 1,01,000 | 63,125 |
| Pharmacy and consumables | 30,000 | 30,000 (20,500 on hold: no prescription) |
| Diagnostics | 9,000 | 9,000 |
| Non-medical (Annexure B) | 12,500 | 0 |
| Total | 1,84,500 | 1,22,125 (1,01,625 until the prescription arrives) |

## Assumptions to state in your limitations slide

- Surgeon, anaesthetist, consultant, OT and nursing charges are treated as associated medical expenses (Def. 5). Pharmacy, consumables, diagnostics and implants are not reduced.
- The proportionate deduction applies only where the hospital bills differently by room category (`differential_billing`).
- Co-payment is 0. The wording has no co-pay percentage; it would come from the schedule.
- The keyword matcher for the specified-disease list is deliberately small. Extend `SPECIFIED_KEYWORDS`.
- Exclusion summaries in `rules/exclusions.json` are paraphrases. Always show the verbatim clause from `policy_clauses.jsonl` as evidence.
- Chunks come from PDF text extraction. Spot-check clause boundaries in the tables before relying on them.
- Annexure A (ombudsman offices) is one chunk and low value for claim logic.
