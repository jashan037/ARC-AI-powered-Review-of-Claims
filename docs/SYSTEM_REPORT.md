# ARC (AI-powered Review of Claims): system audit report

> Note: this audit describes the repository **before** the cleanup (tag `pre-cleanup`). File paths, the officer console and some stale documentation statements have changed since; see `docs/CLEANUP_REPORT.md`. The P0 and P1 lists in section 13 are still open.

Audit date: 20 Sep 2026. Auditor: Claude Code, read-only (no code changed, nothing committed, agent not redeployed, no key printed).
Scope: the application as it exists in the working tree today. Data: only the synthetic documents in `reference/demo_documents/` and synthetic claims in `data/`.
Where I could not verify something the word is **unknown**.

---

## 1. Summary

1. ARC is a FastAPI app that lets a customer upload claim PDFs, builds a claim from them, and answers questions about it. The money and dates come from Python code. The wording of answers comes from a `gpt-5-mini` agent that reads the policy through Azure AI Search.
2. **It works for the demo path.** Real Azure, three intake runs, six customer questions: all returned correct, stable answers; 1,22,125 / 1,01,625 / 20,500 reproduced from the uploaded PDFs; 369/369 tests pass; the deployed agent (v6) is byte-identical to the local prompt and tools.
3. **It is not ready to be put on the internet.** There is no authentication, no rate limiting, no session expiry, and every chat turn spends Azure money (P0).
4. **The 47 changed or new files are not committed.** The last commit (`090fff2`) predates the intake, the customer UI and most of the tests (P0 for reproducible deployment).
5. The customer UI is clean at the first level, but **"Show more" and the reference popups show officer language**: `Excl03`, `Annexure B`, `C.1.c`, `Def. 5`, and "Estimated insurer payment" (P1). CLAUDE.md says otherwise.
6. `/chat` returns `tool_trace` (with tool arguments) and `chunk_key` to any caller. The `?dev=1` switch only hides them in the browser, the server does not check it (P1).
7. Model-written text is officer-voiced even for customers (the agent prompt says "You help a claims officer"). Example: a customer is told "Verify the insured's first policy inception date" (P1).
8. Document intake is rule-based (pypdf + regex). It has **no model and no Azure call**. It only reads text PDFs in the sample layout; scans and unfamiliar layouts are refused, not guessed.
9. Sessions live in one process's memory: a restart or a second worker loses them. Dependencies are not pinned. No Dockerfile or deployment files exist.
10. CLAUDE.md, README.md and docs/DEMO.md contain about a dozen statements that are no longer true (section 11).

---

## 2. End-to-end workflow

Legend: **CODE** = plain Python/JS here; **MODEL CALL** = a large language model; **AZURE CALL** = an Azure service; **UI** = browser.

### 2.1 Steps

| # | Step | Kind | Where |
|---|---|---|---|
| 1 | Customer opens `/` | UI | `GET /` returns `app/static/index.html` with a strict CSP (`app/main.py:index`) |
| 2 | Page creates a visit | UI + CODE | `POST /sessions {"audience":"customer"}` creates an entry in the in-memory dict `SESSIONS` (`main.py:create_session`); returns `session_id` and `intake:true` |
| 3 | Customer drops PDFs (or "Use sample documents") | UI | `customer.js` sends multipart `POST /sessions/{sid}/documents` (or `/documents/sample`) |
| 4 | Each file is read | CODE | `intake.process_file`: size check, `%PDF` check, pypdf text extraction, page limit, classify by title regex, extract fields by regex. **No model, no Azure.** |
| 5 | Files are stored | CODE | `intake.store` puts `{filename, fields, digest}` in `session["documents"][type]` (memory only; newest file of a type replaces the older) |
| 6 | Customer presses continue / page runs the check | UI + CODE | `POST /sessions/{sid}/intake` calls `intake.build`: cross-checks name, policy number, dates, totals; either `needs_attention` with plain reasons, or builds the claim into `session["claim"]` |
| 7 | First chat message | CODE | Built from the same response: `intake.claim_summary_markdown` + three chips from `intake.suggestions` (which calls the engine `E.assess`). **No model.** |
| 8 | Customer asks a question | UI | `POST /sessions/{sid}/chat {"message": ...}` |
| 9 | Fresh conversation | AZURE CALL | Foundry project `…/api/projects/jashanpreetsingh3999-6322`: `conversations.create` |
| 10 | Model picks a tool | MODEL CALL | Foundry `responses.create` with `agent_reference` = `claims-adjudication-agent-v2` (deployment `gpt-5-mini`, prompt in section 5.1). Input text = claim block + last 4 turns + "Question: …" |
| 11 | Tool runs in the backend | CODE (+ AZURE CALL) | `registry.call_tool`: `assess_claim`, `check_waiting_period`, `lookup_non_medical_item`, `get_claim_summary` are pure Python. `search_policy` = 1 embedding call (Azure OpenAI `text-embedding-3-large`, key auth) + 1 hybrid+semantic query (Azure AI Search `claims-search-37`, index `claims-kb-v2`, key auth). `get_clause` = 1 Search filter query per chunk |
| 12 | Tool result goes back | MODEL CALL | next `responses.create` with `function_call_output` items; loop up to `MAX_AGENT_STEPS`=8 |
| 13 | Model calls `final_answer` | MODEL CALL | Structured answer (type, headline, points, next steps, citations, result_id) |
| 14 | Validate | CODE | `registry.validate_final`: citation validity, result_id, decision wording, internal terms, length caps. Up to 2 rejections go back to the model; then it is accepted with unverifiable citations stripped |
| 15 | Render | CODE | `registry.render_final` → `scrub_final` → `render.py` / `compact.py` (Markdown, summary, sections, evidence via Search `get_by_chunk_id`) |
| 16 | Clean up | AZURE CALL | `conversations.delete` (best effort, 5 s) |
| 17 | Show | UI | `customer.js` renders `summary_markdown`, "Show more" sections, reference chips, three suggestion chips |
| 18 | Add a document later | UI + CODE | Same as steps 3 to 6 on the same session; the check re-runs and the claim is rebuilt |

### 2.2 Sequence (a): upload → intake → first message

```
Browser                     FastAPI (one process)                     Azure
  | POST /sessions -------------> SESSIONS[sid] = {claim:None,...}
  |<------- {session_id, intake:true}
  | POST /sessions/sid/documents (multipart, up to 15 files)
  |                               BodyLimitMiddleware (<=15 MB)
  |                               per file: read_pdf -> classify -> extract   [pypdf, regex]
  |                               store in SESSIONS[sid]["documents"]
  |<------- {files:[{status,type,message}], checklist:[10 items]}
  | POST /sessions/sid/intake --> intake.build: checks + claim
  |                               E.assess(claim) for the chips        [pure Python]
  |<------- {status:"ready", claim:{...summary}, summary_markdown, suggestions[3], missing}
  |        (needs_attention -> {reasons:[{message, documents}]} and session["claim"]=None)
  (no Azure request happens anywhere in this sequence)
```

### 2.3 Sequence (b): one chat question

```
Browser            FastAPI                    Foundry (gpt-5-mini)         AI Search / OpenAI embeddings
  | POST /chat ---> turn_scope(60 s)
  |                 conversations.create ------> ok
  |                 responses.create(claim block + history + question) --> function_call
  |                 call_tool(assess_claim)  [Python engine]
  |                   + get_by_chunk_id x N ---------------------------------------------> N filter queries
  |                 responses.create(function_call_output) --> function_call final_answer
  |                 validate_final  (reject -> another responses.create, at most 2)
  |                 render_final: scrub -> render.py/compact.py --(get_by_chunk_id x M)--> M filter queries
  |                 conversations.delete
  |<-- {status, answer_type, answer_markdown, summary_markdown, sections, citations,
  |     suggestions, trace_summary, tool_trace}
  Policy question: the tool is search_policy = 1 embedding + 1 hybrid query, then final_answer.
  Failure: deadline/429/5xx exhausted -> TurnAbort -> status "timeout"|"unavailable" + a "please try again" answer.
```

### 2.4 Sequence (c): adding a document later

```
Browser                       FastAPI
  | (chat screen) "Add a document" -> file picker
  | POST /sessions/sid/documents ---> process_file, store (replaces same type; "replaced": true)
  | POST /sessions/sid/intake ------> build(): re-check everything, rebuild session["claim"]
  |<-- ready + new summary_markdown (final message lists the added document names)
  | or needs_attention: session["claim"]=None, the previous claim is dropped
  Next /chat turn uses the rebuilt claim (there is no cache to invalidate; history is kept).
```

---

## 3. Module map

Runtime = used when the demo server runs. All line counts are from `wc -l`.

### app/ (Python)

| File | Lines | Purpose | Runtime |
|---|---:|---|---|
| `app/main.py` | 270 | FastAPI app: routes, body-size middleware, error handlers, page headers, in-memory `SESSIONS` | yes |
| `app/config.py` | 57 | Reads every setting from environment / `.env` (defaults in section 8) | yes |
| `app/intake.py` | 431 | PDF reader, classifier, field extractor, cross-checks, claim builder, chips | yes |
| `app/resilience.py` | 140 | Turn deadline, per-call timeouts, retry/backoff for 429 and 5xx, `TurnAbort` | yes |
| `app/observability.py` | 46 | One JSON log line per turn (no question, no claim data) | yes |
| `app/agent/instructions.py` | 35 | The agent system prompt `SYSTEM_PROMPT` | yes for `create_agent.py`; the running server does not read it (the deployed agent holds a copy) |
| `app/agent/runner.py` | 189 | `FoundryAgent` (real tool loop), `OfflineAgent` (keyword stand-in), history/claim blocks | yes (`FoundryAgent`); `OfflineAgent` only when `AGENT_MODE=offline` |
| `app/retrieval/base.py` | 153 | `Chunk`, `Retriever`, `LocalRetriever` (BM25), `best_window` excerpts | yes (types); `LocalRetriever` only offline/tests |
| `app/retrieval/azure_search.py` | 84 | `AzureSearchRetriever`, `embed()` | yes |
| `app/tools/claims_engine.py` | 335 | Deterministic checks: waiting periods, room rent, non-medical, documents, amounts | yes |
| `app/tools/evidence.py` | 58 | Clause reference (`C.1.b`) → chunk id | yes |
| `app/tools/registry.py` | 328 | Tool schemas, `call_tool`, `validate_final`, `render_final`, `trace_summary` | yes |
| `app/rendering/render.py` | 509 | Full Markdown answer templates per answer type (locked by 19 golden files) | yes |
| `app/rendering/compact.py` | 367 | Short summary + "Show more" sections, customer/officer wording | yes |
| `app/rendering/scrub.py` | 84 | Finds/removes result ids, chunk keys, tool names in model text | yes |
| `app/__init__.py`, `agent/__init__.py`, `rendering/__init__.py`, `retrieval/__init__.py`, `tools/__init__.py` | 0 | package markers | n/a |

### app/static/ (UI)

| File | Lines | Purpose | Runtime |
|---|---:|---|---|
| `index.html` | 100 | Customer page (upload screen + chat screen) | yes (`/`) |
| `customer.css` | 178 | Customer styling | yes |
| `customer.js` | 602 | Customer page logic: upload, checklist, chat, sections, chips, lost-session recovery | yes |
| `md.js` | 123 | Small HTML-escaping Markdown renderer | yes (both pages) |
| `dev.js`, `dev.css` | 57, 19 | Developer badge and trace panel, loaded only with `?dev=1` | only with `?dev=1` |
| `officer.html`, `arc.js`, `arc.css`, `labels.js` | 76, 440, 204, 34 | Earlier officer console at `/officer` (claim picker, full details) | yes (`/officer`), optional |

### scripts/ (not used by the running server)

| File | Purpose |
|---|---|
| `bootstrap_azure.py` (350) | One-off helper that discovered endpoints, wrote `.env`, created index/upload/role steps |
| `check_env.py` | Validates `.env` and connectivity |
| `create_index.py`, `upload_chunks.py` | Create the `claims-kb-v2` index, embed and upload 186 chunks |
| `create_agent.py` | Creates a new Foundry agent version from `SYSTEM_PROMPT` + `SCHEMAS` (**not run in this audit**) |
| `eval_retrieval.py`, `eval_agent.py` | Retrieval and agent evaluations; `eval_agent.py` writes `examples/eval_report.md` and failure transcripts |
| `demo_check.py` | 8-step officer demo against the live agent |
| `chat_cli.py` | Terminal chat |
| `render_samples.py`, `render_examples.py` | Regenerate `examples/` from the engine/agent |
| `run_demo.sh` | Starts uvicorn on port 8765; refuses unless `RETRIEVER=azure` and `AGENT_MODE=foundry` |
| `take_screenshots.py`, `take_screenshots.mjs` | Playwright screenshots (customer, officer) into `docs/screenshots/` |

### Other

`tools/chunk_policy.py`, `tools/chunk_generic.py` (chunk the policy PDF; not runtime), `data/` (186 chunks, 12 sample claims, rules; runtime reads `sample_claims.json` and `rules/*.json`), `reference/demo_documents/` (10 synthetic PDFs; runtime for "Use sample documents"), `tests/` (369 tests).

---

## 4. Document intake in detail

**Text extraction:** `pypdf` 6.19.0 (`intake.read_pdf`). Local, deterministic, no OCR. A PDF with under 40 non-space characters is treated as a scan and refused (`no_text`).

**Classification:** first two non-banner lines are lower-cased and matched against `_TITLE_RULES` in order (first match wins): policy schedule, claim form, discharge summary/card, final/itemised bill, pharmacy/medicine bill, prescription, diagnostic/laboratory/imaging, consultation/outpatient, KYC, NEFT/cancelled cheque, photo ID/passport/Aadhaar, hospital registration, implant, MLC/FIR, alcohol, ambulance. No match → `unrecognised`.

**Exact prompt and JSON schema sent to a model: none.** Intake never calls a model. The output is a Python dict per document, for example `policy_schedule` → `policy_number, insured_name, first_inception, base_si, bonus, policy_uin, policy_period, plan, aggregate_deductible, copay_percent, protect_benefit_opted`; `claim_form` → `patient_name, hospital, hospital_network, admission, discharge, is_accident, diagnosis, icd10, procedure, pre_existing, claimed_amount, not_enclosed`; `final_bill_receipts` → `lines[{section, description, qty, rate, amount}], total, admission_date, discharge_date, room_rate_per_day, room_days`; and so on (`intake.extract`). Roadmap item 8 (Azure Content Understanding) is **not built**.

**Validations** (`intake.read_pdf`, `intake.build`):
- File: ≤ 5 MB, must start with `%PDF`, not encrypted, ≤ 20 pages, ≥ 40 text characters, at most 15 files per upload, whole upload ≤ 15 MB.
- Claim: policy schedule, claim form and final bill must be present; plan/sum insured/start date readable; hospital dates readable; bill lines readable and **sum equals the bill total**; every document's name equals the policy holder's; policy numbers equal; admission/discharge dates equal across claim form, discharge summary and bill; claim form amount equals bill total.

**Failure behaviour (what the customer reads, verbatim from `FILE_PROBLEMS` and `build`)**

| Case | Message |
|---|---|
| not a PDF / empty | "This isn't a PDF. Please upload PDF files for now." |
| over 5 MB | "This file is larger than 5 MB. Please upload a smaller copy." |
| scan / no text | "We couldn't read any text in this file. It may be a scan or a photo. Please upload a text PDF for now." |
| encrypted | "This file is password-protected. Please upload an unlocked copy." |
| unreadable | "We couldn't open this file. Please check that it opens on your device and try again." |
| too many pages | "This file has too many pages for now (20 at most)." |
| unrecognised | "We couldn't tell what this document is. Is it one of the documents in the checklist?" |
| missing essential | "We couldn't find your policy schedule (it shows your plan and cover). Please add it." (also final bill, claim form) |
| name mismatch | "The name on your final hospital bill with receipts (Rohan Varma) doesn't match your policy (Rohan Verma). Please check you uploaded the right document." |

**Storage:** in the Python process (`SESSIONS[sid]["documents"]`); the PDFs themselves are **not** kept (only extracted fields, file name and a 12-char hash). Nothing on disk. Lost on restart.

**Logging:** one line per upload: session id, number of files, number recognised (`main.py:_read_all`). No names, no content. Verified in the server log seen earlier in this session.

**Timings measured (this machine, 10 PDFs, 3 runs):** upload+read 35 / 31 / 31 ms; intake build 9 / 9 / 9 ms.

---

## 5. The agent

### 5.1 System prompt (verbatim, `app/agent/instructions.py`; deployed v6 is identical, sha256 prefix `0aff2f13eca3`, 6,895 characters)

```
You are the Claims Adjudication Assistant for a health insurer. You help a claims officer understand a claim under the HDFC ERGO my:Optima Secure policy wording. You assist; the human officer decides.

HOW YOU WORK
You have tools. You do not write the final answer as plain text. Every turn ends with ONE call to final_answer, and the backend formats it. Never write markdown, tables or headings yourself.

RULES YOU MUST FOLLOW
1. Policy content comes only from tool results (search_policy, get_clause). Never answer from memory about what the policy says.
2. Dates, months, days, rupee amounts and percentages come only from tools (check_waiting_period, assess_claim). Never calculate them and never type numbers for claim results yourself.
3. Cite with chunk_key values that tools returned this turn, copied exactly (they look like "doc_id:chunk_id"). Never invent a citation. A result_id (like "claim-1a2b3c4d" or "waiting-1a2b3c4d") is NOT a citation: it goes only in the result_id field and never in citations. For claim_assessment and deduction_explanation leave citations empty; the backend adds the evidence.
4. If the retrieved passages do not answer the question, do not guess. Use answer_type "insufficient_information" and say what is missing and where it would be found (Policy Schedule, insurer website, claims officer). Before you conclude this for any question about insurance, the policy or a claim, call search_policy at least once (twice with different words if the first results are off topic); never say the wording is silent without having looked. Use insufficient_information only when the wording says nothing relevant to the question, such as insurer statistics, hospital network membership, premiums and other facts held outside the wording; do not answer such a question with yes or no. When the wording answers the question in part, for example the rule is clear but the dates or facts needed to apply it are missing, use the answer type that fits (coverage_answer, waiting_period_answer and so on), state what the wording says with citations, and list what is missing in next_steps.
5. Do not approve or reject a claim. Use wording like "appears covered", "likely payable", "flagged for review".
6. The policy version is chosen for you from the claim's UIN. If a question is about a different product or insurer, say the knowledge base does not cover it.
7. Be brief and plain. No filler, no apologies, no marketing language. Amounts are in Indian rupees.

CHOOSING THE WORKFLOW
- "Assess / check / evaluate this claim", "what will be paid", "is this claim payable": call assess_claim, then final_answer(answer_type="claim_assessment", result_id=...). If the user asks "what if ..." pass what_if to assess_claim.
- "Why was X deducted / reduced / not paid", "explain the room rent deduction": call assess_claim (with what_if only if they ask a hypothetical), then final_answer(answer_type="deduction_explanation", result_id=..., focus=room|associated|non_medical|hold|deductible|all).
- "Is <treatment> covered?" / "Is <item> payable?": call search_policy (and get_clause for a specific clause, lookup_non_medical_item for billed items). get_clause takes a clause number exactly as printed in the policy, such as "C.1.b" or "B.1.1.1 Note iii"; it does not accept chunk_keys or chunk ids, so when unsure of the number use search_policy instead. Then final_answer(answer_type="coverage_answer") with a verdict and 2 to 5 points. Each point cites the passage it rests on. If the answer depends on dates, also call check_waiting_period.
- "Waiting period for X" or "has the waiting period been served" (dates given): call check_waiting_period and search_policy, then final_answer(answer_type="waiting_period_answer", result_id=<from check_waiting_period>). If no dates were given, ask for them in next_steps and explain the applicable waiting periods from the policy with citations.
- "Which documents do I need / what is missing": if a claim is loaded, call assess_claim then final_answer(answer_type="documents_answer", result_id=...). Otherwise search_policy for the claim-documents clause and use documents_answer with points.
- "What does <term> mean" (room rent, hospitalization, pre-existing disease, associated medical expenses): search_policy, then final_answer(answer_type="definition_answer") with a plain-language headline and key points that cite the definition.
- Greetings, thanks, or questions that have nothing to do with health insurance or this claim: final_answer(answer_type="general_answer") with a one-line headline and no citations. A question about insurance that the wording does not answer is insufficient_information, not general_answer.

WRITING final_answer
- headline: at most 2 short sentences that answer the question directly. Lead with the answer.
- When a passage lists many items (conditions, procedures, exclusions), read the whole list. The point that supports your answer names the entry that matches the treatment or item asked about, in the detail, in the wording the list uses, and cites the list; do not build the point on a different entry that merely appears first or sounds related. A list may have separate parts (for example illnesses and surgical procedures), so make sure the entry you name is in the part that fits.
- When the question asks for a number (days, months, a percentage, a limit), the headline states that figure exactly as the retrieved passage gives it, with its unit and what it is counted from. Never write "the prescribed time limit" or "as specified" in place of a figure the passage contains. If the excerpt you were shown stops before the figure, call get_clause for that clause and read it there; if the wording really gives no figure, say so.
- points: at most 3, most important first, each detail at most 150 characters (one plain sentence). Pick the three that matter most to the officer; a red flag (status problem) always makes the cut. status: ok (favourable), warning (condition or uncertainty), problem (excludes or blocks), info (context).
- coverage_answer verdict: covered, covered_with_conditions, not_covered, depends, or insufficient_information.
- next_steps: things for the officer to check, verify, confirm, request or flag, each starting with such a verb. Never a decision or an instruction to decide: do not tell the officer to pay, not pay, admit, approve, reject, deny, decline or settle, and do not write "mark as payable / non-payable". The officer decides; you point at what to look at. At most 3 next_steps. caveats: limits of the answer. Keep each under 200 characters.
- For claim_assessment and deduction_explanation the backend prints all numbers from the tool result. Keep caveats free of numbers.

IF A TOOL RETURNS AN ERROR
Read the message, fix the arguments or choose another tool. If final_answer is rejected, fix exactly the problems listed and call it again.
```

Observation: the prompt is written for an officer. It is the same for both audiences; `audience` is never passed to the model (`runner._claim_block`, `_history_block`).

### 5.2 Tools (`app/tools/registry.py:SCHEMAS`; deployed schemas identical, `strict=False`)

| Tool | Parameters (required in bold) | Runs |
|---|---|---|
| `search_policy` | **query** (string), top_k (int, default 5, max 8) | Azure: 1 embedding + 1 hybrid/semantic query, filtered by the claim's UIN |
| `get_clause` | **clause_ref** (string, e.g. "C.1.b", "B.1.1.1 Note iii") | Azure Search filter query per resolved chunk |
| `check_waiting_period` | **first_policy_inception**, **admission_date** (ISO), diagnosis, procedure, is_accident, pre_existing, prior_continuous_coverage_months | Python |
| `lookup_non_medical_item` | **item** | Python (Annexure B table) + evidence queries |
| `get_claim_summary` | none | Python |
| `assess_claim` | what_if (object; allowed keys listed in the schema) | Python engine + evidence queries |
| `final_answer` | **answer_type** (8 values), **headline**, verdict, points[{**label**, **status**, **detail**, citations}], next_steps[], caveats[], citations[], result_id, focus | Validated, then rendered |

### 5.3 Validation rules (`registry.validate_final` / `_final`)

Each of these rejects at most once per turn (a flag in `TurnContext`), with a total limit of 2 rejections; on the third attempt the answer is accepted with unverifiable citations removed and a caveat added.
1. `answer_type` in the 8 allowed types; headline present and under 500 characters.
2. `general_answer` after `assess_claim` was called → rejected once.
3. `next_steps` matching the decision regex (pay, reject, approve, "do not pay", "mark as payable", …) → rejected once.
4. Length caps: headline ≤ 2 sentences (not for claim types), ≤ 3 points, each ≤ 150 characters, ≤ 3 next steps → rejected once.
5. Internal terms (result ids, chunk keys, tool names) in headline/points/next steps/caveats → rejected once; `scrub_final` removes what remains.
6. `claim_assessment` and `deduction_explanation` need a `result_id` from `assess_claim` of this turn.
7. Every citation must be a `chunk_key` returned by a tool this turn.
8. Q&A types need at least one citation (except insufficient_information and result-backed waiting/documents answers); `coverage_answer` needs a point; point detail ≤ 600 characters.

### 5.4 Timeouts and retries (`app/resilience.py`, `app/config.py`)

Turn deadline 60 s (`TURN_DEADLINE_S`). Model call timeout 40 s, capped by what is left of the deadline. Search 10 s, embeddings 10 s. Retries: 3 after the first try, only for 429/408/5xx and connection errors, exponential backoff 0.5 s doubling to 8 s with jitter, `Retry-After` honoured. A model read-timeout is **not** retried. SDK retries are switched off (`max_retries=0`, `retry_total=0`). A turn that cannot finish returns `status: timeout|unavailable` with a "please try again" text and is not added to history. The browser waits 80 s (`customer.js` `TIMEOUT_MS`).

### 5.5 History and claim passing

A **fresh Foundry conversation per turn**. The backend keeps `session["history"]` (user text, answer type, headline) and adds the last 4 turns as plain text. The claim is passed as a one-line text block: claim id, insured name, plan, procedure, diagnosis (`_claim_block`). Those strings come from the uploaded PDFs (see risk P1-7).

### 5.6 How the "customer voice" is implemented

Only in deterministic code, not in the model: `session["audience"]` is set at `POST /sessions`; `render_final` passes it to `render.py`/`compact.py`, which swap wording (`REC_TEXT_CUSTOMER`, "Room rent above your plan's limit", "Please send the missing …", "your policy doesn't cover", "held" instead of "on hold"), drop clause numbers from the first-level summary, and rebuild documents-answer next steps from the checklist. Model-written headlines, points, next steps and notes are **not** rewritten. `customer.js` shows no developer words unless `?dev=1`.

---

## 6. Customer-visible strings, officer language, and API responses

### 6.1 Strings written by the code

- Page: title "ARC: AI-powered Review of Claims"; "Upload your claim documents"; "Add all your documents at once. We'll read them and tell you if anything is missing."; "Drag and drop your documents here"; "or choose files · PDF, up to 5 MB each"; "Documents we need" with 10 labels; "Please take a look"; "Continue to my claim"; "Add or replace files"; "Use sample documents"; "Demo: please upload only sample documents."; "Ask about your claim"; "Add a document".
- Footer (every screen): "This is an AI-assisted estimate, not a claim decision. A claims officer makes the final decision."
- Recommendation lines: "Likely eligible — a claims officer makes the final decision" · "Likely eligible once your documents are complete" · "Likely not payable — a claims officer will confirm" · "A claims officer needs to review this — provisional estimate".
- Errors in the browser: "That took too long, so nothing was changed. Please try again." · "We couldn't reach the server. Please check your connection and try again." · "That is too much to send at once. Please add fewer or smaller files." · "Something went wrong on our side. Please try again in a moment." · "The server was restarted, so your visit ended. We've started a new one." · "This ARC service is out of date, so uploads aren't available yet. If you run ARC yourself, restart it with scripts/run_demo.sh and reload this page."
- Turn failure answers (from `runner._ABORT_ANSWERS`, shown to the customer): "## This is taking longer than expected … If it keeps happening, tell your administrator." · "## The assistant is temporarily unavailable — The Azure AI service is busy or not responding …" · "## I could not complete that request …".
- Intake messages: section 4.

### 6.2 Remaining officer language or internal terms a customer can see (measured, section 10.3)

| Where | What appears |
|---|---|
| Footer and recommendation lines | "claims officer" (intentional, it is the disclaimer) |
| "Show more" → Waiting period | `Excl03`, `Excl02`, `Excl01`, "Policy C.1.c, p.30" |
| "Show more" → Coverage / Room rent / Non-payable | "Policy B.1.1, p.11", "A.1.2 Def. 5", "Annexure C, p.50", "Annexure B", "C.3.k", table column "Annexure B" with "#56" numbers |
| "Show more" → Full estimate | "Non-payable items (Annexure B)", "Estimated insurer payment" |
| Summary of deduction explanation | "Estimated insurer payment" (Q2 and Q4 of section 10.3) |
| Evidence list and reference popups | clause numbers, quoted policy passages containing `Code – Excl03`, "ANNEXURE" |
| Model-written notes/next steps | "Verify the insured's first policy inception date in the Policy Schedule." · "Confirm whether the cataract event is being claimed as arising from an accident." · "check the Policy Schedule for any specific exclusions or endorsements…" (officer voice; lower-case first letter) |
| UIN | Not seen in any customer answer during the audit; `policy_uin` is in the `/chat` history and claim summary API (`/intake` returns `claim.policy_uin`). Screen use: **unknown** beyond the six questions |
| Tool names / result ids / chunk keys | Not found in any customer-visible text. `chunk_key` is present in the `/chat` JSON `citations` (not displayed) |

The first-level summary was clean of internal terms in all six answers except "insurer" in two.

### 6.3 What the endpoints return, and is anything stripped without `?dev=1`?

`?dev=1` is a **browser-only** switch (`customer.js` loads `dev.js`). **The server never looks at it**, so nothing is stripped on the server for any caller.

| Endpoint | Returns | Internal fields present for everyone |
|---|---|---|
| `GET /health` | `status, live, retriever, agent_mode, default_uin` | UIN, retriever/agent modes |
| `GET /samples` | ids, titles, purposes of 12 sample claims | none |
| `POST /sessions` | `session_id, intake:true` | none |
| `POST /sessions/{sid}/documents` (+`/sample`) | `files[{filename,status,type,label,replaced,message}]`, `checklist[10]` | none |
| `POST /sessions/{sid}/intake` | `status, reasons, checklist, missing`, and when ready `claim{claim_id,insured,plan,diagnosis,procedure,admission,discharge,claimed_amount,policy_uin}`, `summary_markdown`, `suggestions` | `policy_uin` |
| `POST /sessions/{sid}/chat` | `status, answer_type, answer_markdown, summary_markdown, sections, citations, suggestions, trace_summary, tool_trace` | **`tool_trace`** (tool name, **arguments**, ok, ms, problems) and `citations[].chunk_key` |
| `POST /sessions/{sid}/claim`, `POST /assess` | loaded claim summary; deterministic assessment | none |
| `/docs`, `/openapi.json` | interactive API docs | enabled (HTTP 200) |

Example of what `tool_trace` carries: `{"tool":"search_policy","args":{"query":"cataract surgery waiting …"}, …}`.

---

## 7. Azure usage

| Call | Resource | Deployment / index | Auth | Timeout / retry |
|---|---|---|---|---|
| Agent conversation + responses | Foundry resource `claims-agent-project-res` (Korea Central), project `jashanpreetsingh3999-6322` | agent `claims-adjudication-agent-v2` v6, model deployment `gpt-5-mini` | `DefaultAzureCredential` (az login now; needs the Foundry User role) | 40 s per call inside a 60 s turn; retry 429/5xx up to 3; no retry after a read timeout |
| Embeddings | Azure OpenAI endpoint (`AZURE_OPENAI_ENDPOINT`), deployment `text-embedding-3-large`, `dimensions=1536` | — | API key `AZURE_OPENAI_KEY` | 10 s, retried |
| Search | `claims-search-37` (Free tier, Central India) | index `claims-kb-v2`, 186 chunks, semantic config `default`, vector field `embedding` | API key `AZURE_SEARCH_KEY` | 10 s, retried |
| Storage `claimsagentjp2026`, Application Insights, Log Analytics | exist | — | — | **not used by the app** |

**Calls per upload:** 0 model, 0 search, 0 embedding (verified: intake imports no Azure client).

**Calls per chat question** (Azure requests):

| Question type | Model-service requests | Embeddings | Search requests |
|---|---:|---:|---:|
| Claim answer (assess, deduction, documents) | 4 (create conversation, 2 × responses, delete) | 0 | 11 to 25 (measured with a counting stand-in over the same tools and renderers: 25, 12, 11 for the three types; the real trace was `assess_claim > final_answer`) |
| Policy question | 4 (occasionally 5 to 6 with `get_clause` or a rejected `final_answer`) | 1 per `search_policy` | 1 per search + about 1 per evidence chunk |

**Cost drivers:** model tokens (input includes the full system prompt, tool schemas and tool results on every request; **token usage is not logged, so cost per turn is unknown**); the Free-tier Search service (no cost, but no SLA and a small quota; effect of many simultaneous users: **unknown**); the embedding deployment is tiny. There is no budget cap in the code.

---

## 8. Configuration and secrets

All read in `app/config.py` (`.env` loaded from the project root; the shell wins over `.env`).

| Variable | Default | If missing or wrong |
|---|---|---|
| `RETRIEVER` | `local` | `local` runs BM25 over the JSONL (no Azure); demo script refuses unless `azure` |
| `AGENT_MODE` | `offline` | `offline` uses the keyword stand-in, not the real agent; demo script refuses unless `foundry` |
| `DEFAULT_UIN` | `HDFHLIP25041V062425` | wrong UIN → searches return nothing |
| `AZURE_SEARCH_ENDPOINT` / `AZURE_SEARCH_KEY` | empty / empty | `azure` retriever fails at first search (error class only in log); **not checked at startup** |
| `AZURE_SEARCH_INDEX` | `claims-kb-v2` | wrong name → search errors |
| `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_KEY` | empty / empty | embeddings fail → `search_policy` errors (claim answers keep working) |
| `AZURE_OPENAI_API_VERSION` | `2024-10-21` | |
| `EMBEDDING_DEPLOYMENT` | `text-embedding-3-small` (`.env` sets `-large`) | wrong name → embeddings fail; a wrong default would silently differ from the index |
| `EMBEDDING_DIMENSIONS` | `1536` | must match the index vector size |
| `FOUNDRY_PROJECT_ENDPOINT` | empty | agent creation fails on the first chat (agent is created lazily, not at startup) |
| `MODEL_DEPLOYMENT`, `AGENT_NAME` | `gpt-5-mini`, `claims-adjudication-agent-v2` | wrong agent name → chat fails |
| `MAX_AGENT_STEPS` | `8` | |
| `TURN_DEADLINE_S`, `MODEL_TIMEOUT_S`, `SEARCH_TIMEOUT_S`, `EMBED_TIMEOUT_S` | 60, 40, 10, 10 | |
| `MAX_RETRIES`, `BACKOFF_BASE_S`, `BACKOFF_CAP_S` | 3, 0.5, 8 | |
| `MAX_REQUEST_BYTES`, `MAX_UPLOAD_BYTES` | 262144, 15 MB | 413 above |
| `CORS_ORIGINS` | empty | empty = no cross-origin browser access |
| `LOG_LEVEL` | `INFO` | |
| `PORT`, `ENV_FILE` | 8765, `.env` | used only by `scripts/run_demo.sh` |

Local `.env` today (values masked): the two retriever/agent switches are `azure`/`foundry`; the Search key (52 characters) and OpenAI key (84 characters) are set; endpoints and names are set as above.

**Can secrets reach git, logs or responses?**
- Git: `.env` is ignored (`.gitignore:1`), was never tracked, and never appears in history (0 commits touch `.env`). A scan of tracked and untracked files found no key-like assignment; one long string in `scripts/bootstrap_azure.py:300` is a test placeholder. `.env.example` has empty keys.
- Logs: `observability.log_turn` writes only ids, counts, tool names, milliseconds, answer type, error class. The upload log writes counts. No key is ever formatted into a log call (`grep` of `search_key`/`openai_key` finds only the two client constructors). `scripts/check_env.py:59` prints exception text (first 200 characters) for a failed embedding call; whether an SDK exception could contain a key is **unknown**, though the SDKs normally do not.
- Responses: error bodies contain only a code and a fixed message (`main.py` handlers). No stack traces. Verified with a 413, a 422 and a 404.

---

## 9. State and limits

| Topic | Fact |
|---|---|
| Sessions | Python dict `SESSIONS`, one per process. **No expiry, no size cap.** Lost on restart. With two workers or two instances a session created on one is unknown to the other (`404 Unknown session`; the UI then says "The server was restarted…"). Multi-worker behaviour was inferred from the code, **not run** |
| Concurrency | One shared `FoundryAgent` and retriever per process; endpoints are sync and run in a thread pool. Behaviour under simultaneous users: **unknown** (not load-tested); a 3-worker evaluation earlier hit the model quota (429) |
| Upload limits | 5 MB per file, 20 pages, 15 files per request, 15 MB per request (checked: 6 MB file refused, 16 files → 422, 20 MB body → 413) |
| Other body limit | 256 KB on every other route |
| Chat message | 1 to 2,000 characters |
| Rate limiting | **none** |
| Authentication | **none**. Anyone who can reach the port can create sessions, upload, load any claim JSON (`POST /claim`), and chat |
| CORS | Off unless `CORS_ORIGINS` is set. A foreign-origin POST still executes, only the browser blocks reading the reply (no cookies or tokens exist, so this matters little) |
| Security headers | CSP (`default-src 'none'; script-src 'self'; style-src 'self'; …; frame-ancestors 'none'`), `X-Content-Type-Options`, `Referrer-Policy`, `Cache-Control: no-cache` **only on `/` and `/officer`**. API routes and `/static/*` have none |
| Bind address | `uvicorn` default = 127.0.0.1 (local only) |
| PDF parser | pypdf with no CPU/time limit; size and page limits only |

---

## 10. Evidence (everything below was actually run today)

### 10.1 Test suite

`python -m pytest tests -q` → **369 passed, 1 warning, 0 skipped, 0 failed in 18.5 s** (Node and Chrome present, so the label/markdown tests and the 26 Playwright browser tests ran). Per file: api 6, compact 42, customer_ui 8, customer_wording 17, e2e_customer 26, engine 27, eval_transcript 2, general_answer_guard 6, hardening 27, intake 46, intake_api 18, internal_terms 51, length_caps 30, presentation 23, registry_and_agent 9, run_demo 11, web_ui 20. All tests are offline: `tests/conftest.py` forces `RETRIEVER=local`, `AGENT_MODE=offline`. **They therefore do not exercise the real Azure path**; that is covered by 10.2 to 10.4.

### 10.2 Intake on the 10 sample PDFs (live server on port 8790, real HTTP multipart uploads, 3 runs)

- All 10 files recognised every time: claim_form, consultation_papers (previous_consultation_papers), discharge_summary, hospital_bill (final_bill_receipts), kyc_form (kyc), lab_report (diagnostic_reports_bills), neft_form, pharmacy_bills (pharmacy_bills_prescription), photo_id_proof (photo_id_age_proof), policy_schedule.
- Checklist: 9 received, 1 partial (pharmacy bills: prescription missing). `missing = ["The doctor's prescription for your pharmacy bills"]`. `reasons = []`. `status = ready`.
- Extracted key fields (claim): insured Rohan Verma; plan Optima Lite; sum insured ₹5,00,000; diagnosis Acute appendicitis; procedure Laparoscopic appendectomy; admission 2025-09-10T14:30; discharge 2025-09-14T11:00; claimed amount 1,84,500; UIN HDFHLIP25041V062425; claim id `CLM-20250910-0001` (built from admission date and last 4 digits of the policy number; the hand-written sample uses `SYN-CLM-007`).
- Timings: upload 35 / 31 / 31 ms; intake 9 / 9 / 9 ms. The three runs produced identical claims.
- Azure: intake makes **no** Azure call, so "against real Azure" applies only to the checks in 10.3.
- Numbers: running the deterministic engine on the intake-built claim gave recommendation `likely_eligible_pending_documents`, estimated payable **1,22,125**, confirmed now **1,01,625**, held pending **20,500**; deductions: room 12,000, associated 37,875, non-medical 12,500. All 23 bill lines (description, category, amount) are identical to the hand-derived TC07 claim. The real agent then quoted the same figures (10.3). **Equal to the expected 1,22,125 / 1,01,625 / 20,500: yes.**

### 10.3 Six customer questions through the real agent (live: Azure Search + Foundry agent v6; audience customer; one session, sample documents)

Common: HTTP 200, `status: ok` for all six. First-level text shown below is exactly `summary_markdown`; "Show more" sections and reference popups are listed separately. Suggestion chips are shown after each answer.

**Q1 "How much will be paid?"** (claim_assessment, 18.4 s, tools `assess_claim > final_answer`)
```
**Claim CLM-20250910-0001** · Rohan Verma · Optima Lite · Laparoscopic appendectomy for Acute appendicitis

🟠 **Likely eligible once your documents are complete**

| Estimated payment | Confirmed today | Held for documents |
|---:|---:|---:|
| **₹1,22,125** | **₹1,01,625** | **₹20,500** |

**Main reasons**
1. Room rent above your plan's limit: −₹49,875
2. Prescription missing: ₹20,500 held until it arrives
3. 12 non-medical items your policy doesn't cover: −₹12,500

**Next steps**
- Please send the missing prescription.
```
Chips: "Why was my room rent reduced?" · "What documents are missing?" · "Which items are not payable?". "Show more" sections: Coverage, Waiting period, Room rent working, Non-payable items (12), Documents (8 of 9 complete), Full estimate, Notes, Evidence (8). Internal terms in them: `B.1.1`, `C.1.c`, `Excl01/02/03`, `Annexure B/C`, `C.3.k`, `Def.`, "Estimated insurer payment".

**Q2 "Why was my room rent reduced?"** (deduction_explanation, 13.4 s, `assess_claim > final_answer`)
```
**Room rent: proportionate deduction**
1. **Plan limit** — Optima Lite pays room rent up to 1% of the ₹5,00,000 base sum insured per day = **₹5,000/day**.
2. **Bill** — ₹8,000/day for 4 days.
3. **Proportion** — ₹5,000 ÷ ₹8,000 = **62.5%**.
4. **Room charges** — ₹32,000 × 62.5% = ₹20,000 → deduction **₹12,000**.
5. **Associated medical expenses** (consultation, OT, nursing, anaesthesia) — ₹1,01,000 × 62.5% = ₹63,125 → deduction **₹37,875**.
6. **Not reduced** — pharmacy, consumables and diagnostics (₹39,000).

**Result** — Estimated insurer payment: **₹1,22,125** (₹1,01,625 confirmed today)
```
Chips: "What documents are missing?" · "Which items are not payable?" · "What happens next?". Term at first level: "insurer". Evidence section: `B.1.1.1 Note iii`, `A.1.2 Def. 5`, `Annexure C`.

**Q3 "What documents are missing?"** (documents_answer, 16.1 s, `assess_claim > final_answer`)
```
Missing document: medicine/pharmacy bills with the treating medical practitioner’s prescription. Supplying these may release the amount currently held on the claim.

**8 of 9 documents complete.**
- ⚠️ Pharmacy bills with prescription — prescription missing

**Next steps**
- Please send the missing prescription.
```
Chips: "Which items are not payable?" · "What happens next?". Sections: Full checklist, Notes, Evidence (E.1.7, E.1.6). Note "8 of 9" here versus 10 documents on the upload screen (different lists).

**Q4 "Which items are not payable?"** (deduction_explanation, 11.9 s) The summary starts with the **room rent** explanation (same six numbered lines as Q2), then "**Non-medical items** — 12 billed items are non-medical extras that your policy doesn't pay for, so ₹12,500 is not payable.", "**Amount held for documents** — ₹20,500 of medicines is held because the prescription is missing. It becomes payable once supplied, moving the estimate from ₹1,01,625 to ₹1,22,125.", "**Result** — Estimated insurer payment: **₹1,22,125** (₹1,01,625 confirmed today)". The question was about non-payable items; the answer leads with room rent (relevance problem, P1). "Show more": table of 12 items with column heading "Annexure B" and numbers such as `#56`; evidence with `Annexure B`, `C.3.k`.

**Q5 "Will my claim be approved?"** (claim_assessment, 11.8 s) Same text as Q1. It does not say "approved" or "rejected"; it says "Likely eligible once your documents are complete" and shows the footer disclaimer. Chip: "What happens next?".

**Q6 "Is cataract surgery covered, and is there a waiting period?"** (coverage_answer, 18.9 s, `search_policy > final_answer`)
```
**🟠 Covered, with conditions**

Cataract surgery is listed and covered but subject to a 24-month waiting period; accident cases are excepted.

- 🔹 **Listed condition** — Cataract and other disorders of lens and retina are explicitly listed under Specified illnesses/procedures.
- 🟠 **Waiting period** — Expenses for the listed conditions/procedures are excluded until 24 months of continuous coverage from first policy inception; accident claims exempt.
- 🔹 **Portability / enhancement** — Prior continuous cover can reduce the waiting; any sum‑insured enhancement may trigger the exclusion afresh.
```
Section "What to check next": "Verify the insured's first policy inception date in the Policy Schedule." · "Confirm whether the cataract event is being claimed as arising from an accident." · "Check for prior continuous coverage (portability) or any recent sum‑insured enhancement." Section "Notes": "check the Policy Schedule for any specific exclusions or endorsements that override standard wording." References: `C.1.b.vi p.29`, `C.1.b p.28`. Chip: "What happens next?". The answer is correct (24 months, accident exception) but it is not a cited *personal* answer: it does not use the customer's own dates, although the claim is loaded (a "coverage" question is answered generically, by design).

**Internal-term check on everything the customer can open:** no tool name, no result id, no chunk key, no UIN, no "chunk" appeared in any text. Officer/internal wording that did appear: see 6.2 (`Excl0x`, `Annexure`, clause numbers, `Def.`, "insurer", officer-voiced next steps). Tally of flagged texts per question (first-level plus Show more plus references): Q1 many, Q2 4 (1 at first level), Q3 0, Q4 many (1 at first level), Q5 many, Q6 3 (clause numbers only).

**Raw responses:** every response also carried `tool_trace` with arguments and `chunk_key` in citations (section 6.3).

### 10.4 Four failure cases (customer's view; live server)

| Case | Request | Result |
|---|---|---|
| **Empty upload** | (a) no `files` field; (b) one 0-byte `empty.pdf`; (c) intake with nothing uploaded | (a) HTTP 422 `{"error":{"code":"invalid_request","message":"files: Field required"}}` (the browser never sends this: the file picker needs a file). (b) 200, file row: "This isn't a PDF. Please upload PDF files for now." (a 0-byte PDF is described as "not a PDF"). (c) `needs_attention` with three reasons: "We couldn't find your policy schedule (it shows your plan and cover). Please add it." · "…the final itemised hospital bill…" · "…your claim form (it shows your hospital dates and diagnosis)…" |
| **Non-PDF** | text file `notes.txt`, PNG bytes named `photo.pdf`, and `%PDF` header + garbage | first two: "This isn't a PDF. Please upload PDF files for now."; third: "We couldn't open this file. Please check that it opens on your device and try again." Nothing stored |
| **Unrelated PDF** | a text PDF with a recipe; a blank-page PDF | recipe: "We couldn't tell what this document is. Is it one of the documents in the checklist?"; blank: "We couldn't read any text in this file. It may be a scan or a photo. Please upload a text PDF for now." Intake then gives the same three "couldn't find" reasons |
| **Name differs across documents** | full sample set with the hospital bill edited to "Rohan Varma" | all 10 files recognised, then `needs_attention`: "The name on your final hospital bill with receipts (Rohan Varma) doesn't match your policy (Rohan Verma). Please check you uploaded the right document." with a "Replace" action for that document; the claim is **not** built (`session["claim"]=None`). A KYC form in the name "Anita Rao" gave the same style of reason. |
| Follow-up | chat "How much will be paid?" in the needs-attention session (possible through the API, the UI hides the chat) | The model answered `insufficient_information` in officer voice: "Cannot determine the amount payable without the claim file and plan details." plus points about caps and sub-limits |

Also checked: 6 MB file → "This file is larger than 5 MB. Please upload a smaller copy."; 16 files → HTTP 422 "Please upload up to 15 files at a time."; 20 MB body → HTTP 413 "That is larger than the 15 MB limit. Please send less at a time." A password-protected PDF, a PDF over 20 pages, and a very large or malicious PDF (parser hang or memory) were **not tested against the live server** (covered by unit tests for the first two; the third is **unknown**).

### 10.5 Other checks

- Deployed agent: versions 1 to 6 exist; latest is 6, model `gpt-5-mini`; prompt and all 7 tool schemas are identical to the local files (read-only call).
- Working tree: HEAD `090fff23fa2e529e1bcaaf1530b3b3ce95a29ab6`; 47 entries in `git status --short` (23 modified, 24 untracked entries, some are directories).
- `/docs` and `/openapi.json` return 200.

---

## 11. Claims versus reality

| Document | Statement | Reality |
|---|---|---|
| CLAUDE.md §6 | `python -m pytest tests -q   # 30 must pass` | 369 tests (CLAUDE.md §2 and README say 369) |
| CLAUDE.md §9 | "Verified offline (pytest, 30 passing)" | 369 |
| CLAUDE.md §9 | "**NOT verified, never run against real Azure**: azure_search.py, create_index.py, upload_chunks.py, create_agent.py, and the real FoundryAgent" | All were run against real Azure and are used by the live demo; evaluation report shows 28 cases × 3 runs passing (per CLAUDE.md §10.4) |
| CLAUDE.md §9 "Things to check first on real Azure" | pre-run checklist | already done; stale |
| CLAUDE.md §10.1 | "Done when pytest shows 30 passed" | stale |
| CLAUDE.md §1 (line 8) and §10.2/§10.3 | steps still worded as to-do items | Already done (steps 2 and 3); stale |
| CLAUDE.md §3 | "no separate `openai-claims-agent` resource is visible in the resource group" | `.env.example` still points `AZURE_OPENAI_ENDPOINT` at `openai-claims-agent`, and earlier in this session such a resource with `text-embedding-3-small`/`gpt-4.1-mini` was seen; the app's `.env` uses the Foundry resource. Conflicting statements |
| CLAUDE.md §1 (line 9) | chunk keys and traces "exist only behind `?dev=1`" | true only in the browser; `/chat` always returns `tool_trace` and `chunk_key` |
| CLAUDE.md §1 | customer page "never [shows] tool names, traces or chunk keys" | true; but it does show `Excl03`, `Annexure B`, clause numbers in "Show more" |
| CLAUDE.md §10.8 | intake "reproduces the TC07 claim exactly" | true for amounts and lines (10.2); the claim id differs (`CLM-…` vs `SYN-CLM-007`) and `protect_benefit_opted` is `False` instead of unset (no effect on the numbers) |
| README "What has and has not been tested" | the four scripts and `FoundryAgent` are "Not tested" | tested on real Azure |
| README "Known limits" | "Claim input is structured JSON. Extracting it from PDFs … is the next stage." | a rule-based PDF intake exists (Content Understanding is still not used) |
| README | `/docs` mentioned as a dev feature | `/docs` is also enabled in the demo server, no switch to disable it |
| docs/DEMO.md line 5 | verified with agent "version 5" | deployed agent is version 6; the DEMO_check was not re-run in this audit (**unknown** whether it still passes 8/8) |
| docs/DEMO.md | describes the 8-step officer flow | the customer page is now `/`; the officer console is `/officer` (DEMO explains this in line 29); its "verified on 20 Sep 2026" date predates the customer and intake work |
| CLAUDE.md §1, `.env.example` | `EMBEDDING_DEPLOYMENT=text-embedding-3-small` in the example, default in `config.py` | real deployment is `-large`; a user who copies `.env.example` gets a name that does not exist in the Foundry resource |
| Code comment `registry.py` docstring | "see agent/foundry_agent.py" | that file does not exist (`agent/runner.py`) |
| `render_examples` / `examples/` | 12 assessments + Q&A samples | modified in the working tree, not committed; freshness vs. current code: **unknown** |

---

## 12. Deployment readiness

### 12.1 Checklist

| Item | Result | Evidence |
|---|---|---|
| Secrets management | **FAIL** | Keys live in a local `.env`; nothing for Key Vault / app settings; `.env` is git-ignored (pass) |
| Managed identity vs keys | **PARTIAL** | Foundry uses `DefaultAzureCredential` (works with a managed identity); Search and embeddings use API keys (`azure_search.py`), no RBAC path coded |
| Foundry role for a hosted identity | **UNKNOWN** | Your user has Foundry User; a hosted identity would need the same role on the project; not attempted |
| Origins / CORS | **PASS** | Off unless `CORS_ORIGINS` set; UI is same-origin |
| Authentication | **FAIL** | none |
| Upload limits | **PASS** | 5 MB / 20 pages / 15 files / 15 MB, verified |
| Request timeouts | **PASS** | 60 s turn deadline, per-call timeouts, verified by tests; no server-level request timeout (uvicorn) set |
| Session persistence | **FAIL** | in-memory, single process, no expiry |
| Logging / monitoring | **PARTIAL** | JSON logs to stderr with useful fields; no token usage; not wired to Application Insights (exists, unused) |
| Health endpoint | **PARTIAL** | `GET /health` exists but reports only config, not Azure reachability; `HEAD /health` returns 405 |
| Start command | **PARTIAL** | `scripts/run_demo.sh` (local, port 8765); no production command, no Dockerfile/Procfile |
| Dependency pinning | **FAIL** | `requirements.txt` uses `>=` lower bounds only; installed versions listed in the appendix |
| `.env` excluded from any build | **UNKNOWN / RISK** | `.gitignore` covers git only; there is no `.dockerignore`, so a naive container build would copy `.env` |
| Cost / rate-limit protection | **FAIL** | no rate limit, no cap on sessions, no per-session turn limit, no budget alert in code |
| Privacy of uploaded documents | **PARTIAL** | PDFs are not stored (fields only, in memory); extracted fields (name, diagnosis, procedure, claim id) go to the model as text; no retention/deletion policy; only synthetic data may be used today |
| Reproducible build | **FAIL** | 47 uncommitted changes |

### 12.2 Smallest safe deployment path for an Azure for Students subscription (not implemented)

1. Commit the working tree first, pin dependencies (`pip freeze` of the tested versions).
2. Run **one instance only** (in-memory sessions), one uvicorn worker.
3. Host: Azure App Service (Linux, Python, B1) or Azure Container Apps with min = max = 1 replica, in **Central India** (allowed region list: Korea Central, Central India, East Asia, Malaysia West, UAE North).
4. Identity: enable a system-assigned managed identity; grant it **Foundry User** (Azure AI User) on the Foundry resource/project. Put `AZURE_SEARCH_KEY` and `AZURE_OPENAI_KEY` in the host's secret settings (or Key Vault references) rather than a file.
5. Put authentication in front (App Service "Easy Auth" with Microsoft/Entra sign-in is the smallest option), and restrict `CORS_ORIGINS` to the site itself.
6. Add a per-IP or per-session request limit and a session expiry before exposing it to more than a few known people.
7. Send stderr logs to Log Analytics (already exists); set a subscription budget alert.

Blockers or unknowns: whether the student subscription has App Service / Container Apps quota in Central India (**unknown**); the Free-tier Search service has no SLA and a low request quota (the app makes 11 to 25 Search requests for one claim answer); model quota for `gpt-5-mini` (a 429 was seen with 3 parallel evaluation workers); the delegated `az login` credential works locally but a hosted identity needs the role assignment above (needs someone with Owner rights on the resource group).

---

## 13. Risks and bugs (ranked)

**P0 (fix before any non-local deployment)**
- **P0-1 No authentication, no rate limit, no session cap on an API that spends Azure money.** `app/main.py` has no auth dependency; `SESSIONS` (line ~93) grows without bound; `POST /sessions` is open; each `/chat` costs 4 model-service requests and up to 25 Search requests. Evidence: three sessions created with no credential (section 9).
- **P0-2 The tested code is not in git.** HEAD `090fff2` lacks `app/intake.py`, `app/rendering/compact.py`, `app/static/*`, 9 test files and the customer UI. Anything deployed from git would not be what was tested.
- **P0-3 No production packaging and a secrets trap.** No Dockerfile, no `.dockerignore`, `.env` in the project root; a container built from the folder would ship both keys.

**P1 (does not match the stated intent, or a real quality problem)**
- **P1-1 Customer sees officer/internal language** in "Show more" and popups: `Excl01/02/03`, `Annexure B/C`, `C.x.y`, `Def. 5`, "Estimated insurer payment" (evidence 10.3, `render.py`, `compact.py`). CLAUDE.md implies otherwise. **Fixed 20 Sep 2026** for customer sessions (`app/rendering/customer_labels.py`); the officer wording is unchanged.
- **P1-2 `/chat` returns `tool_trace` with tool arguments and `chunk_key` to every caller**; `?dev=1` is not checked server-side (`main.py:250-252`). **Fixed 20 Sep 2026**: returned only with `DEBUG_TRACE=1` on the server (default off).
- **P1-3 Model-written text is officer-voiced for customers** (`instructions.py` line 1; audience not passed to the model). Seen: "Verify the insured's first policy inception date…", lower-case "check the Policy Schedule…".
- **P1-4 Question "Which items are not payable?" is answered with the room-rent explanation first** (Q4). The customer asked one thing and gets a long six-line calculation before the answer.
- **P1-5 A chat message after a `needs_attention` result gets an officer-voiced "Insufficient information" answer** (API path; the UI hides the chat, but nothing on the server prevents it).
- **P1-6 Sessions are in memory:** restart or second worker = all customers lose their visit. Worker/instance counts are not enforced anywhere.
- **P1-7 Prompt-injection surface:** text extracted from the uploaded PDFs (`insured_name`, `procedure`, `diagnosis`) is inserted into the model input by `_claim_block` without any filtering; a PDF can carry instructions. Mitigations exist (validators, tools do the arithmetic), so impact is limited to wording; not tested with a hostile PDF (**unknown**).
- **P1-8 Dependencies are unpinned** (`>=`): a fresh install can pull different SDK versions than the ones tested; `azure-ai-projects` 2.x had API changes before.
- **P1-9 The agent is created lazily at the first chat** (`get_agent()`), so a wrong endpoint or missing role appears as the first customer's error, not at start-up; `/health` says "ok" regardless.

**P2**
- P2-1 25 Search requests to render one assessment: the same clauses are fetched by the tool and again by the renderer, with no cache (measured 25/12/11). Slow on the Free tier and wasteful.
- P2-2 pypdf runs without a time limit; a crafted 5 MB PDF could keep a worker busy (not tested).
- P2-3 `/docs` and `/openapi.json` are public; `/samples` publishes the twelve sample claims; `GET /health` exposes mode and UIN.
- P2-4 Security headers are only set on `/` and `/officer`; API and static responses have none.
- P2-5 Token usage and cost are not logged; cost per turn is unknown.
- P2-6 Turn failure answers say "tell your administrator" and "The Azure AI service" (aimed at officers) and the customer-facing error says "This ARC service is out of date … restart it with scripts/run_demo.sh".
- P2-7 The intake checklist has 10 items but the answers count "8 of 9"; the customer sees two different totals.
- P2-8 `.env.example` and the `config.py` default name `text-embedding-3-small`, but the index was built with `-large`; a copy of the example would fail.
- P2-9 Stale documentation (section 11).
- P2-10 `HEAD` requests return 405 on `/` and `/health` (some health probes use HEAD).
- P2-11 A 0-byte PDF is reported as "This isn't a PDF"; harmless but slightly misleading.

---

## 14. Appendix

**Git:** branch `main`, HEAD `090fff23fa2e529e1bcaaf1530b3b3ce95a29ab6` (`090fff2 keep result ids, chunk keys and tool names out of officer-facing text`), 47 uncommitted entries, remote `origin` = GitHub `jashan037/claims-adjudication-agent`.

**Runtime:** Python 3.14.7 (venv `.venv`), macOS arm64, Node v24.14.0, Chrome (used by Playwright).

**Installed versions (tested):** fastapi 0.141.1, starlette 1.6.0, uvicorn 0.53.0, pydantic 2.13.5, openai 3.16.2, azure-ai-projects 2.7.0, azure-core 1.41.0, azure-identity 1.25.3, azure-search-documents 12.0.0, azure-storage-blob 12.30.2 (installed, unused), pypdf 6.19.0, python-multipart 0.0.32, httpx 0.28.1, python-dotenv 1.2.3, pytest 9.1.1, playwright 1.63.0.

**Azure resources (no keys):** subscription "Azure for Students"; resource group `rg-claims-agent`; Search `claims-search-37` (Free, Central India), index `claims-kb-v2` (186 chunks; the old `rag-1789575754829` is untouched); Foundry resource `claims-agent-project-res` (Korea Central), project `jashanpreetsingh3999-6322`, endpoint `https://claims-agent-project-res.services.ai.azure.com/api/projects/jashanpreetsingh3999-6322`; deployments `gpt-5-mini`, `text-embedding-3-large`; agent `claims-adjudication-agent-v2` (v6 live; portal agent `claims-adjudication-agent` v1 untouched); storage `claimsagentjp2026` (unused at runtime); Application Insights and Log Analytics (unused by the app).

**How to run:**
```bash
source .venv/bin/activate && az login              # same account as the subscription
python -m pytest tests -q                          # 369 tests, offline
scripts/run_demo.sh                                # http://127.0.0.1:8765/  (customer), /officer (console); refuses unless azure + foundry
PORT=8790 scripts/run_demo.sh                      # another port (used for this audit)
python scripts/check_env.py                        # validates .env
```

**Commands run for this audit (all read-only for the repo and Azure):**
`git log`, `git rev-parse`, `git status --short`, `git remote -v`, `git ls-files`, `git log --all -- .env`; `wc -l`/`ls`/`find` inventory; `cat`/`sed`/`grep` of code and docs; `.venv/bin/python -m pytest tests -q` (twice: run and collect-only); `pip list`; `lsof -i` (listeners); `curl` against `http://127.0.0.1:8790` (`/health`, `/`, `/officer`, `/static/customer.js`, `/docs`, `/openapi.json`, `/samples`, `OPTIONS /sessions`, `POST /sessions`); three scratch Python scripts (in the session scratchpad, not in the repo) that call the live API: intake ×3, six customer questions, failure cases; one in-process script that ran the intake-built claim through `claims_engine.assess`; one in-process script that counted retriever calls using the offline agent over a counting wrapper of the local retriever; one read-only Foundry call (`agents.list_versions`) to compare the deployed agent with the local prompt and schemas; a masked read of `.env` variable names and lengths. Real Azure requests made: 6 chat turns (about 24 model-service requests, 1 embedding, and Search requests) plus the read-only agent listing.

**Not run / not verified (unknown):**
- `scripts/eval_agent.py` and `scripts/demo_check.py` were **not** re-run (only the earlier recorded results exist).
- `scripts/create_agent.py` was deliberately **not** run.
- Behaviour with more than one worker or instance, under simultaneous users, or with a long-lived server (memory growth): not run.
- Token usage and cost per turn: not measured.
- Managed identity, RBAC to Search, Key Vault, App Service/Container Apps quota in the student subscription: not tried.
- A hostile or very large PDF, a password-protected PDF and a >20-page PDF on the live server: not tried (the last two are unit-tested).
- Real (non-synthetic) documents, scans, photos, other layouts: not tried by design.
- Policy answers beyond the six questions in 10.3 and the earlier evaluation: not re-run.
- Whether SDK exception text can contain secrets (`scripts/check_env.py:59`): unknown.
- Real-browser phone-size behaviour was checked earlier by screenshots (`docs/screenshots/`), not re-run in this audit.
