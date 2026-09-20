# Cleanup report

20 Sep 2026. Scope: reorganise and clean the repository. **No new features, no behaviour changes.** Undo everything with `git reset --hard pre-cleanup` (tag on commit `2c275af`). Not touched: Azure resources, the deployed agent (still version 7), `.env`. P0 and P1 items of `docs/SYSTEM_REPORT.md` are deliberately **not** fixed and remain open.

## Result

| | Before (`pre-cleanup`) | After |
|---|---:|---:|
| Tests | 445 passed | **439 passed**, 0 failed, 0 skipped (the 6 removed tests only asserted on the deleted officer console) |
| Tracked files | 168 | 158 |
| Tracked size | 3.7 MB | 3.6 MB |
| Text lines (excl. png/pdf) | 20,831 | 19,690 (includes the two new cleanup documents) |
| `app/` Python | 3,243 | 3,231 |
| `app/static` js / css / html | 1,266 / 403 / 176 | 792 / 199 / 100 |
| tests Python | 3,234 | 3,148 |
| scripts Python + sh | 1,460 + 42 (+159 mjs) | 1,459 + 42 |
| tools Python | 338 (+71 kb pack) | 407 incl. moved kb scripts (338 + 71 − 2 dead constants) |
| ruff F401/F841/F811 | 12 findings | 0 |

## Commits (each followed by the full test run)

1. `checkpoint before cleanup` (tag `pre-cleanup`), `add cleanup plan`
2. `remove dead code, unused imports and variables`
3. `remove the officer console and its tests`
4. `delete duplicate sample PDFs, duplicate eval questions and the first-prompt file`
5. `move sample documents, screenshots, examples, evidence and source files into demo/, docs/evidence and tools/`
6. `move scripts into setup, eval and dev folders and the test helper into tests/helpers`
7. `split requirements into pinned runtime and dev files`
8. `update .gitignore and add .dockerignore`
9. `update docs and defaults to match reality, label test-only code`

## Deleted

- Officer console: `app/static/officer.html`, `arc.js`, `arc.css`, `labels.js`, the `/officer` route (now 404), `scripts/take_screenshots.mjs`, and 6 officer-only tests (5 more tests in `tests/test_web_ui.py` were trimmed, not deleted). The officer wording mode in the renderers is kept.
- Duplicates (verified byte-identical with `cmp`): the 5 PDFs in `reference/claims_data_pack/demo_claim/` (the same 5 exist in the 10-PDF set), `claim_manifest.json`, `reference/claims_data_pack/test_cases/rag_eval_questions.json` (same as `data/rag_eval_questions.json`).
- `FIRST_PROMPT.md` (superseded by `CLAUDE.md`).
- Dead code: `Chunk.as_dict`, `DOC_NAMES`, `RE_NUM`, `skip_pages_as_tables`; unused imports in `runner.py` (2), `intake.py`, `compact.py`, `retrieval/base.py`, `download_kb.py`, `tests/test_intake_api.py`; unused variables in `render.py` (5) and `bootstrap_azure.py`.

## Moved (old → new; 71 renames in git, all history kept)

| Old | New |
|---|---|
| `reference/demo_documents/*` | `demo/documents/*` (runtime path `intake.SAMPLE_DIR`) |
| `reference/claims_data_pack/demo_claim/expected_extraction.json` | `demo/documents/expected_extraction.json` |
| `reference/claims_data_pack/test_cases/test_cases.json`, `.../README.md` | `demo/original_pack/` |
| `reference/policy/optima-secure-HDFHLIP25041V062425.pdf` | `tools/source/` |
| `reference/kb_sources_pack/*` | `tools/kb_sources/` |
| `docs/screenshots/*` (8), `docs/DEMO.md` | `demo/screenshots/`, `demo/DEMO.md` |
| `examples/*.md` (19) | `demo/examples/` |
| `examples/eval_report.md`, `examples/eval_failures/*` | `docs/evidence/` |
| `scripts/{create_index,upload_chunks,create_agent,bootstrap_azure,check_env}.py` | `scripts/setup/` |
| `scripts/{eval_retrieval,eval_agent,demo_check}.py` | `scripts/eval/` |
| `scripts/{chat_cli,render_samples,render_examples,take_screenshots}.py` | `scripts/dev/` |
| `tests/pdfmaker.py` | `tests/helpers/pdfmaker.py` |
| `scripts/run_demo.sh` | unchanged |

Every moved script's repo-root computation, `bootstrap_azure.py`'s sibling-script lookup, default output folders, test paths and all documented commands were updated in the same commits.

## Other changes

- `requirements.txt` is runtime only and pinned (fastapi 0.141.1, uvicorn 0.53.0, pydantic 2.13.5, python-dotenv 1.2.3, python-multipart 0.0.32, pypdf 6.19.0, openai 3.16.2, azure-identity 1.25.3, azure-search-documents 12.0.0, azure-ai-projects 2.7.0). `requirements-dev.txt` adds pytest, httpx, playwright. `pip install --dry-run` and `pip check`: nothing to change, no conflicts. `azure-storage-blob` was never listed; it is installed only as a dependency of `azure-ai-projects`.
- `.gitignore` extended (`.coverage*`, `htmlcov/`, `*.log`, `node_modules/`; `.env`, `.env.*`, `.env.bak` ignored). New `.dockerignore` excludes `.env*`, `.venv`, `.git`, `tests`, `docs`, `scripts`, `tools`, the docs files, and `demo/` **except `demo/documents/`**, because "Use sample documents" reads those PDFs at runtime (a deliberate departure from "exclude demo"). No Docker build was tried.
- Stale facts fixed: `EMBEDDING_DEPLOYMENT` default and `.env.example` now `text-embedding-3-large`; the `.env.example` endpoint is a placeholder instead of the old `openai-claims-agent` name; `registry.py` docstring points at `agent/runner.py`; `OfflineAgent` and `LocalRetriever` are labelled test/development only (comments, not moved); `CLAUDE.md` rewritten short (158 → 105 lines), `README.md` rewritten, `demo/DEMO.md` updated for the customer page, agent v7 and the new paths; a banner at the top of `docs/SYSTEM_REPORT.md` says it describes the pre-cleanup state.

## Proof that nothing broke

- **Tests:** 439 passed after every commit (final run at the end of this stage).
- **Live comparison (real Azure agent v7, live Search):** sample documents, six questions, "Add a document", saved before and after outside the repo.
  - Intake: same files recognised, **byte-identical claim, summary and suggestion chips**.
  - Same answer types and tool traces for all six questions. Byte-identical summaries for "How much will be paid?", "Why was my room rent reduced?" and "what's my name". Numbers identical for five of six (₹1,22,125 / ₹1,01,625 / ₹20,500 / ₹12,000 / ₹37,875 / ₹12,500 / ₹1,84,500).
  - "Which items are not payable?": the model chose a narrower focus this time, so the answer shows a subset of the earlier figures; nothing new or different. "What documents are missing?" and the cataract answer differ only in model-written wording.
  - No internal term (tool names, result ids, chunk keys, UIN) in any answer.
- **Browser (Chrome via Playwright) on the cleaned app started with `scripts/run_demo.sh`:** `/` loads with 10 checklist items, "Use sample documents" recognises 9 of 10, Continue shows the summary and 3 suggestions, the chip answer shows ₹1,22,125 and ₹1,01,625, "Add a document" adds the KYC form and re-checks the claim, no console or page errors. `/officer` returns 404.
- **Scripts run after the move:** `check_env.py --offline`, `bootstrap_azure.py selftest`, `eval_retrieval.py` (hit@5 16/16), `eval_agent.py --offline` on two cases, `render_samples.py` (12 files), `render_examples.py` (7 files), `chat_cli.py`, `take_screenshots.py --help`, `demo_check.py` (see below).

## Things I could not verify, or left because I was not sure

- **`demo_check.py` ran twice by accident of having no offline mode** (it uses the real agent because `.env` selects it): the first run said **7 of 8** and I did not capture which step failed; the second run was **8 of 8**. I treat it as a model-wording flake (the DEMO says wording varies) but this is not proven. The full 38-case evaluation was **not** re-run after the cleanup (the prompt and agent did not change).
- `create_index.py`, `upload_chunks.py` and `create_agent.py` were **not executed** (they change Azure resources); they compile and their repo-root path was checked. The `bootstrap_azure.py all` path that starts the moved scripts was not run (only `selftest`). `tools/kb_sources/ingest_kb.py` and `download_kb.py` (need internet and `requests`) were not run; their relative paths are unchanged but untested.
- No Docker build was run; `.dockerignore` is untested.
- **`demo/original_pack/test_cases.json`** (same 12 case ids as `data/sample_claims.json`, different structure) was kept because I could not prove it is a pure duplicate.
- `scripts/dev/take_screenshots.py` still imports the test helper `tests/helpers/pdfmaker.py`.
- `docs/CLEANUP_PLAN.md`, `docs/SYSTEM_REPORT.md` and `docs/evidence/*` keep their original (pre-cleanup) paths in their text on purpose; they are historical records.
- I installed `coverage`, `vulture` and `ruff` into the local `.venv` for the analysis; they are not in any requirements file (only mentioned in a comment of `requirements-dev.txt`).

## Action for you

**Restart your demo server on port 8765.** It started before the cleanup and still looks for the sample PDFs in the old `reference/demo_documents` folder, so "Use sample documents" would find nothing there until you stop it and run `scripts/run_demo.sh` again. (My own test server on port 8790 runs the cleaned code.)
