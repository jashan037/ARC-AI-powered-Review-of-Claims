# HANDOFF (written 21 Sep 2026, after the final pass, for a fresh session)

Synthetic data only. Never print or commit keys. Read `CLAUDE.md` first, then `docs/FINAL_REPORT.md`; this file is the short
version of where things stand.

## Where things are

- **Git:** branch `main`. Tags: `final-submission` (this state), `pre-final` (before the final pass), and the older
  `pre-cleanup`, `pre-purechat`, `pre-format-pass`, `pre-accuracy-pass`, `pre-final-pass`.
- **Agent:** `claims-adjudication-agent-v2`, **version 29** (latest; prompt 8,993 characters, reasoning effort low).
  `AGENT_VERSION` is not set in `.env`, so the app uses the latest. Roll back by putting `AGENT_VERSION=28` (the version
  before the final pass) or `23` in `.env` and restarting. Nothing on Azure is ever deleted;
  `python scripts/setup/create_agent.py` adds a version from `app/agent/instructions.py` + the tool schemas.
- **Run:** `scripts/run_demo.sh` -> http://127.0.0.1:8765/ (refuses unless `.env` says azure + foundry). Restart it after
  any code change. Tests: `python -m pytest tests -q`.
- **`.env`:** present, mode 600, git-ignored. `AUTH_MODE` unset (= `key`). Values were never printed;
  `python scripts/security/scan_secrets.py` finds no secret in the tree or in the 60-odd commits.

## What the final pass added

The three synthetic document sets built from parameters (`demo/make_sample_sets.py`), the claims team's report PDF
(`app/report.py`, no model at all), the three-view UI at real paths (`/`, `/upload`, `/chat`) with a vendored Inter and an
axe-core pass, four new guards (focus, timing, payment-claim, "officer"), `/ready`, token counts in the turn log, a
Dockerfile and `docs/DEPLOY.md`, and the adversarial and security test sets. `docs/FINAL_REPORT.md` has the audit table,
the evidence and the honest list of what was not verified.

## The three document sets (what each one is for)

| Set | Story | Load it |
|---|---|---|
| `on_time` | in force, filed 7 days after discharge, one document missing | "Try with sample documents" |
| `late_filing` | in force, filed 372 days after discharge: a review flag, never a rejection | `/?sample=late` |
| `expired` | the policy ended before the admission: likely not covered, leads with that | `/?sample=expired` |

Their hand-derived outcomes are in `demo/samples/expected_outcomes.json`. **Never change a number there to make something
pass**; re-derive it from the wording.

## Known problems still open

- **No authentication.** Do not expose the app. `docs/DEPLOY.md` section 5 says what would have to come first.
- **Sessions in memory, one instance.** A restart ends every visit; a second worker would lose half of them.
- **Advice wording.** The guards catch decisions, figures, verdicts, the topic, dates and the voice. They cannot prove a
  sentence is well-judged; `docs/evidence/final_report.md` lists every reply that failed a check in the last run.
- **`AUTH_MODE=entra` is written but unproven**: no role has been assigned. The owner assigns
  *Search Index Data Reader* and *Cognitive Services OpenAI User* (see `docs/SECURITY.md`), then sets `AUTH_MODE=entra`.
- **Docker image not built** (the daemon was not running on this machine); the Dockerfile is therefore unverified.
- **Speed:** two model calls per turn minimum, more with a tool or a rewrite. The Foundry deployment can return 429 or time
  out; the app retries then shows a plain "try again".
- Only wording HDFHLIP25041V062425 is indexed; HDFC's 68-item Annexure B only; intake reads text PDFs in the sample layout.
- Stale eval scripts kept as records but no longer runnable against the current format: `scripts/eval/eval_agent.py`,
  `quality_suite.py`, `demo_check.py`, `format_suite.py`, `purechat_check.py`, `facts_check.py`.

## Decisions that should not be undone without a strong reason

- ARC never approves or rejects. Customer-facing text says "your insurer's team", never a role inside the insurer.
- The model never computes money or dates: tools return final figures and the guards enforce it. **Do not loosen a guard.**
- A late filing is a review flag (E.1.7 Note iv), never an automatic rejection.
- The claims team's report has no model in it, so it stays reproducible.
- Keys live only in `.env`; prefer Entra once the roles exist.
