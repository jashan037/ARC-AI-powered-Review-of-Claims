# CLAUDE.md: ARC, AI-powered Review of Claims (AI-103 student project)

Read this whole file before doing anything. Facts below are as of 20 Sep 2026 (after the repository cleanup, tag `pre-cleanup` marks the state before it). `docs/SYSTEM_REPORT.md` is the audit: its P0 and P1 lists are known, open issues.

## 1. What this is

A customer uploads health-insurance claim documents (PDF), ARC reads them, builds the claim and answers questions about it, for the public **HDFC ERGO my:Optima Secure** wording (UIN HDFHLIP25041V062425). It uses Azure AI Search (RAG), a Foundry agent with function tools, and grounded, cited answers. **A claims officer always decides**: the wording is "likely", "appears", "flagged for review", never a decision.

Served by FastAPI: **`/` is the customer page** (screen 1 upload and document check, screen 2 chat; short summaries with "Show more"; `?dev=1` adds a badge and a trace panel from `dev.js`). Static files in `app/static`, no build step, no CDN. The earlier officer console was removed; the **officer wording mode stays in the renderers** (19 golden files in `tests/golden` lock it).

## 2. The user and how to work with them

- CS student, **new to Azure**. For portal steps give exact click paths and wait for confirmation.
- Never ask them to paste keys into chat. Secrets go only into `.env`. Never print, log or commit secrets.
- Small, reviewable diffs; explain in plain language. Commits: one-line message, no co-author line, only when asked.
- Keep the tests green (439, all offline; the markdown test needs Node, the browser tests need Playwright with the installed Chrome and skip themselves without them).
- Ask before any destructive or costly Azure action (deleting an index or agent, changing tiers, creating resources). Do not touch the old index `rag-1789575754829` or the portal agent `claims-adjudication-agent` (v1).

## 3. Azure resources (all exist and are in use)

| Resource | Details |
|---|---|
| Subscription / RG | Azure for Students, `rg-claims-agent`. Regions allowed by policy: Korea Central, Central India, East Asia, Malaysia West, UAE North. |
| Azure AI Search | `claims-search-37`, **Free tier**, Central India. Index **`claims-kb-v2`** (186 clause-level chunks, semantic config `default`, 1536-dim vectors), API-key auth. |
| Models | In the Foundry resource `claims-agent-project-res` (Korea Central): `gpt-5-mini` and **`text-embedding-3-large`** (requested with `dimensions=1536`). Embeddings use the OpenAI endpoint and key from `.env`. |
| Foundry | Project `jashanpreetsingh3999-6322`; agent **`claims-adjudication-agent-v2`, version 7 live** (prompt = `app/agent/instructions.py`, tools = `app/tools/registry.py:SCHEMAS`). Auth is `DefaultAzureCredential` (`az login`, role **Foundry User**). |
| Other | Storage `claimsagentjp2026` and Application Insights/Log Analytics exist but the app does not use them. |

`python scripts/setup/check_env.py` validates `.env` (see `.env.example`). Function tools cannot be added in the portal; `scripts/setup/create_agent.py` adds a new agent version from the local prompt and schemas (run it only when the prompt or schemas changed).

## 4. Architecture and decisions (do not undo without a strong reason)

```
documents -> app/intake.py (pypdf + rules, no model, no Azure) -> claim in the session
question -> Foundry agent (fresh conversation per turn) -> tools run in the backend:
   search_policy / get_clause -> Retriever (Azure AI Search hybrid + semantic, filtered by the claim's UIN)
   check_waiting_period, assess_claim (+what_if), lookup_non_medical_item, get_claim_summary -> claims_engine (pure Python)
-> final_answer -> validate_final -> render_final -> summary_markdown + sections
```

1. **The model never computes dates or money.** Python does. Claim numbers reach the answer only through tool results (`result_id`).
2. **Every turn ends with `final_answer`.** The backend validates it (citations must be `chunk_key`s returned in the same turn; claim types need a `result_id`; guards for decision wording, internal terms, length caps, general_answer after assess, plain questions). Two rejections, then citations are stripped and a caveat added. **Do not loosen `validate_final`.**
3. **Plain questions** (name, hospital, dates, plan, hello, thanks, weather) get a one-line `general_answer`; `assess_claim` is refused for them (`app/tools/plain_questions.py`).
4. Function tools run in the backend loop (`responses.create` -> execute calls -> `function_call_output`). Chat history is kept by the backend and passed as a short text block.
5. `LocalRetriever` (BM25) and `OfflineAgent` (keyword router) are **test and development only** (`RETRIEVER=local`, `AGENT_MODE=offline`; `tests/conftest.py` forces them). The live app needs `RETRIEVER=azure`, `AGENT_MODE=foundry`.
6. Retrieval is version-aware (`uin`, `doc_id` on every chunk). Only wording HDFHLIP25041V062425 is indexed. A newer wording (HDFHLIP26058V082526) exists; `tools/chunk_policy.py` refuses it, use `tools/chunk_generic.py`.
7. Hardening (`app/resilience.py`, `app/observability.py`): timeouts everywhere, 60 s turn deadline, retry of 429/5xx, JSON log per turn without content, clean error bodies, body-size limits.

## 5. Repo map

```
app/        main.py (API), config.py, intake.py, resilience.py, observability.py
            agent/ (instructions.py, runner.py)  retrieval/ (base.py, azure_search.py)
            tools/ (claims_engine.py, evidence.py, registry.py, plain_questions.py)  rendering/ (render.py, compact.py, scrub.py)
            static/ (index.html, customer.css, customer.js, md.js, dev.js, dev.css)
data/       policy_clauses.jsonl, rules/*.json, sample_claims.json (12), rag_eval_questions.json (19)
demo/       documents/ (10 synthetic PDFs, manifest, expected_extraction.json), screenshots/, examples/, DEMO.md, original_pack/
scripts/    run_demo.sh | setup/ (create_index, upload_chunks, create_agent, bootstrap_azure, check_env)
            eval/ (eval_retrieval, eval_agent, demo_check) | dev/ (chat_cli, render_samples, render_examples, take_screenshots)
tools/      chunk_policy.py, chunk_generic.py, source/ (policy PDF), kb_sources/ (28-document source list)
tests/      test_*.py, conftest.py, golden/answers/, helpers/pdfmaker.py
docs/       SYSTEM_REPORT.md, CLEANUP_PLAN.md, CLEANUP_REPORT.md, evidence/ (eval_report.md, eval_failures/)
```

Answer types: `claim_assessment`, `coverage_answer`, `waiting_period_answer`, `deduction_explanation`, `documents_answer`, `definition_answer`, `insufficient_information`, `general_answer`.

## 6. Commands

```bash
pip install -r requirements-dev.txt          # runtime is requirements.txt (pinned); dev adds pytest, httpx, playwright
python -m pytest tests -q                    # 439 pass
scripts/run_demo.sh                          # http://127.0.0.1:8765/ (refuses unless .env is azure + foundry)
python scripts/dev/render_samples.py && python scripts/dev/render_examples.py      # -> demo/examples/
python scripts/eval/eval_retrieval.py --verbose
RETRIEVER=azure AGENT_MODE=foundry python scripts/eval/eval_agent.py --workers 2   # 38 cases; report in docs/evidence
RETRIEVER=azure AGENT_MODE=foundry python scripts/eval/demo_check.py               # the 8 demo steps
python scripts/dev/chat_cli.py --claim TC07
python tools/chunk_policy.py tools/source/optima-secure-HDFHLIP25041V062425.pdf --uin HDFHLIP25041V062425 --doc-id optima-secure-v062425 --out data/policy_clauses.jsonl
```

## 7. Domain rules in `claims_engine.py` (check against the wording before changing)

- **Waiting periods (C.1):** Excl03 30 days (not for accidents; not if continuous cover > 12 months); Excl02 24 months for the 20 listed illnesses and 25 procedures (not for accidents; 36 if also pre-existing); Excl01 36 months pre-existing. `prior_continuous_coverage_months` shortens them; the tool returns `eligible_from`.
- **Hospitalization (Def. 19, B.1.1.1 Note i):** at least 24 hours unless a day-care procedure.
- **Room rent (B.1.1.1 Note iii):** if the billed room rate exceeds the plan limit, room and **associated medical expenses** are paid in proportion `limit / billed`. Limits in `data/rules/plan_config.json` (Optima Lite 1% of base sum insured per day, ICU 2%; Secure/Suraksha/Super Secure/Global at actuals; Select single private room). Not applied to pharmacy, consumables, diagnostics, implants, ICU associated expenses. Only if `differential_billing`.
- **Associated medical expenses (Def. 5):** consultation, OT, nursing, anaesthesia, surgeon fees (assumed), blood, oxygen.
- **Non-medical items (C.3.k, Annexure B, 68 items):** non-payable unless Protect Benefit is in force (`covered` on Secure/Super Secure/Global; `optional` on Select/Lite, needs `protect_benefit_opted`; `not_covered` on Suraksha).
- **Documents (E.1.7):** checklist from `data/rules/claim_documents.json`; missing or incomplete documents give `likely_eligible_pending_documents`; a missing prescription puts the medicines line **on hold**.
- **Amount:** aggregate deductible first, then co-pay (0 unless in the schedule), then cap at `sum_insured_available`.
- **Recommendation enum:** `likely_eligible`, `likely_eligible_pending_documents`, `likely_not_payable`, `needs_human_review`. `payable_confirmed_now` excludes held items; `estimated_payable_if_docs_supplied` includes them.
- **Not modelled:** IRDAI's longer non-payable list, sub-limits, restore/bonus benefits, cashless pre-auth, network lookup, multi-claim history.

Claim JSON: required `claim_id, plan, base_si_lakh, first_policy_inception, admission_datetime, discharge_datetime, bill_lines[{description, category, amount, annexure_b_no?}]`; categories `room, icu_room, associated, pharmacy_medicine, consumables, diagnostics, implant, non_medical, other`. Optional fields: see `data/sample_claims.json` and `app/intake.py` (which also adds `policy_number` and `hospital`).

## 8. Ground truth

- The 12 sample claims (TC01 to TC12) have **hand-derived** expected outcomes in `data/sample_claims.json`. **Never edit expected numbers to make a test pass**; re-derive from the wording. Key demo, **TC07** (also what the 10 demo PDFs produce): estimate **₹1,22,125**, **₹1,01,625** confirmed until the prescription arrives, **₹20,500** held; room −12,000, associated −37,875, non-medical −12,500.
- `data/rag_eval_questions.json`: 19 questions with expected `chunk_id`s (3 unanswerable). Local BM25 baseline hit@5 16/16, full recall 13/16 (optimistic).
- `chunk_key` = `doc_id:chunk_id`; the index `id` is a sanitised `chunk_key` (Azure keys cannot contain `:` or `.`).

## 9. Status (20 Sep 2026)

Done and verified on real Azure: index and 186 chunks, agent v7, agent evaluation (38 cases, `docs/evidence/eval_report.md`), the 8-step demo, hardening, compact answers, customer page, document intake from the sample PDFs, plain-question handling. Roadmap items still open:

- **IRDAI non-payable list** (needs the IRDAI annexure PDF from the user): extend `lookup_non_medical_item` and the bill analysis.
- **2026 wording support:** adapt the chunker, index UIN HDFHLIP26058V082526, make the engine honour the differences (utilization order, Protect Benefit only if in schedule, Infinite Benefit).
- **Intake for real documents:** Azure AI Content Understanding or Document Intelligence behind `process_file`/`build` (today: text PDFs in the sample layout only).
- **Deployment:** blocked by the P0 items in `docs/SYSTEM_REPORT.md` (no authentication or rate limit, in-memory sessions, secrets in `.env`). Running locally is acceptable for the demo.

Out of scope: real patient data, payments, a full production frontend.

## 10. Rules for you

- Numbers, dates and clause citations come from code or retrieved chunks, never from the model's arithmetic or memory. Do not put policy facts into the system prompt.
- When you change behaviour, add or update a test. Run the full suite before saying something works. Say plainly what you did **not** run.
- Synthetic data only. Keep `.env` out of git; never echo secrets; rotate a key if one leaks.
- When unsure about an Azure API, check Microsoft Learn and say what you checked.
