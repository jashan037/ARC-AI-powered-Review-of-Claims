# HANDOFF (written 21 Sep 2026, for a fresh session)

Synthetic data only. Never print or commit keys. Read `CLAUDE.md` first; this file is the honest state of play.

## Where things are
- **Git:** branch `main`; the commit hash is on the last line of `git log -1` (the tag `pre-final` marks this handoff). Older tags: `pre-cleanup`, `pre-purechat`, `pre-format-pass`, `pre-accuracy-pass`.
- **Agent:** `claims-adjudication-agent-v2`, **version 28** (latest). `AGENT_VERSION` is NOT set in `.env`, so the app uses the latest. Roll back by adding `AGENT_VERSION=23` (the version before the accuracy pass; 19 is before the format pass; 15 is before pure-chat) to `.env` and restarting. Nothing is deleted on Azure; `python scripts/setup/create_agent.py` adds a version from `app/agent/instructions.py` + `app/tools/registry.py:SCHEMAS` (run it only when the prompt or a tool schema changed).
- **Run:** `scripts/run_demo.sh` -> http://127.0.0.1:8765/ (refuses unless `.env` is azure + foundry). Restart it after any code change (a stale server once caused blank replies). Tests: `python -m pytest tests -q` -> **372 pass** (offline; Node needed for the markdown tests, Playwright + Chrome for `tests/test_ui.py`, which skip themselves without them; one UI test failed once in a full run and passed on rerun, probably timing).
- **`.env`:** exists, mode 600, git-ignored (`.env.*` too). All 13 variables are set (search endpoint/key/index, OpenAI endpoint/key, deployments, Foundry project endpoint, agent name, dims). `AUTH_MODE` is unset (= `key`). Values were never printed; `scripts/security/scan_secrets.py` found no secret in the tree or the 55 commits at the last run.

## What works (evidence I actually ran)
- Real Azure, 204 replies (68 questions x 3 runs, agent v28, 1 worker): **202 fully correct**, all six hard gates 0 violations (unsupported numbers, verdict contradictions, internal terms, decision words, unsupported policy statements, multi-part answers). Latency p50 6.8 s, p95 16.6 s, max 24.5 s. `docs/evidence/accuracy_report.md`, `accuracy_transcripts.md` (what the customer sees); the earlier run (195/204) is `accuracy_report_previous_run.md`.
- Format suite, 24 questions v19 vs v23: 22/24 -> 24/24 (`docs/evidence/format_before_after.md`); 12-question chat check (`purechat_transcripts.md`); facts fix (`facts_fix_transcripts.md`).
- Browser UI (quiet upload + chat) driven with Playwright in Chrome at 1280x800 and 390x844: `tests/test_ui.py`, screenshots in `docs/screenshots/ui_final/` and `docs/screenshots/format/`.
- Secret scan, log redaction, session expiry, rate limit, developer routes off: tests in `test_secrets.py`, `test_surface.py`.
- Sample claim numbers (hand-derived, in tests): estimate ₹1,22,125, counted so far ₹1,01,625, held ₹20,500; what-if room ₹5,000/day -> bill ₹1,72,500, estimate ₹1,60,000, counted ₹1,39,500; cover ₹5,50,000, ₹4,27,875 left after this claim, ₹1,27,875 with a stated ₹3,00,000.

## Fixed since the last full (38-case) evaluation
Pure-chat design (no templates, no `final_answer`); guards in code (numbers, internal terms, decision/hedging, voice, format, verdict consistency, policy-statement support, names); complete claim facts from all 10 documents (the policy expiry bug: the period was extracted but never sent); derived totals; what-if now changes the room charge and the bill; `cover_left` tool; policy-in-force, filing-time and patient checks; renewal facts; dates the customer mentions checked in code; date-aware verdict guard (a first-inception date was once rewritten as "not in force"); both payment figures when something is held; new UI; markdown renderer with safe links; security hardening (`docs/SECURITY.md`); `AUTH_MODE=entra` code.

## Known problems still open
- **Officer wording in customer text:** mostly removed (internal-term guard, plain-language swaps), but the guard is regex-based; rare phrases can slip. Old documents (`docs/SYSTEM_REPORT.md`, `demo/DEMO.md`, `demo/screenshots/`) still describe the old officer console and answer types.
- **Advice wording:** the model sometimes suggests next steps ("upload the prescription", "a claims officer will review") and occasionally stiff phrases; the decision guard blocks approve/reject, not every piece of advice. Two of 204 replies in the last run were wrong (an omitted ₹12,500; an oddly framed cover-left answer).
- **What-if / cover-left:** worked out in code and verified on the sample claim only. The model must call the tool (or the code precomputes it for "cover left" questions and dates); an uncalled what-if is caught by the number guard but the answer can then be thin. Plus Benefit amount is not read, so cover-left says it is excluded when opted. Other claims a customer states are assumed paid in full.
- **Filing time and expiry:** filing uses today's real date (`ARC_TODAY` for tests), so the sample claim (discharged 14 Sep 2025) shows a 372-day late-filing **review flag** and the outlook "needs a claims officer's review" instead of "likely eligible". Not a rejection, by design. Policy expiry/in-force is checked against the schedule's period only; renewals are not modelled (only a fact block).
- **Speed and 429s:** two model calls per turn minimum (conversation create + response), 3-4 with tools or a rewrite; p50 ~7 s. The Foundry model can time out or return 429/unavailable (seen in one run: ~15 replies affected); the app retries 429/5xx, then shows a plain "try again" message. Free-tier Search and shared quota make this worse under load.
- **Other:** sessions in memory (lost on restart, single worker); rate limit per process; no authentication (do not expose it); intake reads only text PDFs in the sample layout; IRDAI's longer non-payable list, sub-limits, restore/bonus rules, network lookup are not modelled; only wording HDFHLIP25041V062425 is indexed; old `scripts/eval/eval_agent.py`, `quality_suite.py`, `demo_check.py` target a removed format and do not run.

## Decisions taken
- ARC never approves or rejects; wording is "likely / appears / would likely not be payable / flagged for review"; a claims officer decides. The chat is **customer-facing**; the report for the claims team is a separate deliverable (not built here).
- The model never computes money or dates; every figure must come from code or a tool result (guards enforce it and are not to be loosened). A late filing is a review flag, never an automatic rejection (E.1.7 Note iv).
- Keys stay in `.env`; Entra (`AUTH_MODE=entra`) is coded but no role was assigned: roles Search Index Data Reader and Cognitive Services OpenAI User need the owner's go-ahead (see `docs/SECURITY.md`).

## NOT verified
`AUTH_MODE=entra` against Azure; multi-worker or deployed behaviour; real phones and non-Chrome browsers; any claim other than the sample; real documents (scans, other layouts); load; semantic correctness beyond the regex/gate checks in the suite; the Foundry prompt is ~9,100 characters and its effect at other reasoning levels.
