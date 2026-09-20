# Cleanup plan (Stage 1: plan only, nothing has been changed yet)

Written 20 Sep 2026. Rule for the whole job: **no new features, no behaviour changes.** The customer flow (upload, intake, chat, "Use sample documents", "Add a document") must work exactly as today.

## 0. Checkpoint and baseline (done)

| Item | Value |
|---|---|
| Test suite before | **445 passed**, 0 failed, 0 skipped (27 s) |
| Checkpoint | commit `2c275af` "checkpoint before cleanup", tag **`pre-cleanup`** (everything below can be undone with `git reset --hard pre-cleanup`) |
| Secrets check before committing | no `.env`/key/zip/bak file staged; the two key values from `.env` were searched for (without printing them) in all 97 changed files: 0 hits |
| BEFORE live baseline | saved outside the repo (scratchpad `BEFORE.json`): sample documents loaded, 6 questions on the real agent (live Azure Search + Foundry agent v7), plus "Add a document". All six answered `ok`; numbers `₹1,22,125 / ₹1,01,625 / ₹12,000 / ₹12,500 / ₹1,84,500`; no internal terms in any answer |
| Size before | 168 tracked files, 3.7 MB, 20,831 text lines (app py 3,243 · app js/css/html 1,266/403/176 · tests py 3,234 · scripts py 1,460 + sh 42 + mjs 159 · tools py 338 + kb pack 71 · markdown 4,193 · data 3,360) |

## 1. Evidence gathered

| Tool | Result |
|---|---|
| **coverage** (`app/`, whole test suite + one live run on real Azure incl. `/`, upload, intake, 6 chats, add a document) | **96 %** of `app/` (2,114 statements, 81 not hit). Nothing in `app/` is unreachable by module: every module is at least 87 % covered. Missed lines are error branches (e.g. `runner.py` 97-102 timeout notice, `resilience.py` 96-98) |
| **ruff F401/F841/F811** (app, scripts, tools, tests, kb pack) | 12 findings: 7 unused imports, 5 unused local variables (listed in section 3) |
| **vulture** (min confidence 60) | 14 findings. 9 are FastAPI handlers registered by decorator (`http_error`, `validation_error`, `unexpected_error`, `create_session`, `use_sample_documents`, `run_intake`, `load_claim`, `assess_stateless`) plus `officer_console` (a route, dead only once the console is removed) and `JsonFormatter.format` (called by `logging`). **Real dead code: `Chunk.as_dict`, `DOC_NAMES`, `RE_NUM`, `skip_pages_as_tables`** |
| **reference count** of every top-level function/class/constant in `app/ scripts/ tools/` across py/js/html/sh/md/json | only the names above have zero references. No name is used only by tests. Names used once in their own file plus tests (`OfflineAgent`, `_parse_what_if`, `harden`, `JsonFormatter`, `what_if_words`, `claim_reasons`, `short_label`, `backoff_delay`, `AzureSearchRetriever`, `validate_final`) are live |
| **dynamic dispatch checked by hand** | tool names in `registry.SCHEMAS`/`_dispatch` (7 tools) are all called or deployed (agent v7 has all 7); FastAPI routes are used by `customer.js`, tests or `README`; pytest fixtures live in `tests/conftest.py` and inside test files (no unused conftest fixture); scripts referenced by tests: `render_samples.py`, `render_examples.py`, `eval_agent.py`, `run_demo.sh`; scripts referenced by other scripts: `bootstrap_azure.py` runs `check_env`, `create_index`, `upload_chunks`, `eval_retrieval`, `create_agent` by path |
| **JS (manual)** | `customer.js`, `md.js`, `dev.js` are loaded by `index.html`/`customer.js`. `officer.html` loads `md.js`, `labels.js`, `arc.js`, `arc.css`. `labels.js` and `arc.js` and `arc.css` are referenced only by `officer.html` and by tests. `node --check` passes for all six files today |
| **third-party imports** | runtime: `azure-ai-projects`, `azure-identity`, `azure-search-documents`, `openai`, `python-dotenv`, `fastapi`, `pydantic`, `pypdf`, `starlette` (via fastapi), `python-multipart` (FastAPI file upload), `uvicorn`. dev only: `pytest`, `playwright`, `httpx` (Starlette's TestClient). `requests` is used only by `reference/kb_sources_pack/download_kb.py` and is not in requirements. `azure-storage-blob` is not in `requirements.txt` and is installed only because **`azure-ai-projects` requires it**, so it cannot be removed |

## 2. Target layout

```
app/              runtime only: main, config, intake, resilience, observability, agent/, tools/, rendering/, retrieval/, static/ (customer UI)
data/             policy_clauses.jsonl, rules/*, sample_claims.json, rag_eval_questions.json (unchanged)
scripts/
  run_demo.sh     stays (tests and docs refer to it)
  setup/          create_index.py upload_chunks.py create_agent.py bootstrap_azure.py check_env.py
  eval/           eval_retrieval.py eval_agent.py demo_check.py
  dev/            chat_cli.py render_samples.py render_examples.py take_screenshots.py
tools/            chunk_policy.py chunk_generic.py source/ (the policy PDF) kb_sources/ (the 28-document source list and downloaders)
tests/            test_*.py, conftest.py, golden/answers/, helpers/pdfmaker.py (test-only helper, now clearly separated)
demo/             documents/ (the 10 sample PDFs + manifest + expected_extraction.json), screenshots/, examples/ (generated answers), DEMO.md
docs/             SYSTEM_REPORT.md, CLEANUP_PLAN.md, CLEANUP_REPORT.md, evidence/ (eval_report.md, eval_failures/)
requirements.txt (runtime, pinned) · requirements-dev.txt · .gitignore · .dockerignore · README.md · CLAUDE.md · .env.example
```

## 3. Verdicts

### 3.1 DELETE (proof that nothing references it)

| What | Proof |
|---|---|
| Officer console: `app/static/officer.html`, `arc.js` (440 lines), `arc.css` (204), `labels.js` (34); the `/officer` route (`main.py:officer_console`, 4 lines) | referenced only by `main.py`, `test_web_ui.py`, `scripts/take_screenshots.mjs`, docs. The customer page (`index.html`) loads none of them. Coverage of the live customer run never touched them. **The officer wording mode stays** (19 golden files) |
| `scripts/take_screenshots.mjs` | screenshots of the officer console only; nothing references it except docs |
| Tests that only cover the officer console (in `tests/test_web_ui.py`): `test_the_officer_console_has_the_live_badge_and_the_offline_warning_banner`, `test_the_ui_reads_only_the_sanitized_trace_field`, `test_quick_questions_match_the_demo_script`, `test_the_ui_renders_the_summary_first_with_collapsed_sections_chips_and_a_collapsed_trace`, `test_claim_dropdown_labels_are_short`, `test_quick_questions_follow_the_loaded_claim`; and the officer/`arc.*`/`labels.js` parts of `test_root_serves_the_customer_page_and_officer_serves_the_console`, `test_the_page_is_csp_clean_and_uses_no_outside_resources`, `test_the_static_assets_are_served_with_the_right_types`, `test_the_brand_colours_are_used`, `test_the_javascript_files_have_no_syntax_errors` (these five are **trimmed**, not deleted, so the customer page keeps its checks) | each asserts on `officer.html`, `arc.js`, `arc.css` or `labels.js` only. The other 13 tests in the file (API, trace_summary, markdown renderer, health, examples render) stay. Expected test count drops by about 6 to 8; every removed assertion is about a file that no longer exists |
| `reference/claims_data_pack/demo_claim/*.pdf` (5 PDFs) and `claim_manifest.json` | the 5 PDFs are **byte-identical** to `reference/demo_documents/{claim_form,discharge_summary,hospital_bill,lab_report,policy_schedule}.pdf` (verified with `cmp`). Nothing loads them: `intake.SAMPLE_DIR` reads `demo_documents` |
| `reference/claims_data_pack/test_cases/rag_eval_questions.json` | byte-identical to `data/rag_eval_questions.json` (verified with `cmp`) |
| `FIRST_PROMPT.md` (5.6 KB) | the original starting prompts, superseded by `CLAUDE.md`; only `README.md:71` mentions it. Recoverable from the `pre-cleanup` tag |
| Dead code: `Chunk.as_dict` (`retrieval/base.py:70`), `DOC_NAMES` (`tools/claims_engine.py:28`), `RE_NUM` and `skip_pages_as_tables` (`tools/chunk_policy.py:82,87`) | zero references anywhere (grep over the whole tracked tree, incl. tests, docs and JS) |
| Unused imports (ruff F401): `runner.py` `render as R` and `claims_engine as E`; `intake.py` `Path`; `compact.py` `pct`; `retrieval/base.py` `field`; `kb_sources_pack/download_kb.py` `sys`; `tests/test_intake_api.py` `pytest` | ruff F401; grep shows no test patches `runner.R`/`runner.E` and no module re-exports these names |
| Unused locals (ruff F841): `render.py:149` `a`, `:151` `lines`, `:238` `c`, `:335` `c`; `bootstrap_azure.py:243` `steps` | assigned, never read. Removing them cannot change output; the 19 golden files lock every rendered answer |

### 3.2 MOVE (old → new)

| Old | New |
|---|---|
| `reference/demo_documents/*` (10 PDFs, `manifest.json`, `README.txt`) | `demo/documents/` (**runtime path: `intake.SAMPLE_DIR`, `scripts/*`, tests updated**) |
| `reference/claims_data_pack/demo_claim/expected_extraction.json` | `demo/documents/expected_extraction.json` (ground truth for those PDFs; `tests/test_intake.py` reads it) |
| `reference/claims_data_pack/test_cases/test_cases.json`, `reference/claims_data_pack/README.md` | `demo/original_pack/` — **unsure, see section 5**; not referenced by code |
| `reference/policy/optima-secure-HDFHLIP25041V062425.pdf` | `tools/source/` (input of the chunker; command in CLAUDE.md/README updated) |
| `reference/kb_sources_pack/*` (KB_SOURCES.md, download_kb.py, ingest_kb.py, kb_manifest.json) | `tools/kb_sources/` (`ingest_kb.py` calls `tools/chunk_*.py` relative to the repo root: path constant adjusted) |
| `docs/screenshots/*.png` (8) | `demo/screenshots/` |
| `docs/DEMO.md` | `demo/DEMO.md` |
| `examples/*.md` (19 generated answers) | `demo/examples/` (`render_samples.py`/`render_examples.py` default output folders and `tests/test_web_ui.py`, `tests/test_compact.py` updated) |
| `examples/eval_report.md`, `examples/eval_failures/*` (9) | `docs/evidence/` (`eval_agent.py` defaults updated; `tests/test_eval_transcript.py` unaffected) |
| `scripts/{create_index,upload_chunks,create_agent,bootstrap_azure,check_env}.py` | `scripts/setup/` |
| `scripts/{eval_retrieval,eval_agent,demo_check}.py` | `scripts/eval/` |
| `scripts/{chat_cli,render_samples,render_examples,take_screenshots}.py` | `scripts/dev/` |
| `tests/pdfmaker.py` | `tests/helpers/pdfmaker.py` (+ `__init__.py`); imports in 4 test files and `take_screenshots.py` updated |
| `scripts/run_demo.sh` | **stays** |

Every moved script computes the repo root from its own location (`Path(__file__).resolve().parent.parent`) and `bootstrap_azure.py` starts sibling scripts by path (`ROOT / "scripts" / name`); both are adjusted with the move (one directory deeper) and covered by running each script's `--help`/offline mode plus the existing tests (`test_compact.py` runs the two render scripts, `test_eval_transcript.py` loads `eval_agent.py`, `test_run_demo.py` runs `run_demo.sh`).

### 3.3 KEEP (used at runtime or protected)

- **All 15 modules of `app/`** (coverage 87 to 100 %), the customer UI (`index.html`, `customer.css`, `customer.js`, `md.js`) and `dev.js`/`dev.css` (loaded by `?dev=1`, tested).
- **Officer wording in `render.py`/`compact.py`** (19 golden files lock it) and the **`/samples`, `/sessions/{id}/claim`, `/assess` API routes**: unused by the customer page but documented, tested (`test_api.py`) and part of the public API. Removing them would be a behaviour change.
- **`OfflineAgent`, `LocalRetriever`**: needed by the 445 tests (`conftest.py` forces them). I will only add a "test/development only, not the real agent" label to their docstrings/comments. They are **not moved**, so no import can break.
- `data/*` (all four kinds), `tests/golden/answers/*`, `tools/chunk_policy.py`, `tools/chunk_generic.py`, the policy PDF, the demo documents, `docs/SYSTEM_REPORT.md`.
- `scripts/eval/demo_check.py`: runs the 8 demo questions through the real agent in officer mode. It stays because it is the only regression check of those answers.

## 4. Other changes (Stage 2 steps 3 and 4, Stage 3)

- **Requirements.** `requirements.txt` becomes runtime only, pinned to the tested versions: `fastapi==0.141.1`, `uvicorn[standard]==0.53.0`, `pydantic==2.13.5`, `python-dotenv==1.2.3`, `openai==3.16.2`, `azure-identity==1.25.3`, `azure-search-documents==12.0.0`, `azure-ai-projects==2.7.0`, `pypdf==6.19.0`, `python-multipart==0.0.32`. `requirements-dev.txt`: `-r requirements.txt`, `pytest==9.1.1`, `httpx==0.28.1`, `playwright==1.63.0`. Optional analysis tools (`coverage`, `vulture`, `ruff`) go in a comment. `pytest` and `httpx` leave the runtime file. `azure-storage-blob` is not listed today and is an `azure-ai-projects` dependency, so nothing to remove. `requests` (KB downloader) is noted in `tools/kb_sources/` only.
- **`.gitignore`**: add `.env.bak`, `.coverage*`, `htmlcov/`, `*.log`, `node_modules/` (`.env`, `.env.*`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.DS_Store`, `*.pyc`, `*.zip`, `kb/` already there).
- **`.dockerignore`** (new): `.env*` (keeps `.env.example` out too), `.venv`, `.git`, `tests`, `docs`, `scripts`, `tools`, `__pycache__`, `.pytest_cache`, and `demo/` **except `demo/documents/`** (see section 5, this differs from your list on purpose).
- **Stage 3 facts** (from section 11 of the audit): test count 445; Azure paths that run for real; agent v7; customer page at `/`; officer console removed; new layout; how to run; `EMBEDDING_DEPLOYMENT` default and `.env.example` → `text-embedding-3-large`; `registry.py` docstring "see agent/foundry_agent.py" → `agent/runner.py`; CLAUDE.md shortened. P0/P1 items are **not** touched.

## 5. Things I need you to decide or confirm with "go"

1. **`demo/documents` is needed at runtime.** The customer page's "Use sample documents" link reads the 10 PDFs from `intake.SAMPLE_DIR`. You asked for `.dockerignore` to exclude `demo`; doing that literally would break "Use sample documents" in a container. My plan: keep them at `demo/documents/` (as you specified) and let `.dockerignore` exclude everything else in `demo/`. Say so if you would rather move them to `data/`.
2. **`test_cases.json` (pack copy).** Same 12 case ids as `data/sample_claims.json` but a different structure, unreferenced by code. I could not prove it is a pure duplicate, so I keep it (moved to `demo/original_pack/`) instead of deleting it.
3. **`FIRST_PROMPT.md`**: deleted (in git history and the tag). Tell me if you want it kept in `docs/`.
4. **`docs/DEMO.md` / `demo_check.py`**: the 8-step script describes the removed officer console. In Stage 3 I will rewrite DEMO.md around the customer page (same questions, same numbers) and keep `demo_check.py` unchanged.
5. `scripts/dev/take_screenshots.py` will import the test helper `tests/helpers/pdfmaker.py` (to build the "bill in another name" file). I keep that dependency rather than copy the helper.

## 6. Execution order (Stage 2, after your "go"), each step = one commit + full test run

1. Delete dead code and unused imports/locals (3.1, last two rows) → tests
2. Remove the officer console and its tests/screenshot script → tests
3. Delete duplicate sample PDFs, duplicate eval questions, `FIRST_PROMPT.md` → tests
4. Move reference data (`demo/documents`, tools/source, kb_sources, original_pack, DEMO.md, screenshots, examples, evidence) with all path references → tests
5. Move scripts into `scripts/{setup,eval,dev}`, `tests/helpers`, fix root paths and docs → tests + run each script's offline/`--help` path
6. Requirements split and pinning → tests (+ `pip install --dry-run` check of the pins)
7. `.gitignore`, `.dockerignore` → tests
8. Stage 3 facts (docs, defaults, docstring) → tests
9. Stage 4: prove it (suite, live 6-question comparison, `run_demo.sh`, `/`, "Use sample documents", "Add a document"), write `docs/CLEANUP_REPORT.md`

## 7. What this plan did not verify

- Whether `demo_check.py` still passes 8/8 (not run in this stage).
- Coverage of `scripts/` and `tools/` (measured only for `app/`; their liveness comes from references and running them).
- Whether any external document or the user's own notes point at paths that will move (I can only search this repository).
