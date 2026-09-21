# CLAUDE.md: ARC, AI-powered Review of Claims (AI-103 student project)

Read this whole file before doing anything. Facts below are as of **21 Sep 2026, after the final pass** (tag `pre-final` marks the state before it, `final-submission` the state after). `docs/FINAL_REPORT.md` is the record of that pass: what was built, what was verified and what was not. `docs/SYSTEM_REPORT.md` is the older audit; its P0/P1 items are mostly closed now (see the final report), but read it for context.

## 1. What this is

A customer uploads health-insurance claim documents (PDF), ARC reads them, builds the claim, answers questions about it in plain words, and prepares a report for the insurer's claims team, for the public **HDFC ERGO my:Optima Secure** wording (UIN HDFHLIP25041V062425). It uses Azure AI Search (RAG) and a Foundry agent with function tools. **ARC never decides**: the wording is "likely", "appears", "flagged for review". Customer-facing text says **"your insurer's team"**, never "claims officer" (a guard enforces it).

Served by FastAPI. **Three views at three real paths, one HTML file**: `/` (landing: headline, one navy pill button, a sample link, a demo note), `/upload` (one drop section, batches of 5 files, plain rows), `/chat` (blank cards rendered with md.js, a fixed composer, and top right "Download report" + "Start over" once the claim is ready). History API, `sessionStorage` for the session id, `/chat` without a ready claim redirects to `/upload`, and a refresh restores the conversation from `GET /sessions/{id}/messages`. Every UI string is in one `TEXT` block in `app.js`. Static files in `app/static` (`index.html`, `app.css` with the design tokens at the top, `app.js`, `md.js`, `logo.svg`, `fonts/InterVariable.woff2` + OFL). No build step, no CDN, no external request; strict CSP (no inline script or style, `font-src 'self'`). The logo is an inline SVG arc (teal) with an amber dot. Screenshots: `python scripts/dev/ui_screenshots.py` -> `docs/screenshots/final/`. Browser tests incl. axe-core: `tests/test_ui.py`.

## 2. The user and how to work with them

- CS student, **new to Azure**. For portal steps give exact click paths and wait for confirmation.
- Never ask them to paste keys into chat. Secrets go only into `.env`. Never print, log or commit secrets.
- Small, reviewable diffs; explain in plain language. Commits: one-line message, no co-author line, only when asked.
- Keep the tests green (**551**, all offline; the markdown tests need Node, the browser tests need Playwright with the installed Chrome, and both skip themselves without them).
- Ask before any destructive or costly Azure action (deleting an index or agent, changing tiers, creating resources). Do not touch the old index `rag-1789575754829` or the portal agent `claims-adjudication-agent` (v1).

## 3. Azure resources (all exist and are in use)

| Resource | Details |
|---|---|
| Subscription / RG | Azure for Students, `rg-claims-agent`. Regions allowed by policy: Korea Central, Central India, East Asia, Malaysia West, UAE North. |
| Azure AI Search | `claims-search-37`, **Free tier**, Central India. Index **`claims-kb-v2`** (186 clause-level chunks, semantic config `default`, 1536-dim vectors), API-key auth. |
| Models | In the Foundry resource `claims-agent-project-res` (Korea Central): `gpt-5-mini` and **`text-embedding-3-large`** (requested with `dimensions=1536`). Embeddings use the OpenAI endpoint and key from `.env`. |
| Foundry | Project `jashanpreetsingh3999-6322`; agent **`claims-adjudication-agent-v2`, latest version 29 (prompt 8,993 characters, reasoning effort low); roll back with `AGENT_VERSION=28` (before the final pass) or `23` (before the accuracy pass) in `.env`, nothing is deleted** (prompt = `app/agent/instructions.py`, tools = `app/tools/registry.py:SCHEMAS`). Auth is `DefaultAzureCredential` (`az login`, role **Foundry User**). |
| Other | Storage `claimsagentjp2026` and Application Insights/Log Analytics exist but the app does not use them. |

`python scripts/setup/check_env.py` validates `.env` (see `.env.example`). Function tools cannot be added in the portal; `scripts/setup/create_agent.py` adds a new agent version from the local prompt and schemas (run it only when the prompt or schemas changed).

## 4. Architecture and decisions (do not undo without a strong reason)

```
documents -> app/intake.py (pypdf + rules, no model; records the source file and page of every field) -> claim in the session
question -> code runs assess_claim first -> Foundry agent (fresh conversation per turn), input = claim facts + assessment + short history
   tools run in the backend: search_policy / get_clause (Azure AI Search), check_waiting_period, assess_claim (+what_if), lookup_non_medical_item,
   get_claim_summary, cover_left  -> claims_engine (pure Python, dates and money)
-> plain Markdown reply -> guards (rewrite once, then repair in code) -> suppress repeated notes -> sources added by code
claim + engine result -> app/report.py (reportlab, vendored DejaVu Sans, NO model, nothing from the chat) -> the claims team's 4-page PDF
```

1. **The model never computes dates or money.** Python does (`claims_engine.py`, `tools/totals.py`, `tools/facts.py`). What a reply may state:
   - **G1** claim facts only from `claim_facts` (`get_claim_summary` and the block sent every turn), verbatim;
   - **G2** every number, date, percentage and count must be in the allowed set (`number_guard.py`; formats normalised; Markdown-aware; repair works on blocks);
   - **G3** rule verdicts (policy in force, waiting period, filing time, non-medical, documents, outlook) must agree with the tools (`tools/verify.py`);
   - **G4** policy statements need support in a passage retrieved this turn, the facts, a tool result or a rule table (string support check, no second model call);
   - **G5** unknown: say so and ask one question; customer-stated facts and hypotheticals are labelled assumptions.
   - **G6** the topic asked about decides what the answer leads with (`tools/focus.py`: non_medical, room, doctor_fees, held, deductible, overall; a claim that was not in force leads with that, never with an amount);
   - **G7** nobody is told to wait for a treatment that already happened, and the accident exception is a condition, never settled (`tools/timing.py`);
   - **G8** an amount presented as "what we will pay" must be one of the engine's payment figures, even when the number itself is real (`verify.payment_problems`).
2. Guards live in `tools/guards.py` (numbers, amounts, internal terms, decision/hedging, voice + "officer", required figures, verdicts, names incl. document names, policy support, focus, timing, format via `tools/format_guard.py`). Each rejects once, then the text is repaired in code. **Do not loosen them.** `tests/test_adversarial.py` scripts a model that lies about each of them.
3. Plain questions and hostile requests are answered in code (`tools/canned.py`); the fixed "upload first" reply needs no model.
4. Function tools run in the backend loop. Chat history is kept by the backend and passed as a short text block.
5. `LocalRetriever` (BM25) and `OfflineAgent` are **test and development only** (`RETRIEVER=local`, `AGENT_MODE=offline`; `tests/conftest.py` forces them, sets `ARC_TODAY=2025-01-01` and `DEBUG=1`). The live app needs `RETRIEVER=azure`, `AGENT_MODE=foundry`.
6. Retrieval is version-aware (`uin`, `doc_id` on every chunk). Only wording HDFHLIP25041V062425 is indexed.
7. **The engine also checks:** policy in force on the admission date; filing time (30 days from discharge, E.1.6; late = REVIEW FLAG, never a rejection, E.1.7 Note iv; today is `ARC_TODAY` or the real date); patient is the insured person; a what-if `room_rate_per_day` changes the room charge, the bill and the claimed amount (rate x days), `plan_room_limit_per_day` changes only the limit.
8. **The claims team's report** (`app/report.py`, `GET /sessions/{id}/report.pdf`): built entirely in code from the claim facts and the engine result. No model call, nothing from the chat, no what-if figure, nothing stored. Deterministic to the byte apart from the timestamp; at most 4 pages, under 300 KB; page 1 is the whole summary. The report id is the first 8 hex of the SHA-256 of the canonical facts. Every result is also a WORD (Pass / Review / Not met / Missing), so it reads the same in greyscale.
9. **Security** (`docs/SECURITY.md`): developer routes off unless `DEBUG=1`, `/health` only `{"status":"ok"}` and `/ready` two booleans, security headers everywhere (including the PDF and the static files), session expiry (30 min) and caps, per-IP rate limit, `DELETE /sessions/{id}`, a 10-second PDF parsing budget, log redaction with token counts only, `AUTH_MODE=key|entra`, pre-commit secret scan. `Dockerfile` is one worker as a non-root user; `docs/DEPLOY.md` is the (unexecuted) deployment path.
10. Hardening (`app/resilience.py`, `app/observability.py`): timeouts everywhere, 60 s turn deadline, retry of 429/5xx, one JSON log line per turn without content, clean error bodies, body-size limits.

## 6. Commands

```bash
pip install -r requirements-dev.txt          # runtime is requirements.txt (pinned); dev adds pytest, httpx, playwright, detect-secrets
python -m pytest tests -q                    # 551 pass
scripts/run_demo.sh                          # http://127.0.0.1:8765/ (refuses unless .env is azure + foundry)
python demo/make_sample_sets.py              # regenerate the three document sets; --check compares them with the originals
python scripts/dev/ui_screenshots.py         # all three views at two sizes + each report's first page -> docs/screenshots/final/
python scripts/security/scan_secrets.py      # values from .env and key shapes, in the tree and the whole history
RETRIEVER=azure AGENT_MODE=foundry python scripts/eval/accuracy_suite.py --run --repeats 3
RETRIEVER=azure AGENT_MODE=foundry python scripts/eval/accuracy_suite.py --concurrent    # two customers at once
python scripts/eval/accuracy_suite.py --report    # -> docs/evidence/final_report.md, final_transcripts.md
python scripts/eval/eval_retrieval.py --verbose
python scripts/dev/chat_cli.py --claim TC07
python tools/chunk_policy.py tools/source/optima-secure-HDFHLIP25041V062425.pdf --uin HDFHLIP25041V062425 --doc-id optima-secure-v062425 --out data/policy_clauses.jsonl
```

Stale scripts that target a removed answer format and do **not** run: `scripts/eval/eval_agent.py`, `quality_suite.py`, `demo_check.py`, `format_suite.py`, `purechat_check.py`, `facts_check.py` (kept only as the record of how those passes were measured).

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

- The 12 sample claims (TC01 to TC12) have **hand-derived** expected outcomes in `data/sample_claims.json`. **Never edit expected numbers to make a test pass**; re-derive from the wording. Key demo, **TC07** (the same claim the sample documents produce): estimate **₹1,22,125**, **₹1,01,625** counted so far until the prescription arrives, **₹20,500** held; room −12,000, associated −37,875, non-medical −12,500.
- **The three document sets** (`demo/samples/{on_time,late_filing,expired}`, built by `demo/make_sample_sets.py`): the same claim in force and on time, in force but filed 372 days late (a review flag), and after the policy ended (likely not covered). Their hand-derived outcomes are in `demo/samples/expected_outcomes.json` and are what `tests/test_sample_sets.py` and the accuracy suite check against. `expected_extraction.json` is the field-level ground truth for `late_filing`. Judged as of `ARC_TODAY=2026-09-21`.
- `data/rag_eval_questions.json`: 19 questions with expected `chunk_id`s (3 unanswerable). Local BM25 baseline hit@5 16/16, full recall 13/16 (optimistic).
- `chunk_key` = `doc_id:chunk_id`; the index `id` is a sanitised `chunk_key` (Azure keys cannot contain `:` or `.`).

## 9. Status (21 Sep 2026, after the final pass) - details in `docs/FINAL_REPORT.md`

Agent **v29** live (prompt 8,993 chars; rollback `AGENT_VERSION=28`, or `23` for pre-accuracy-pass), **551 tests pass**, tags `pre-final` (before the pass) and `final-submission` (after). Built in this pass: the three document sets from parameters, the claims team's report PDF, the three-view UI at real paths, the focus/timing/payment/officer guards, `/ready`, token counts in the turn log, the Dockerfile and the deploy notes, the adversarial and security test sets.

Verified on real Azure in this pass: the accuracy suite over all three sets, three runs (`docs/evidence/final_report.md`, `final_transcripts.md`), and two customers at the same time. **Not verified:** the Docker build (the daemon was not running on this machine), `AUTH_MODE=entra` against Azure (no roles assigned - the owner must assign them, see `docs/SECURITY.md`), any deployment, real phones or non-Chrome browsers, real documents (scans, other layouts), and load beyond two concurrent users.

Open and known: no authentication; sessions in memory on one instance; only wording HDFHLIP25041V062425 indexed; HDFC's 68-item Annexure B only; intake reads text PDFs in the sample layout; the guards check figures, verdicts, topic and wording but cannot prove a sentence is well-judged.

## 10. Rules for you

- Numbers, dates and clause citations come from code or retrieved chunks, never from the model's arithmetic or memory. Do not put policy facts into the system prompt.
- When you change behaviour, add or update a test. Run the full suite before saying something works. Say plainly what you did **not** run.
- Synthetic data only. Keep `.env` out of git; never echo secrets; rotate a key if one leaks.
- When unsure about an Azure API, check Microsoft Learn and say what you checked.
