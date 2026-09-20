# ARC: AI-powered Review of Claims

A customer uploads health-insurance claim documents, ARC reads them and builds the claim, and the customer chats about it. The policy is the public HDFC ERGO my:Optima Secure wording. **A claims officer always decides**; ARC says "likely", never "approved".

How it works: a Foundry agent (gpt-5-mini) chooses tools. Azure AI Search finds policy clauses, deterministic Python does every date and rupee amount, a validator checks that citations came from tool results in the same turn, and a renderer prints the answer, so numbers and citations never pass through the model's typing.

```
documents (PDF) -> intake (pypdf + rules, no model) -> claim
question + claim -> Foundry agent -> tools (search_policy, get_clause, check_waiting_period, assess_claim, lookup_non_medical_item, get_claim_summary)
                 -> final_answer -> validator -> renderer -> short summary + "Show more"
```

Answer types: `claim_assessment`, `coverage_answer`, `waiting_period_answer`, `deduction_explanation`, `documents_answer`, `definition_answer`, `insufficient_information`, `general_answer`.

## Layout

```
app/        runtime code: API (main.py), intake, agent/, tools/, rendering/, retrieval/, static/ (the customer page)
data/       policy chunks (186), rules, 12 sample claims, retrieval eval questions
demo/       documents/ (10 synthetic PDFs the page loads), screenshots/, examples/ (generated answers), DEMO.md
scripts/    run_demo.sh, setup/ (Azure), eval/ (evaluations), dev/ (CLI, renderers, screenshots)
tools/      policy chunking (offline data prep), source/ (the policy PDF), kb_sources/ (source list for more wordings)
tests/      all offline; golden answers in tests/golden, test-only helper in tests/helpers
docs/       SYSTEM_REPORT.md (audit), CLEANUP_PLAN.md, CLEANUP_REPORT.md, evidence/ (eval report and transcripts)
```

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt                 # requirements.txt is runtime only; the dev file adds pytest, httpx, playwright
cp .env.example .env                                # fill it in for Azure; the defaults run offline
python -m pytest tests -q                           # 439 tests, all offline; browser tests use the Chrome you already have
scripts/run_demo.sh                                 # customer page at http://127.0.0.1:8765/  (refuses unless .env is azure + foundry)
uvicorn app.main:app --reload                       # dev; API docs at /docs; add ?dev=1 to the page for the badge and trace panel
python scripts/dev/chat_cli.py --claim TC07         # terminal chat (follows RETRIEVER / AGENT_MODE)
python scripts/dev/render_samples.py                # 12 assessments -> demo/examples/
python scripts/dev/render_examples.py               # one example per answer type -> demo/examples/
python scripts/eval/eval_retrieval.py --verbose     # retrieval baseline on the 19 questions
```

## Connect it to Azure

```bash
az login                                            # the account needs the Foundry User role on the project
python scripts/setup/check_env.py                   # validates .env
python scripts/setup/create_index.py                # claims-kb-v2 (clause-level index)
python scripts/setup/upload_chunks.py               # embed + upload data/policy_clauses.jsonl
RETRIEVER=azure python scripts/eval/eval_retrieval.py --verbose
python scripts/setup/create_agent.py                # a new agent version with the function tools (SDK only)
RETRIEVER=azure AGENT_MODE=foundry python scripts/eval/eval_agent.py --workers 2   # 38 cases against the real agent
```

Live settings: `RETRIEVER=azure`, `AGENT_MODE=foundry`. Embeddings use the deployment `text-embedding-3-large` with `EMBEDDING_DIMENSIONS=1536`. Rebuild the chunks from the policy PDF: `python tools/chunk_policy.py tools/source/optima-secure-HDFHLIP25041V062425.pdf --uin HDFHLIP25041V062425 --doc-id optima-secure-v062425 --out data/policy_clauses.jsonl`.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | the customer page |
| POST | `/sessions` | new session, body optional `{"audience": "customer"}` |
| POST | `/sessions/{id}/documents` , `/documents/sample` | upload PDFs / load the sample documents |
| POST | `/sessions/{id}/intake` | build the claim: `ready` or `needs_attention` with plain reasons |
| POST | `/sessions/{id}/claim` | `{"sample_id": "TC07"}` or `{"claim": {...}}` |
| POST | `/sessions/{id}/chat` | `{"message": "..."}` -> `summary_markdown`, `sections`, `citations`, `suggestions`, `trace_summary` |
| POST | `/assess` | stateless deterministic assessment, no LLM |
| GET | `/samples`, `/health` | sample claims; settings and whether the live agent is on |

## Status and known limits

- Tested: 439 offline tests and, on the real Azure agent, 38 evaluation cases (`docs/evidence/eval_report.md`) and the 8-step demo (`scripts/eval/demo_check.py`).
- Intake reads text PDFs in the layout of `demo/documents/` only; scans and other layouts are refused, not guessed. Azure Content Understanding is not built.
- Sessions are in memory, there is no authentication or rate limiting, and dependencies are pinned but not locked: see `docs/SYSTEM_REPORT.md` (P0 and P1 lists) before any deployment.
- Only wording HDFHLIP25041V062425 is indexed. Non-medical items use HDFC's Annexure B (68 items), not IRDAI's longer list.
- All data is synthetic. Never load a real person's documents.
