# Claims copilot backend

FastAPI backend around your Foundry agent. Azure AI Search finds the policy clauses, deterministic Python tools do the dates and money, and a renderer prints every answer in a fixed format.

## How one question flows

```
question (+ claim loaded in the session)
   -> Foundry agent (gpt-5-mini) chooses tools
        search_policy / get_clause        -> Azure AI Search, filtered to the claim's policy UIN
        check_waiting_period              -> claims_engine (Python)
        assess_claim (+ what_if)          -> claims_engine (Python)
        lookup_non_medical_item           -> Annexure B table
   -> final_answer(answer_type, ...)      -> validated: citations must come from tool results this turn
   -> renderer prints Markdown            -> numbers and citations never pass through the LLM's typing
```

Answer types: `claim_assessment`, `coverage_answer`, `waiting_period_answer`, `deduction_explanation`, `documents_answer`, `definition_answer`, `insufficient_information`, `general_answer`.

## Run it offline first (no Azure)

```bash
pip install -r requirements.txt
cp .env.example .env
python -m pytest tests -q                       # 69 tests, all offline
python scripts/render_samples.py                # 12 claim assessments -> examples/
python scripts/render_examples.py               # one example per answer type -> examples/
python scripts/eval_retrieval.py --verbose      # keyword baseline on the 19 questions
python scripts/chat_cli.py --claim TC07         # try it in the terminal (offline stand-in for the agent)
uvicorn app.main:app --reload                   # http://127.0.0.1:8000/docs
```

## Connect it to Azure

```bash
# fill .env (endpoints, keys, project endpoint), then:
python scripts/create_index.py                  # 1. new clause-level index (claims-kb-v2)
python scripts/upload_chunks.py                 # 2. embed + upload data/policy_clauses.jsonl
RETRIEVER=azure python scripts/eval_retrieval.py --verbose   # 3. compare with the baseline
az login && python scripts/create_agent.py      # 4. agent with function tools (SDK only; the portal cannot add them)
RETRIEVER=azure AGENT_MODE=foundry python scripts/chat_cli.py --claim TC07   # 5. real agent in the terminal
RETRIEVER=azure AGENT_MODE=foundry uvicorn app.main:app --reload            # 6. API
```

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/sessions` | new session |
| POST | `/sessions/{id}/claim` | `{"sample_id": "TC07"}` or `{"claim": {...}}` |
| POST | `/sessions/{id}/chat` | `{"message": "..."}` -> `answer_markdown`, `answer_type`, `citations`, `tool_trace` |
| POST | `/assess` | stateless deterministic assessment, no LLM |
| GET | `/samples`, `/health` | |

## What has and has not been tested

Tested (offline, `pytest`): claims engine on 12 hand-derived cases, clause-reference resolution, tool validation, the Foundry function-calling loop against a scripted fake client, the API, the renderer.
Not tested (needs your Azure resources): `azure_search.py`, `create_index.py`, `upload_chunks.py`, `create_agent.py`, and the real `FoundryAgent` calls. They follow the current Microsoft docs (azure-ai-projects 2.x, azure-search-documents 11.x); expect to fix small API differences on first run.

## Known limits

- Policy wording indexed: HDFHLIP25041V062425 only. The 2026 wording needs the generic chunker (see the KB pack).
- Non-medical check uses HDFC's Annexure B (68 items). IRDAI's longer standard list is not loaded yet.
- Claim input is structured JSON. Extracting it from PDFs (Content Understanding) is the next stage.
- The keyword baseline in `eval_retrieval.py` uses questions written from the same document, so its scores are optimistic.

## Working with Claude Code

`CLAUDE.md` holds the full project context and is read automatically. `FIRST_PROMPT.md` has the prompt to start with and follow-up prompts for later stages.
