# ARC final report

Final pass before submission, started 21 Sep 2026 from commit `c6542da` (tag `pre-final`).
Baseline before any change: **372 tests pass** (`python -m pytest tests -q`, all offline), working tree clean.

This report is written in stages. Section 1 is the Stage 0 status audit: for every item of the final-pass
specification, whether it already existed, was built in this pass, or was not done. Later sections record what was
verified, what failed and what was not verified.

Synthetic data only. No key is printed anywhere in this repository. No Azure resource was created, deleted or
re-tiered in this pass; the only Azure change is a new **version of the existing agent** (section 6).

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
| 1.7d | p50 <= 15 s, p95 <= 30 s on the real agent, 1 worker | verified in this pass | section 4 |
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
| 3.x | Everything in Stage 3 (endpoint, layout, content, determinism, tests, samples) | **now** | `app/report.py`, `tests/test_report.py`, `docs/evidence/sample_report_*.pdf` |

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
| 4.10 | WCAG AA pass with axe-core in Playwright, keyboard-only flow, 360-1920 px | **now** | `tests/test_ui.py` (axe test skips without the vendored axe-core) |

### Stage 5: security and packaging

| # | Item | Status | Evidence |
|---|---|---|---|
| 5.1 | Secret scan of tree and full history without printing secrets (hashes) | already | `scripts/security/scan_secrets.py` |
| 5.2 | Second scanner (gitleaks or detect-secrets) | **now** | `detect-secrets` in `requirements-dev.txt`, run recorded in section 5 |
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
| 5.14 | Dockerfile (non-root, one worker) and `.dockerignore` | **now** (Dockerfile), already (`.dockerignore`, updated) | `Dockerfile` |
| 5.15 | `docs/SECURITY.md` and `docs/DEPLOY.md` | already / **now** | `docs/DEPLOY.md` |
| 5.16 | `.env` permissions 600 | already | `ls -l .env` |

### Stage 6: verification and submission

| # | Item | Status | Evidence |
|---|---|---|---|
| 6.1 | Accuracy suite over sets A, B and C, three runs on the real agent | **now** | `scripts/eval/accuracy_suite.py`, `docs/evidence/final_report.md` |
| 6.2 | Adversarial unit tests (fake model returns wrong figures, verdicts, invented policy) | **now** | `tests/test_adversarial.py` |
| 6.3 | Security test set | already in part, extended | `tests/test_surface.py`, `tests/test_hardening*.py` |
| 6.4 | Two concurrent users, latency reported | **now** | section 4 |
| 6.5 | Final transcripts and pass table | **now** | `docs/evidence/final_transcripts.md`, `final_report.md` |
| 6.6 | Screenshots at 1280x800 and 390x844 | **now** | `docs/screenshots/final/` |
| 6.7 | README, DEMO, SUBMISSION, FINAL_REPORT, CLAUDE.md up to date | **now** | this file and those |
