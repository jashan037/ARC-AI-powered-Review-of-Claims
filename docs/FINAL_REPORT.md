# ARC final report

Final pass before submission, 21 Sep 2026, from commit `c6542da` (tag `pre-final`) to tag `final-submission`.

- Baseline before any change: **372 tests pass**, working tree clean.
- After the pass: **551 tests pass** (`python -m pytest tests -q`, all offline), working tree clean.
- On the real agent (version 29): **252 of 252 replies fully correct**, every hard gate at zero, p50 7.0 s, p95 15.7 s.

Section 1 is the Stage 0 status audit: for every item of the specification, whether it already existed, was built in
this pass, or was not done. Sections 2 to 9 record what was verified, what the numbers were, what I decided where the
specification left room, and what I did **not** verify.

Synthetic data only. No key is printed anywhere in this repository. No Azure resource was created, deleted or
re-tiered; the only Azure change is a new **version of the existing agent** (section 6).

---

## 1. Stage 0: status audit

Status values: **already** = existed at `pre-final` and was checked, not rebuilt; **now** = built or fixed in this
pass; **no** = not done (with the reason). Evidence is a file, test or transcript.

### Stage 1: correctness, wording, speed

| # | Item | Status | Evidence |
|---|---|---|---|
| 1.1a | `claim_facts` carries every schedule field and every extracted claim field | already | `app/tools/facts.py`, `tests/test_facts.py` |
| 1.1b | Documents received / missing and a `not_found` list in the facts | already | `facts.build` sections "Documents", `not_found` |
| 1.1c | The model input carries the facts every turn | already | `app/agent/runner.py:_claim_block` |
| 1.1d | Policy period end labelled "Policy expiry" | already | `facts.EXPIRY` |
| 1.1e | Policy in force on the admission date, in code | already | `claims_engine.policy_in_force`, `tests/test_engine_checks.py` |
| 1.1f | Patient is the insured person, in code | already | `claims_engine.check_patient_is_insured` |
| 1.1g | Filing time (30 days, E.1.6; late = review flag only, E.1.7 Note iv) | already | `claims_engine.filing_status` / `filing_text` |
| 1.1h | `ARC_TODAY` override for tests only | already | `claims_engine.today`, `tests/conftest.py` |
| 1.2a | What-if `room_rate_per_day` replaces the room lines with rate x days and recomputes the bill | already | `claims_engine.apply_what_if`, `tests/test_engine.py` |
| 1.2b | Separate what-if "if my plan allowed Rs X a day" (`plan_room_limit_per_day`) | already | `apply_what_if`, `_limit_per_day` |
| 1.2c | `cover_left` = base + bonus (+ Plus Benefit if opted) - this claim - stated claims, unverified | already, with one gap | `app/tools/registry.py:cover_left`. The schedule states the Plus Benefit only as "Not opted / Opted" with no amount, so when it is opted the tool says so and leaves it out instead of guessing; stated in section 7. |
| 1.2d | Derived figures computed in code and added to the number guard's allowed set in every format | already | `app/tools/totals.py`, `app/tools/number_guard.py` |
| 1.3a | Waiting periods and policy-in-force judged on the date of treatment | already | `check_waiting_period(first_inception, admission_date)`, `verify._date_truth`, `registry.date_notes` |
| 1.3b | Never tell the customer to "wait" for treatment that already happened | **now** | new `app/tools/timing.py` guard + `tests/test_timing.py` |
| 1.3c | For future treatment, give the earliest covered date | already | `check_waiting_period` -> `eligible_from` / `can_be_claimed_from` |
| 1.3d | Never state the accident exception as settled ("unless it was caused by an accident") | **now** | `app/tools/guards.py:_mentions`, `app/agent/instructions.py`, `tests/test_timing.py` |
| 1.3e | Renewal rules (only covers treatment on or after its start, credit, no retro-cover, proof) | already | `facts.RENEWAL` |
| 1.4a | Answer only what was asked; length by question kind | already | `app/tools/format_guard.py` |
| 1.4b | Every part of a multi-part question, in order | already | prompt rule + hard gate in `scripts/eval/accuracy_suite.py` |
| 1.4c | What-if replies open with the payment change ("from X to Y") | **now** | `guards._required` also requires the baseline estimate on a what-if |
| 1.4d | Both figures ("once documents arrive" / "counted now") when something is held | already | `guards._required` |
| 1.4e | Cover-left only when asked | already | `registry._COVER_LEFT_Q`, `guards._COVER_LEFT` |
| 1.4f | Never "other claims you told us" unless the customer stated one | already | `cover_left` assumptions, `guards._required` |
| 1.4g | Suppress any note or next step already shown in this chat | already | `verify.suppress_repeats` |
| 1.4h | Hedged outcome words, never "approved" or "confirmed" | already | `guards._DECISION`, `_CONFIRMED`, `verify.hedge_problems` |
| 1.4i | Ask ONE question when a fact is really missing | already | prompt rule (no code guard: a question is not a checkable fact) |
| 1.4j | Customer-stated facts and hypotheticals labelled as assumptions | already | prompt rule + `cover_left` assumptions |
| 1.5 | Question topic -> focus mapped in code, and the model corrected when it disagrees | **now** | new `app/tools/focus.py` + `tests/test_focus.py` |
| 1.6a | Number guard covers dates in any format, durations, counts, and plan / hospital / document names | already for dates, durations, counts, plans, hospitals; **now** for document names | `number_guard.scan`, `verify._pairs`, `verify.entity_problems` |
| 1.6b | Verdict-consistency guard (rewrite once, then a code-built sentence) | already | `app/tools/verify.py:verdict_problems`, `fix_verdicts` |
| 1.6c | Policy-statement support check | already | `verify.policy_problems`, `drop_policy` |
| 1.6d | Fixed friendly replies before the claim is ready, on `needs_attention`, and for hostile or off-topic | already | `app/tools/canned.py`, `main.chat` |
| 1.6e | Text from uploaded PDFs sanitised before it enters the model input | already | `app/tools/sanitize.py`, `runner._claim_block` (`quoted`) |
| 1.7a | `assess_claim` run in code before the first model call | already | `registry.precompute` |
| 1.7b | Agent prompt at most 9,000 characters | **now** | was 9,027; trimmed rules that code enforces |
| 1.7c | Chunk lookups cached | already | `app/retrieval/azure_search.py` |
| 1.7d | p50 <= 15 s, p95 <= 30 s on the real agent, 1 worker | **pass** - p50 7.0 s, p95 15.7 s | section 4 |
| 1.7e | Model calls per turn and 429s reported | already | `guards.model_calls`, `scope.retries`, accuracy report |

### Stage 2: sample document sets

| # | Item | Status | Evidence |
|---|---|---|---|
| 2.1 | `demo/make_sample_sets.py` regenerates the 10 PDFs from parameters | **now** | `demo/make_sample_sets.py` |
| 2.2 | Layout, names, amounts and bill lines unchanged (intake still parses) | **now** | `--check` compares the regenerated text layer with the original PDFs line by line; `tests/test_sample_sets.py` |
| 2.3 | Sets A `on_time`, B `late_filing`, C `expired` under `demo/samples/` | **now** | `demo/samples/{on_time,late_filing,expired}` |
| 2.4 | All three omit the prescription; every PDF keeps the "SYNTHETIC DEMO DOCUMENT" footer | **now** | `tests/test_sample_sets.py` |
| 2.5 | `?sample=late`, `?sample=expired` in the sample loader, documented | **now** | `app/main.py`, `app/static/app.js`, `docs/DEMO.md` |
| 2.6 | Hand-derived expected outcomes for A, B and C, independent of the code | **now** | section 3 of this report, `scripts/eval/accuracy_suite.py` |

### Stage 3: the claim report PDF

| # | Item | Status | Evidence |
|---|---|---|---|
| 3.x | Everything in Stage 3 (endpoint, layout, content, determinism, tests, samples) | **now** - 4 pages, ~57 KB, page 1 complete for all three sets | `app/report.py`, `tests/test_report.py`, `docs/evidence/sample_report_*.pdf` |

### Stage 4: the UI

| # | Item | Status | Evidence |
|---|---|---|---|
| 4.1 | Three real URL paths (`/`, `/upload`, `/chat`) served by one page, History API, server fallback | **now** | `app/static/*`, `app/main.py`, `tests/test_web_ui.py` |
| 4.2 | Landing page (H1, sub-line, primary button, sample link, demo note) | **now** | `app/static/index.html`, `tests/test_ui.py` |
| 4.3 | Vendored Inter (woff2 + OFL licence), preloaded, system fallback | **now** | `app/static/fonts/` |
| 4.4 | Logo mark (teal arc + amber dot) top left on all views, favicon, theme-color, titles | **now** | `app/static/index.html`, `app/static/app.js` |
| 4.5 | Upload view as specified (one line, batches of 5, plain rows, needs-attention stays active) | already in substance, rebuilt | `tests/test_ui.py` |
| 4.6 | Chat view as specified (cards, md.js tables, composer, scroll rules, drop anywhere) | already in substance, rebuilt | `tests/test_ui.py` |
| 4.7 | "Download report" and "Start over" top right on `/chat`, only when ready | **now** | `app/static/app.js`, `tests/test_ui.py` |
| 4.8 | Conversation survives a refresh (`GET /sessions/{id}/messages`) | **now** | `app/main.py`, `tests/test_web_ui.py` |
| 4.9 | Every UI string in one constants block | **now** | `app/static/app.js` (`TEXT`) |
| 4.10 | WCAG AA pass with axe-core in Playwright, keyboard-only flow, 360-1920 px | **now** - no serious or critical issue on any view | `tests/test_ui.py` (the axe test skips if `tests/helpers/axe.min.js` is absent) |

### Stage 5: security and packaging

| # | Item | Status | Evidence |
|---|---|---|---|
| 5.1 | Secret scan of tree and full history without printing secrets (hashes) | already | `scripts/security/scan_secrets.py` |
| 5.2 | Second scanner (gitleaks or detect-secrets) | **now** | `detect-secrets` in `requirements-dev.txt`; the run and its three placeholder findings are in section 5 |
| 5.3 | Pre-commit scan and a failing test for key-shaped strings | already | `scripts/security/pre-commit`, `tests/test_secrets.py` |
| 5.4 | Logs, errors and exceptions never contain keys or auth headers | already | `app/observability.py:redact`, `tests/test_secrets.py` |
| 5.5 | `AUTH_MODE=key|entra` with Entra tokens for Search and embeddings | already | `app/auth.py`, `docs/SECURITY.md` |
| 5.6 | Roles looked up and **not** assigned without permission | already | `docs/SECURITY.md`; nothing was assigned in this pass |
| 5.7 | Developer surface only with `DEBUG=1`; `/health` minimal; separate `/ready` | already except `/ready` (**now**) | `app/main.py`, `tests/test_surface.py` |
| 5.8 | Security headers on every route including static and the PDF | already, extended to the PDF | `tests/test_report.py` |
| 5.9 | Idle expiry, caps, per-IP rate limit, delete-my-data | already | `tests/test_hardening*.py` |
| 5.10 | Time limit on PDF parsing | **now** | `app/intake.py` page/time budget, `tests/test_intake.py` |
| 5.11 | Model-call token usage logged (counts only) | **now** | `app/observability.py`, `app/agent/runner.py` |
| 5.12 | Customer-appropriate error messages everywhere | already, one reworded | `app/main.py` 500 body |
| 5.13 | Pinned dependencies | already, `reportlab` added | `requirements.txt` |
| 5.14 | Dockerfile (non-root, one worker) and `.dockerignore` | **now** (Dockerfile), already (`.dockerignore`, updated). **The image was not built**: no Docker daemon on this machine (section 9) | `Dockerfile` |
| 5.15 | `docs/SECURITY.md` and `docs/DEPLOY.md` | already / **now** | `docs/DEPLOY.md` |
| 5.16 | `.env` permissions 600 | already | `ls -l .env` |

### Stage 6: verification and submission

| # | Item | Status | Evidence |
|---|---|---|---|
| 6.1 | Accuracy suite over sets A, B and C, three runs on the real agent | **now** - 252/252 correct, all gates 0 | `scripts/eval/accuracy_suite.py`, `docs/evidence/final_report.md` |
| 6.2 | Adversarial unit tests (fake model returns wrong figures, verdicts, invented policy) | **now** - 37 tests; two of them found real gaps, which became the payment-claim guard and a fix to the documents verdict | `tests/test_adversarial.py` |
| 6.3 | Security test set | already in part, extended with 29 new checks | `tests/test_security.py`, `tests/test_surface.py`, `tests/test_hardening*.py` |
| 6.4 | Two concurrent users, latency reported | **now** - p50 10.3 s, 0 failures | section 4, `docs/evidence/concurrent_run.json` |
| 6.5 | Final transcripts and pass table | **now** | `docs/evidence/final_transcripts.md`, `final_report.md` |
| 6.6 | Screenshots at 1280x800 and 390x844 | **now** | `docs/screenshots/final/` |
| 6.7 | README, DEMO, SUBMISSION, FINAL_REPORT, CLAUDE.md up to date | **now** | this file and those |

Two rows of that table changed while the work was done, and the table above already says so; for the record:

- **1.2c cover_left** includes the base sum insured and the cumulative bonus. The schedule prints the Plus Benefit only as
  "Not opted" / "Opted" with no amount, so when it is opted the tool says it could not read the amount and leaves it out
  instead of guessing. That is a limitation of the document, not of the code.
- **3.x the report's result words.** The specification asked for statuses as words (Pass / Review / Missing) so the page
  reads in greyscale. A check that is outright **not met** (the policy was not in force) is neither a "review" nor a
  "missing document", so the report uses a fourth word, **Not met**, and the legend on page 1 names all of them.

---

## 2. What the final pass built

| Stage | What it added | Where |
|---|---|---|
| 1 | Four new guards: **focus** (the topic asked about decides what the answer leads with), **timing** (nobody is told to wait for a treatment that already happened; the accident exception is a condition, never settled), **payment-claim** (an amount offered as "what we will pay" must be a payment figure the engine produced, even when the number itself is real), **officer** (customer text says "your insurer's team"). A what-if now has to open with the change ("from X to Y"). Document names are checked against the claim's own checklist. The prompt is 8,993 characters. | `app/tools/focus.py`, `timing.py`, `verify.py`, `guards.py`, `app/agent/instructions.py` |
| 2 | The ten synthetic documents became a **generator with parameters**, and three sets: `on_time`, `late_filing`, `expired`. `--check` proves the regenerated text layer is identical, line for line, to the original hand-made PDFs. The outcomes were derived by hand into `expected_outcomes.json`. | `demo/make_sample_sets.py`, `demo/samples/` |
| 3 | The **claims team's report**: `GET /sessions/{id}/report.pdf`, built in code from the claim facts and the engine result, no model, nothing from the chat, nothing stored, deterministic to the byte apart from the timestamp, at most 4 pages, ~57 KB, page 1 the whole summary. Intake now records the **source file and page** of every field so the report can say where each fact came from. | `app/report.py`, `app/intake.py`, `app/assets/fonts/` |
| 4 | The UI rebuilt as **three views at three real paths** (`/`, `/upload`, `/chat`) served by one file, with the History API, a session id in `sessionStorage`, a refresh that restores the conversation, a vendored Inter, an SVG logo drawn as vector, "Download report" and "Start over", every string in one `TEXT` block, and an axe-core pass at WCAG 2.1 AA. | `app/static/*`, `app/main.py`, `tests/test_ui.py` |
| 5 | `/ready` (two booleans, cached, leaks nothing), model-call **token counts** in the turn log, a 10-second PDF parsing budget, `HEAD /health`, a customer-appropriate 500, a Dockerfile (one worker, non-root), `docs/DEPLOY.md`, a second secret scanner, and key shapes scanned across the **whole git history**. | `app/main.py`, `app/observability.py`, `Dockerfile`, `docs/DEPLOY.md`, `scripts/security/scan_secrets.py` |
| 6 | The accuracy suite rewritten over **all three sets** with eight hard gates, a two-customer concurrency check, **37 adversarial tests** (a model that lies about figures, verdicts, policy, dates, names and documents) and **29 security tests**. | `scripts/eval/accuracy_suite.py`, `tests/test_adversarial.py`, `tests/test_security.py` |

## 3. The three document sets and their hand-derived outcomes

Judged as of `ARC_TODAY=2026-09-21`. Every figure below was derived by hand from the documents and the policy wording and
lives in `demo/samples/expected_outcomes.json`; `tests/test_sample_sets.py` and the accuracy suite check against that file,
never against the engine's own output.

| | `on_time` | `late_filing` | `expired` |
|---|---|---|---|
| Policy period | 15 Mar 2026 - 14 Mar 2027 | 15 Mar 2025 - 14 Mar 2026 | 15 Mar 2025 - 14 Mar 2026 |
| Admitted / discharged | 10 - 14 Sep 2026 | 10 - 14 Sep 2025 | 20 - 24 Apr 2026 |
| Age | 29 | 28 | 28 |
| In force on the admission date | yes | yes | **no** |
| Filing | 7 days, due 14 Oct 2026, on time | **372 days**, due 14 Oct 2025, review flag | 150 days, due 24 May 2026, late |
| Outcome | likely eligible pending documents | needs a person's review | **likely not payable** |
| Estimate / counted now / held | ₹1,22,125 / ₹1,01,625 / ₹20,500 | ₹1,22,125 / ₹1,01,625 / ₹20,500 | ₹0 / ₹0 / ₹0 |

Common to all three: bill ₹1,84,500; room-related reduction ₹49,875 (room ₹12,000 + associated ₹37,875); 12 non-medical
items ₹12,500, largest attendant food ₹2,800, surgical gloves ₹2,400, service charges ₹2,000, the other 9 ₹5,300; cover
₹5,50,000; cover left ₹4,27,875 after this claim and ₹1,27,875 with a stated other claim of ₹3,00,000; what-if room
₹5,000/day gives bill ₹1,72,500, estimate ₹1,60,000, counted ₹1,39,500, and with the Protect Benefit as well ₹1,72,500 and
₹1,52,000. The prescription is missing in all three sets on purpose (9 of 10 documents received).

One engine change came out of this: when a check is violated nothing is payable, so **nothing is "on hold" either**
(`claims_engine.assess`). Showing "₹20,500 on hold" on a claim that is not covered would suggest it is only a paperwork
problem.

## 4. Verification on the real agent (version 29)

`RETRIEVER=azure AGENT_MODE=foundry python scripts/eval/accuracy_suite.py --run --repeats 3`, then `--concurrent`, then
`--report`. Full numbers in `docs/evidence/final_report.md`; every reply, exactly as the customer saw it, in
`docs/evidence/final_transcripts.md`.

| | Result | Target |
|---|---|---|
| Replies | **252** (84 questions x 3 runs; 64 questions on `on_time`, 12 on `late_filing`, 8 on `expired`) | about 60 questions, 3 runs |
| Fully correct | **252 / 252** | - |
| unsupported numbers / verdict contradictions / internal terms / decision words / unsupported policy statements | **0 / 0 / 0 / 0 / 0** | 0 |
| "officer" in customer text / repeated notes / amounts offered as a payment | **0 / 0 / 0** | 0 |
| Multi-part questions answered in full, in order | **18 / 18** | all |
| Latency | **p50 7.0 s, p95 15.7 s, max 22.4 s** | p50 <= 15 s, p95 <= 30 s |
| Model calls per turn | median **3**, max 5 | reported |
| 429 / 5xx retries inside a turn | **0** | reported |
| Replies that needed a platform retry (timeout or unavailable) | **1 of 252**, which then succeeded | reported |
| Tokens | 1,903,864 in, 133,394 out over the 252 replies (median 9,264 in, 424 out) | counts logged |
| Two customers at the same time | 12 turns, 57 s wall clock, p50 10.3 s, p95 12.5 s, max 16.7 s, 0 failures | sanity check |

The guards intervened on 117 of the 252 replies (one rewrite) and repaired 40 in code after a second failure: format 105,
figures 22, internal terms 14, decision 12, timing 11, numbers 7, names 7, verdict 4, amounts 2, focus 1. That is the
system working as designed - the model's first draft is often close but not exact, and the code, not the model, has the
last word.

**Quota:** nothing needed raising. Median 3 model calls a turn at ~9.3k input tokens each stayed inside the `gpt-5-mini`
deployment's limits even with two customers at once, and no turn hit a 429. If a demo ever does, raise the deployment's
tokens-per-minute in the Foundry portal (Deployments -> `gpt-5-mini` -> Edit) rather than retrying harder.

**Three checker patterns were widened during the run, and no expected figure was.** The suite's `--report` re-evaluates the
expectations from the stored replies, so a wording pattern that was too narrow can be corrected without re-running the
agent. The three: "not in force" now also accepts "after your policy end date"; "is my claim late" now looks for "is late"
rather than any use of the word "late" (a correct reply says "not late"); "my documents are late" now accepts "rather than
automatically rejected" alongside "not rejected"; and "what is my address" accepts "no address appears in your documents".
Each was a phrasing the specification's own words allow. The figures, dates and verdicts were never touched.

One wording blemish is visible in the transcripts and was not worth another prompt change: on one reply the internal-term
guard rewrote a quotation of the assessment into "your claim shows: ...", which is clumsy but accurate.

## 5. Security

| Check | Result |
|---|---|
| `python scripts/security/scan_secrets.py` | no `.env` value in the working tree or in the 63 commits of history; no key-shaped string in the tree; no commit ever added one |
| `detect-secrets scan` (second opinion) | 3 findings, all placeholders: two invented strings in `bootstrap_azure.py`'s own self-test and the fake key `tests/test_secrets.py` assembles at run time to prove the detector works |
| Logs | one JSON line per turn: ids, tool names, status, latency, retries, **token counts**; no question, claim data or answer. Every line passes the redactor |
| Errors | a fixed customer sentence and the exception **class name** only; `tests/test_security.py` raises an error carrying a key and proves neither the body nor the log holds it |
| Surface | `/docs`, `/openapi.json`, `/samples`, `/assess`, `/sessions/{id}/claim` are 404 without `DEBUG=1`; `/health` is one word; `/ready` is two booleans, cached 30 s, and names no endpoint, key or exception |
| Headers | on every route including the static files, the fonts and the PDF |
| Limits | 30-minute idle expiry, 100 documents and 200 turns per session, 120 requests and 20 chat turns per IP per minute, 5 MB and 20 pages and **10 seconds** per document, 26 MB per upload, 256 KB per other request |
| Delete my data | `DELETE /sessions/{id}`, and "Start over" in the page calls it |
| `.env` | mode 600, untracked, git-ignored |

`AUTH_MODE=entra` is written and unit-tested but **unproven against Azure**: it needs two data-plane roles that only the
subscription owner can assign (*Search Index Data Reader* on `claims-search-37`, *Cognitive Services OpenAI User* on
`claims-agent-project-res`). I did not assign them and I am not going to without being asked; `docs/SECURITY.md` and
`docs/DEPLOY.md` both carry the table.

## 6. The one Azure change

`python scripts/setup/create_agent.py` added **version 29** of the existing agent `claims-adjudication-agent-v2` (the
prompt changed in Stage 1, so a new version was needed). Nothing was deleted, no resource was created, no tier changed.

- Live: version 29 (8,993-character prompt, reasoning effort low, the same seven function tools).
- **Roll back** by putting `AGENT_VERSION=28` in `.env` and restarting - that is the version this pass started from.
  `AGENT_VERSION=23` goes back to before the accuracy pass. Nothing on Azure is deleted, so any version stays available.

## 7. What the old audit's open items look like now

`docs/SYSTEM_REPORT.md` section 13 listed the problems that started this work.

| Item | Now |
|---|---|
| P0-1 no auth, no rate limit, no session cap | rate limit, caps and expiry are in and tested. **No authentication**: still true, by choice for a laptop demo; `docs/DEPLOY.md` section 5 says what must come first |
| P0-2 the tested code was not in git | closed long before this pass |
| P0-3 no packaging, secrets trap | `Dockerfile` (one worker, non-root) and a `.dockerignore` that excludes `.env`. **The image was not built** (no Docker daemon on this machine) |
| P1-1 officer / internal language to the customer | closed, and now guarded: "officer" = 0 in 252 replies |
| P1-2 tool trace to every caller | closed (`DEBUG_TRACE` only) |
| P1-3 officer-voiced model text | closed (prompt + voice guard) |
| P1-4 "which items are not payable" answered with the room rent first | closed by `app/tools/focus.py`, with a test |
| P1-5 officer-voiced answer after `needs_attention` | closed (fixed friendly reply, no model call) |
| P1-6 sessions in memory | still true; one worker is now enforced in the Dockerfile and stated everywhere |
| P1-7 prompt injection from a PDF | text is cleaned, capped and quoted; `tests/test_adversarial.py` includes an injected instruction inside a document |
| P1-8 unpinned dependencies | pinned |
| P1-9 the agent is created lazily | still lazy, but `/ready` now says whether it can be reached before a customer finds out |
| P2-1 25 Search requests per answer | chunk cache |
| P2-2 pypdf without a time limit | 10-second budget per file |
| P2-3 `/docs`, `/samples` public | gated behind `DEBUG=1` |
| P2-4 headers only on some routes | everywhere |
| P2-5 token usage not logged | logged (counts only) |
| P2-6 error text aimed at officers | reworded for a customer |
| P2-7 two different document counts | one source of truth (`intake.document_counts`), used by the first message and the report |
| P2-8 `.env.example` named the wrong embedding model | fixed in the cleanup |
| P2-9 stale documentation | this pass rewrote README, DEMO, SUBMISSION, HANDOFF and CLAUDE.md |
| P2-10 `HEAD` returned 405 | `HEAD /health` works for probes; the page paths still answer GET only |
| P2-11 a 0-byte PDF was called "not a PDF" | it now says the file is empty |

## 8. Definition of done

| Item | Result | Evidence |
|---|---|---|
| Full suite passes | **pass** - 551 tests, all offline | `python -m pytest tests -q` |
| The app starts with `scripts/run_demo.sh` | **pass** - started on a free port, `/ready` said ready, intake, a real chat turn with the right figures, and the report downloaded (4 pages, A4, right headers) | section 4 and the live check in this pass |
| All three views work by keyboard | **pass** - the whole flow (landing -> upload -> chat -> ask) driven by Tab and Enter only | `tests/test_ui.py::test_the_whole_flow_works_from_the_keyboard_only` |
| All three views work by touch | **partly verified** - 360 to 1920 px with no horizontal scroll, iOS safe areas, 16 px inputs, and the mobile preset in Chrome; **no real phone was used** | `tests/test_ui.py::test_the_views_work_from_360_to_1920` |
| The report downloads and matches the engine for A, B and C | **pass** - every rupee amount in the PDF is one the engine produced, for all three sets | `tests/test_report.py` |
| The three sets produce the expected outcomes on the real agent | **pass** - 192/192, 36/36, 24/24 replies | `docs/evidence/final_report.md` |
| Accuracy gates at zero | **pass** - all eight gates 0 of 252 | same |
| p50 and p95 reported against the targets | **pass** - p50 7.0 s (target 15), p95 15.7 s (target 30) | same |
| No secrets in git or logs; security checks pass | **pass** | section 5, `tests/test_secrets.py`, `tests/test_security.py` |
| WCAG AA with axe-core, no serious or critical issue | **pass** on all three views | `tests/test_ui.py::test_every_view_passes_axe_core_with_no_serious_issue` |
| Page weight without the font under 300 KB | **pass** - 52 KB of HTML, CSS and JS | `tests/test_ui.py` |
| `final-submission` tag created, working tree clean | **pass** | `git tag`, `git status` |

## 9. What I did not run or verify

1. **The Docker image was never built.** The Docker daemon was not running on this machine and I did not start it. The
   Dockerfile and `.dockerignore` are therefore unverified, and so is everything in `docs/DEPLOY.md` (which says so at the
   top).
2. **`AUTH_MODE=entra` against Azure.** The code path exists and is unit-tested; no role has been assigned, so it has never
   made a real keyless call. Assigning the roles is the owner's decision.
3. **No deployment of any kind.** Nothing was pushed to a registry or a container app.
4. **No real phone, no browser other than Chrome.** The responsive and touch checks are Chrome's emulation.
5. **No real documents.** Intake was never given a scan, a photo, a different insurer's layout or a real person's papers.
   It refuses what it cannot read, which is the designed behaviour, but the refusal path was only exercised with
   deliberately broken synthetic files.
6. **Load beyond two concurrent customers.** Two parallel sessions of six questions each; nothing heavier.
7. **Semantic quality beyond the checks.** The gates prove figures, verdicts, policy support, topic, dates and wording.
   They cannot prove a reply is well-judged or kind. 252 transcripts are in `docs/evidence/final_transcripts.md` so a human
   can read them.
8. **The 2026 policy wording** (`HDFHLIP26058V082526`) is not chunked or indexed, and neither is any other insurer's.
9. **One flaky browser test.** `test_dropping_on_the_chat_view_adds_documents_with_an_overlay` failed once in a full run
   and passed on its own four times in a row; the waits were loosened afterwards and it has not failed since. It is a
   timing artefact of the drag-and-drop simulation, not a bug in the page.
10. **The older evaluation scripts** (`eval_agent.py`, `quality_suite.py`, `demo_check.py`, `format_suite.py`,
    `purechat_check.py`, `facts_check.py`) target a removed answer format and were not run; they are kept only as the
    record of how those earlier passes were measured.
