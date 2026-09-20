# CLAUDE.md: Claims Adjudication Assistant (AI-103 student project)

Read this whole file before doing anything. It is the full context of the project as of 19 Sep 2026.

## 1. What we are building

A health-insurance **claims-officer assistant** for the public **HDFC ERGO my:Optima Secure** policy wording. It answers policy questions with exact citations and assesses a structured claim (eligibility, waiting periods, room-rent deduction, non-payable items, missing documents, estimated payment) in a fixed, nicely formatted layout. **The human officer always decides.** It is a university project (AI-103, Azure AI), so it must clearly use Azure AI Search (RAG), a Foundry agent with function tools, and grounded, cited answers.

Scope right now: **backend plus two small web pages**, static files in `app/static`, served by FastAPI, same origin as the API, no build step, no CDN. **`/` is the customer page** ("ARC: AI-powered Review of Claims"): screen 1 uploads and checks documents, screen 2 is a chat about the claim; it shows summaries, never tool names, traces or chunk keys (those exist only behind `?dev=1`, in `dev.js`). **`/officer` is the earlier officer console** (claim picker, live badge, trace panel). A full frontend comes later.

## 2. The user, and how to work with them

- CS student, **new to Azure**. When a step happens in a portal, give exact click paths (portal.azure.com or ai.azure.com), say what they should see afterwards, and wait for confirmation before the next step.
- Never ask them to paste keys into chat. Secrets go only into `.env`. Never print, log or commit secrets.
- Prefer small, reviewable diffs. Explain what you changed and why in plain language. Keep the tests green (445, all offline; the markdown and label tests need Node and the browser tests need Playwright with the installed Chrome; each skips itself when its tool is missing).
- Ask before any destructive or costly Azure action (deleting an index or agent, changing pricing tiers, creating new resources).

## 3. Azure resources that already exist (all built by the user last week)

| Resource | Details |
|---|---|
| Subscription / RG | Azure for Students, resource group `rg-claims-agent`. Policy restricts regions to: Korea Central, Central India, East Asia, Malaysia West, UAE North. |
| Storage | `claimsagentjp2026` (Central India), container `policy-docs` holds the raw policy PDF. Not used at runtime yet. |
| Azure AI Search | `claims-search-37`, **Free tier**, Central India. Old wizard index `rag-1789575754829` (generic chunks, no clause IDs). Leave it alone. |
| Models | Deployments live in the Foundry resource `claims-agent-project-res` (no separate `openai-claims-agent` resource is visible in the resource group). Deployment names: `gpt-5-mini` and **`text-embedding-3-large`** (not small). Embeddings are requested with `dimensions=EMBEDDING_DIMENSIONS` (default 1536) so the index stays at 1536. Use the endpoint from the **OpenAI** tab of Keys and Endpoint and KEY 1. |
| Foundry | Project on resource `claims-agent-project-res` (Korea Central). Portal-made agent `claims-adjudication-agent` (v1, worked in the playground on a knee-replacement question) and a Foundry IQ knowledge base `policy-knowledge-base` pointing at the old index. **Do not modify v1.** |
| Monitoring | Application Insights and Log Analytics were auto-provisioned with the project. |

Foundry project name: `jashanpreetsingh3999-6322`, project endpoint `https://claims-agent-project-res.services.ai.azure.com/api/projects/jashanpreetsingh3999-6322`. Portal is on **New Foundry**. Deployment names and endpoints are in the user's `.env` (see `.env.example`); `python scripts/setup/check_env.py` validates them. Roles: the user's account needs **Foundry User** (formerly Azure AI User) on the Foundry resource/project to create and run agents. Function tools cannot be added in the portal; they are added with the SDK (`scripts/setup/create_agent.py`).

## 4. Architecture and the decisions behind it

```
question (+ claim loaded in session)
  -> Foundry agent v2 (gpt-5-mini) picks tools
       search_policy / get_clause    -> Retriever (Azure AI Search hybrid + semantic, filtered by the claim's policy UIN)
       check_waiting_period          -> claims_engine (pure Python)
       assess_claim (+ what_if)      -> claims_engine
       lookup_non_medical_item       -> Annexure B table
       get_claim_summary
  -> final_answer(answer_type, ...)  -> validator -> renderer -> Markdown
```

Decisions (do not undo without a strong reason):

1. **New index `claims-kb-v2`**, not the wizard index. Needs clause-level chunks with `chunk_id`, `chunk_key`, `uin`, `citation`, page. Built from `data/policy_clauses.jsonl` (186 chunks) by `scripts/setup/upload_chunks.py`, with embeddings computed in the backend.
2. **The LLM never computes dates or money.** Deterministic Python does. Claim numbers reach the answer only through tool results (`result_id`).
3. **The agent must finish every turn with `final_answer`.** The backend validates it (citations must be `chunk_key`s returned by tools **in that same turn**; claim answer types need a `result_id`), lets the model retry twice, then strips unverifiable citations and adds a caveat. The backend renders the Markdown itself, so the format is exact.
4. **Function tools run in the backend**, in a loop: `responses.create` -> execute `function_call` items -> send `function_call_output` -> repeat. A **fresh conversation per turn**; the backend keeps chat history and passes a short summary. This avoids dangling tool calls. Runs expire after 10 minutes.
5. **Retriever abstraction:** `LocalRetriever` (BM25 over the JSONL, for tests and offline dev) and `AzureSearchRetriever`. Switch with `RETRIEVER=local|azure`. Agent switch: `AGENT_MODE=offline|foundry`. `OfflineAgent` is a keyword router that exercises the same tools and renderers. It is **not** the real agent.
6. **Version-aware retrieval:** every chunk carries `uin` and `doc_id`. Retrieval filters by the claim's `policy_uin`. Only wording UIN **HDFHLIP25041V062425** is indexed today.
7. A newer wording exists (**HDFHLIP26058V082526**, for policies starting 2026-04-02). Observed differences: an Optima Secure + plan and Infinite Benefit (B.2.17); Protect Benefit only if stated in the schedule; different utilization order (D.1.19); room-rent option for a single private room on Secure/Super Secure; reworded D.1.3 and D.1.14; age measured at each policy year start. Its annexures moved (start pp.52/56/60) so `tools/chunk_policy.py` **refuses to run on it** (guard on UIN). Use `tools/chunk_generic.py` until the clause chunker is adapted.

## 5. Repo map

```
CLAUDE.md, README.md, requirements.txt, .env.example, .gitignore
app/
  config.py                    settings from env
  main.py                      FastAPI: /health /samples /sessions (audience officer|customer) /sessions/{id}/claim /sessions/{id}/chat /assess and the intake routes /sessions/{id}/documents, /documents/sample, /intake; pages at / and /officer
  intake.py                    claim intake: reads text PDFs (pypdf), recognises the document type, extracts fields, builds the claim; ready | needs_attention with plain reasons
  retrieval/base.py            Chunk, Retriever ABC, LocalRetriever (BM25 + tiny synonym map)
  retrieval/azure_search.py    AzureSearchRetriever, embed(), get_retriever()
  tools/claims_engine.py       all deterministic checks, assess(), apply_what_if(), compact_summary()
  tools/plain_questions.py     plain questions (name, hospital, dates, plan ...; hello/thanks): recognised in the backend, answered as a one-line general_answer, never an assessment
  tools/evidence.py            clause ref ("C.1.b", "B.1.1.1 Note iii", "A.1.2 Def. 5") -> chunk_id
  tools/registry.py            tool JSON schemas, call_tool(), final_answer validation, render_final()
  rendering/render.py          Markdown templates for every answer type
  static/                      customer page: index.html, customer.css, customer.js, md.js (small vendored markdown renderer); dev.js/dev.css only with ?dev=1; officer console: officer.html, arc.css, arc.js, labels.js
  rendering/scrub.py           internal terms (result ids, chunk keys, tool names): find() for the validator guard, scrub_final() in the renderer
  agent/instructions.py        the agent's system prompt
  agent/runner.py              FoundryAgent (tool loop), OfflineAgent, get_agent()
scripts/                       create_index, upload_chunks, create_agent, eval_retrieval, chat_cli, render_samples, render_examples, run_demo.sh (starts the app on 8765, real agent only), take_screenshots.py (Playwright, customer page), take_screenshots.mjs (officer console), demo_check.py, eval_agent.py
tools/                         chunk_policy.py (clause-aware, tuned to V062425), chunk_generic.py
data/                          policy_clauses.jsonl, rules/*.json, sample_claims.json (12), rag_eval_questions.json (19)
tests/                         445 tests, all offline (engine, tools, agent loop with a fake client, API, timeouts/retries/logging/API hardening, general_answer guard, eval transcripts, query-aware excerpts, decision-wording guard, internal-terms guard and scrub, web UI and its renderer)
examples/                      rendered outputs (12 assessments, 7 Q&A types)
reference/                     demo_documents (10 synthetic PDFs for the upload demo), policy PDF, claims_data_pack (demo claim PDFs, expected_extraction.json), kb_sources_pack (28-document source list, downloader)
```

Answer types: `claim_assessment`, `coverage_answer`, `waiting_period_answer`, `deduction_explanation`, `documents_answer`, `definition_answer`, `insufficient_information`, `general_answer`.

## 6. Commands

```bash
pip install -r requirements.txt      # add --pre if azure-ai-projects 2.x is only available as a pre-release
python -m pytest tests -q            # 30 must pass
python scripts/dev/render_samples.py && python scripts/dev/render_examples.py
python scripts/eval/eval_retrieval.py --verbose
python scripts/dev/chat_cli.py --claim TC07
uvicorn app.main:app --reload
# Azure (needs .env filled, az login, Foundry User role):
python scripts/setup/create_index.py && python scripts/setup/upload_chunks.py
RETRIEVER=azure python scripts/eval/eval_retrieval.py --verbose
python scripts/setup/create_agent.py
RETRIEVER=azure AGENT_MODE=foundry python scripts/dev/chat_cli.py --claim TC07
# Rebuild chunks from the PDF:
python tools/chunk_policy.py reference/policy/optima-secure-HDFHLIP25041V062425.pdf --uin HDFHLIP25041V062425 --doc-id optima-secure-v062425 --out data/policy_clauses.jsonl
```

## 7. Domain rules encoded in `claims_engine.py` (check against the wording before changing)

- **Waiting periods (C.1):** Excl03 30 days (not for accidents; not if continuous cover > 12 months); Excl02 24 months for the 20 listed illnesses and 25 procedures (not for accidents; 36 if also pre-existing); Excl01 36 months pre-existing. `prior_continuous_coverage_months` (portability) shortens them. The tool also returns `eligible_from`.
- **Hospitalization (Def. 19, B.1.1.1 Note i):** at least 24 hours unless a day-care procedure.
- **Room rent (B.1.1.1 Note iii):** if billed room rate exceeds the plan limit, room and **associated medical expenses** are paid in proportion `limit / billed`. Plan limits from `data/rules/plan_config.json` (Optima Lite: 1% of base sum insured per day, ICU 2%; Secure/Suraksha/Super Secure/Global: at actuals; Select: single private room, hospital-specific rate). Not applied to pharmacy, consumables, diagnostics, implants, or ICU associated expenses. Applies only if `differential_billing`.
- **Associated medical expenses (Def. 5):** consultation, OT, nursing, anaesthesia, surgeon fees (assumed), blood, oxygen.
- **Non-medical items (C.3.k, Annexure B, 68 items):** non-payable unless Protect Benefit is in force (`covered` on Secure/Super Secure/Global plans; `optional` on Select/Lite, needs `protect_benefit_opted`; `not_covered` on Suraksha).
- **Documents (E.1.7):** checklist from `claim_documents.json`; incomplete or missing documents give `likely_eligible_pending_documents`; a missing prescription puts the medicines line **on hold**.
- **Amount:** aggregate deductible first (D.1.19 in the older wording), then co-pay (0 unless in schedule), then cap at `sum_insured_available`.
- **Recommendation enum:** `likely_eligible`, `likely_eligible_pending_documents`, `likely_not_payable`, `needs_human_review`. `payable_confirmed_now` excludes held items; `estimated_payable_if_docs_supplied` includes them.
- **Not modelled yet:** IRDAI's longer non-payable list (~200 items, 4 groups incl. items subsumed into room/procedure charges), sub-limits, restore/bonus benefits, cashless pre-auth, network lookup, multi-claim history.

Claim JSON: required `claim_id, plan, base_si_lakh, first_policy_inception, admission_datetime, discharge_datetime, bill_lines[{description, category, amount, annexure_b_no?}]`. Categories: `room, icu_room, associated, pharmacy_medicine, consumables, diagnostics, implant, non_medical, other`. Optional: `insured_name, policy_uin, diagnosis, procedure, is_accident, pre_existing, is_day_care_procedure, room_rate_per_day, room_days, icu_rate_per_day, differential_billing, hospital_network, documents{id:{present,complete,missing_parts}}, documents_data, aggregate_deductible_remaining, copay_percent, sum_insured_available, protect_benefit_opted, prior_continuous_coverage_months, refractive_error_dioptres, review_flags, claimed_amount`. See `data/sample_claims.json`.

## 8. Ground truth you can rely on

- The 12 sample claims (TC01 to TC12) have **hand-derived** expected outcomes (in `data/sample_claims.json`). Key demo: **TC07** (Optima Lite, appendectomy, bill 1,84,500, room 8,000/day vs 5,000 limit -> ratio 0.625): room -12,000, associated -37,875, non-medical -12,500, estimate **1,22,125**, **1,01,625** confirmed until the prescription arrives. **Never edit expected numbers to make a test pass.** Re-derive from the wording instead.
- `data/rag_eval_questions.json`: 19 questions with expected `chunk_id`s (3 are unanswerable on purpose). Local BM25 baseline: hit@5 16/16, full recall 13/16 (optimistic: questions were written from the same document).
- `chunk_id` scheme: `B1.1.1-Note-iii`, `C1-b`, `C1-b-list`, `C2-l`, `C3-k`, `A1.1-Def19`, `A1.2-Def5`, `E1.7`, `ANX-B`, `ANX-C-Lite`. `chunk_key` = `doc_id:chunk_id`. Azure document keys cannot contain `:` or `.`, so the index `id` is a sanitized `chunk_key`.

## 9. Status: what is verified and what is not

**Verified offline (pytest, 30 passing):** engine vs 12 hand-derived cases; every clause reference in every sample resolves to a real chunk; tool validation (invented citation rejected, third attempt strips); the Foundry tool loop against a **scripted fake client**; API endpoints; renderer output.

**NOT verified, never run against real Azure:** `azure_search.py`, `create_index.py`, `upload_chunks.py`, `create_agent.py`, and the real `FoundryAgent` calls. Written from Microsoft Learn (azure-ai-projects 2.x function-calling page; azure-search-documents 11.x). Expect small API differences. Fix them at the source, keep the abstraction, and re-run the tests.

Things to check first on real Azure:
1. `pip install azure-ai-projects` version (2.x, maybe `--pre`). `AIProjectClient(...).get_openai_client()`, `agents.create_version(agent_name, definition=PromptAgentDefinition(model, instructions, tools=[FunctionTool(name, parameters, description, strict)]))`.
2. `openai.conversations.create()`, `openai.responses.create(input=..., conversation=id, extra_body={"agent_reference": {"name":..., "type":"agent_reference"}})`; `function_call_output` items passed as a list of dicts; `conversations.delete(conversation_id=...)`.
3. Function tools use `strict=False` (schemas are not strict-mode compliant). Try `strict=True` only if you also make every property required and nullable.
4. Search: semantic ranker enabled on the service; semantic config name `default`; `VectorizedQuery`; `@search.reranker_score`; free tier index limits.
5. Embeddings via `AzureOpenAI(...).embeddings.create(model=<deployment name>, dimensions=1536)`. The deployment is text-embedding-3-large, so `dimensions` is required to match the 1536-dim index.
6. `MODEL_DEPLOYMENT` must be a deployment the Foundry project can use.
7. Token/role errors: Foundry User role, allow a few minutes to propagate, `az login` with the same account.

## 10. Roadmap (do in order; each has a done-when)

1. **Environment.** venv, install, `.env` from `.env.example`, tests pass. *Done when* `pytest` shows 30 passed.
2. **Azure wiring, retrieval.** Create index, upload 186 chunks, run `eval_retrieval.py` with `RETRIEVER=azure`. *Done when* the index shows 186 documents, Search explorer returns results, and hit@5 is at least the local baseline (investigate every MISS).
3. **Azure wiring, agent.** Create agent v2, run `chat_cli.py --claim TC07` with the real agent. *Done when* "assess this claim" returns the formatted assessment with 1,22,125 and 1,01,625, and the trace is `assess_claim > final_answer`.
4. **Agent-level evaluation. Done** (`scripts/eval/eval_agent.py`, report in `examples/eval_report.md`, transcripts of failed or retried runs in `examples/eval_failures/`; agent v3: 28 cases x 3 runs all passed). **Demo script** in `docs/DEMO.md`, re-verified with `scripts/eval/demo_check.py`. Original brief: Write `scripts/eval/eval_agent.py`: run all 12 samples ("assess this claim") and a set of coverage/waiting/definition/unanswerable questions through the real agent; check recommendation, amounts, citations exist, `final_answer` was accepted; report a table. Include the user's earlier test: "Is knee replacement covered and what is the waiting period?" (expect: joint replacement on the specified list, 24 months, accident exception, PED longer period, cited to C.1.b/C.1.a).
5. **Hardening. Done.** `app/resilience.py`: every Azure call has a timeout, a chat turn has a 60 s deadline (`TURN_DEADLINE_S`), 429 and transient 5xx are retried with capped exponential backoff (honours Retry-After), and a timeout or exhausted retries gives a "please try again" answer with `status` `timeout` | `unavailable` (`AgentResult.status`, also in the `/chat` response). Model calls are never retried after a read timeout (the request may still be running). The SDK defaults (openai: 10 min timeout, 2 silent retries; azure-core: 10 retries) are switched off in favour of this. `app/observability.py`: one JSON log line per turn (tools, ms, answer type, status, latency, retries; never the question, claim data or secrets). API: clean `{"error": {"code", "message"}}` bodies, no stack traces, 413 above `MAX_REQUEST_BYTES`, CORS only for `CORS_ORIGINS`. Known limit: 3 parallel evaluation workers hit the gpt-5-mini quota (429); raise the deployment's tokens-per-minute in Foundry if that matters.
6. **IRDAI non-payable list** as a table (ask the user for the PDF: Annexure A of IRDAI's 2020 standardization guidelines, or the current master circular annexure). Extend `lookup_non_medical_item` and the bill analysis with the four groups (non-payable vs subsumed into room/procedure/treatment costs). Add tests.
7. **2026 wording support.** Adapt `chunk_policy.py` (or improve the generic chunker), index it with `--uin HDFHLIP26058V082526 --effective-from 2026-04-02`, make the engine honour differences (utilization order, Protect Benefit only if in schedule, Infinite Benefit). Test that a claim with each UIN retrieves only its own wording.
8. **Claim intake from PDFs. Partly done.** `app/intake.py` is a local, rule-based reader for text PDFs in the format of `reference/demo_documents` (no Azure): it reproduces the TC07 claim exactly from the 10 sample PDFs, flags wrong-person, wrong-total and wrong-date documents, and refuses scans, photos and unfamiliar files with a plain message. **Still to do:** Azure AI Content Understanding (or Document Intelligence) behind the same `process_file` / `build` interface so real, scanned or differently laid-out documents work. Original brief: Azure AI Content Understanding (or Document Intelligence) to turn the PDFs in `reference/claims_data_pack/demo_claim/` into claim JSON; score against `expected_extraction.json`. Show extracted values for officer confirmation.
9. **Optional deployment** (Azure Container Apps or App Service in Central India, managed identity, Key Vault). Student quotas may block it; running locally is acceptable for the demo.

Out of scope for now: a full frontend, real patient data, payments, production hardening beyond item 5.

## 11. Rules for you

- Numbers, dates, clause citations in answers come from code or retrieved chunks, never from the model's own arithmetic or memory. Do not loosen `validate_final`.
- Do not put policy facts into the system prompt. Policy content must come from retrieval.
- Do not call the assistant's output a claim decision. Wording is "likely", "appears", "flagged for review".
- Synthetic data only. Never use real people's claim documents (real filled schedules on Scribd etc. contain personal data).
- Keep `.env` out of git. Never echo secrets. Rotate a key if one leaks.
- When you change behaviour, add or update a test. Run the full suite before saying something works. Say plainly what you did **not** run.
- When unsure about an Azure API, check Microsoft Learn rather than guessing, and tell the user what you checked.
