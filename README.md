# ARC: AI-powered Review of Claims

A customer uploads their health-insurance claim documents. ARC reads them, builds the claim, explains in plain words what is
likely to be paid and why, and prepares a report the insurer's claims team can act on.

**ARC never approves or rejects a claim.** It says "likely", "appears", "flagged for review"; the insurer's team decides.
Every number, date and rule result is computed by Python. The language model only explains, and every reply it writes is
checked against the tool results of that turn before the customer sees it.

The policy is the public **HDFC ERGO my:Optima Secure** wording (UIN HDFHLIP25041V062425). All documents and claims in this
repository are synthetic.

```
documents (PDF) -> app/intake.py (pypdf + rules, no model) -> the claim
question         -> code runs assess_claim first
                 -> Foundry agent (gpt-5-mini) with function tools: search_policy / get_clause (Azure AI Search),
                    check_waiting_period, assess_claim (+ what-if), lookup_non_medical_item, get_claim_summary, cover_left
                 -> claims_engine.py (pure Python: dates, rupees, rules)
                 -> plain Markdown reply -> guards (numbers, verdicts, policy support, focus, timing, decision words,
                    voice, format) -> the customer
claim + engine   -> app/report.py (no model at all) -> the claims team's PDF
```

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt          # requirements.txt is the pinned runtime; the dev file adds pytest, httpx, playwright, detect-secrets
python -m pytest tests -q                    # the whole suite, offline: no Azure, no model
cp .env.example .env                         # then fill it in (see "Connect it to Azure")
scripts/run_demo.sh                          # http://127.0.0.1:8765/  (refuses to start unless .env selects the real agent)
```

The demo is three views at three real paths: `/` (landing), `/upload` (drop your documents), `/chat` (ask about the claim).
On `/chat` the top right has **Download report** (the claims team's PDF) and **Start over** (deletes everything held for the session).

## The three sample document sets

`demo/samples/` holds the same ten synthetic PDFs three times over; only the dates and the age differ, so the same claim tells
three different stories. `python demo/make_sample_sets.py` regenerates all three from their parameters, and
`--check` proves the regenerated text still matches the original documents line for line.

| Set | Policy period | Admitted | What it shows | Load it with |
|---|---|---|---|---|
| `on_time` | 15/03/2026 - 14/03/2027 | 10/09/2026 | in force, filed 7 days after discharge, one document missing | "Try with sample documents" |
| `late_filing` | 15/03/2025 - 14/03/2026 | 10/09/2025 | in force, filed 372 days after discharge: a review flag, never a rejection | `/?sample=late` |
| `expired` | 15/03/2025 - 14/03/2026 | 20/04/2026 | the policy had ended before the admission: likely not covered | `/?sample=expired` |

Every set leaves the doctor's prescription out on purpose, so the demo shows a real gap. The outcome each set must produce was
derived by hand from the documents and the wording and lives in `demo/samples/expected_outcomes.json`; the tests and the
accuracy suite check against that file, never against the engine's own output.

## Layout

```
app/        the runtime: API (main.py), intake.py, report.py, agent/, tools/ (engine + guards), retrieval/, static/ (the page), assets/fonts/
data/       186 policy clause chunks, the rule tables, 12 hand-derived sample claims, retrieval eval questions
demo/       make_sample_sets.py and samples/ (three sets of ten PDFs + the hand-derived expected outcomes)
scripts/    run_demo.sh, setup/ (Azure), eval/ (the accuracy suite and others), dev/ (chat CLI, screenshots), security/ (secret scan, hooks)
tests/      all offline (Node for the markdown tests, Playwright + Chrome for the browser tests; both skip themselves)
docs/       SUBMISSION.md, DEMO.md, FINAL_REPORT.md, SECURITY.md, DEPLOY.md, SYSTEM_REPORT.md (the audit), evidence/, screenshots/
tools/      policy chunking (offline data prep) and the policy PDF
```

## Connect it to Azure

```bash
az login                                            # the account needs the Foundry User role on the project
python scripts/setup/check_env.py                   # validates .env
python scripts/setup/create_index.py                # builds claims-kb-v2 (clause-level index)
python scripts/setup/upload_chunks.py               # embeds and uploads data/policy_clauses.jsonl
RETRIEVER=azure python scripts/eval/eval_retrieval.py --verbose
python scripts/setup/create_agent.py                # adds an agent version from app/agent/instructions.py + the tool schemas (SDK only)
RETRIEVER=azure AGENT_MODE=foundry python scripts/eval/accuracy_suite.py --run --repeats 3
python scripts/eval/accuracy_suite.py --report      # -> docs/evidence/final_report.md, final_transcripts.md
```

Live settings are `RETRIEVER=azure` and `AGENT_MODE=foundry`. Embeddings use `text-embedding-3-large` with
`EMBEDDING_DIMENSIONS=1536`. `AGENT_VERSION=` pins an older agent version without deleting anything.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/`, `/upload`, `/chat` | the customer page (one file, three paths) |
| POST | `/sessions` | a new session |
| POST | `/sessions/{id}/documents` , `/documents/sample?set=` | upload PDFs / load one of the three sample sets |
| POST | `/sessions/{id}/intake` | build the claim: `ready`, or `needs_attention` with plain reasons |
| POST | `/sessions/{id}/chat` | `{"message": "..."}` -> `{status, reply, sources}` |
| GET | `/sessions/{id}/messages` | the conversation so far, so a refresh keeps it |
| GET | `/sessions/{id}/report.pdf` | the claims team's report, built in code (no model, nothing from the chat, nothing stored) |
| DELETE | `/sessions/{id}` | delete everything held for the session |
| GET | `/health` , `/ready` | `{"status":"ok"}` / whether Search and the agent can be reached |

Developer routes exist only with `DEBUG=1`: `/docs`, `/openapi.json`, `/samples`, `POST /assess`, `POST /sessions/{id}/claim`.
Sessions expire after 30 idle minutes; the caps, the per-IP rate limit and the rest are in `docs/SECURITY.md`.

## Where to look next

- `docs/SUBMISSION.md` - the problem, the design, the evidence index and the honest limitations.
- `docs/DEMO.md` - a three-minute demo script with the exact questions and the expected answers for each set.
- `docs/FINAL_REPORT.md` - what was built in the final pass, what was verified, and what was not.
- `docs/evidence/` - the accuracy report and the transcripts of exactly what the customer sees, plus the three sample reports.
- `docs/SECURITY.md`, `docs/DEPLOY.md` - what the code enforces, and a single-instance deployment path (not executed).

## Known limits

- One policy version is indexed (HDFHLIP25041V062425). Non-medical items use HDFC's 68-item Annexure B, not IRDAI's longer list.
- Intake reads text PDFs in the layout of `demo/samples/`. A scan, a photo or an unfamiliar layout is reported back to the customer, never guessed at.
- Sessions live in memory in one process: a restart ends every visit, and a second worker would lose half of them.
- There is no authentication. Do not put this on a public URL as it is (`docs/DEPLOY.md` section 5).
- An estimate is not a decision. ARC is a reading and explaining tool; the insurer's team decides every claim.
