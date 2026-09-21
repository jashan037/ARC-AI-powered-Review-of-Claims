# ARC security notes

Synthetic data only. These are the rules the code enforces and the steps you follow. Nothing here needs a key to be written down anywhere except `.env`.

## What the code enforces

- **No developer surface by default.** `/docs`, `/openapi.json`, `/samples`, `/assess` and `/sessions/{id}/claim` answer 404 unless the server starts with `DEBUG=1`. `/health` returns only `{"status":"ok"}`; `/ready` says whether Search and the agent can be reached, as two booleans and a word, cached for 30 seconds, and never names an endpoint, a key or an exception.
- **Headers on every response** (`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`, `frame-ancestors 'none'`, `Cache-Control`); the page adds a strict CSP (no inline script or style, no CDN, same-origin requests only).
- **Sessions:** deleted after 30 minutes idle (`SESSION_TTL_S`), at most 100 documents (`MAX_DOCS_PER_SESSION`) and 200 chat turns (`MAX_MESSAGES_PER_SESSION`) each. `DELETE /sessions/{id}` erases everything for that session, and "Start over" in the page calls it.
- **Documents:** 5 MB and 20 pages per file, 26 MB per upload request, and a 10-second parsing budget per file (`intake.MAX_PARSE_SECONDS`), so a slow or crafted PDF cannot keep a worker busy. Text from a document is cleaned, capped and quoted before it reaches the model (`app/tools/sanitize.py`).
- **The claims-team report** (`GET /sessions/{id}/report.pdf`) is built in code from the documents, carries nothing from the chat, is never stored, and is served `no-store` as an attachment.
- **Rate limit** per IP: 120 requests a minute overall, 20 chat turns a minute (`RATE_LIMIT_PER_MIN`, `CHAT_RATE_LIMIT_PER_MIN`). It is in memory: with several workers or servers put a real limiter in front.
- **Logs and errors:** one JSON line per turn with no question, claim data or answer - ids, tool names, status, latency, retry counts and the model-call token COUNTS only; every log line passes through a redactor (keys, bearer tokens, auth headers); error responses carry a fixed sentence and never an exception message.
- **Secrets:** `.env` is git-ignored and mode 600. `scripts/security/scan_secrets.py` looks for the values in `.env` (printing only their SHA-256 prefixes) and for key-shaped strings in the working tree and in every line any commit ever added, printing only file names and commit ids. A git pre-commit hook (`sh scripts/security/install_hooks.sh`) and tests (`tests/test_secrets.py`, `tests/test_security.py`) block a commit that contains one. `detect-secrets` (in `requirements-dev.txt`) is the second opinion:

  ```bash
  python scripts/security/scan_secrets.py
  detect-secrets scan --exclude-files '\.venv/|\.git/|\.pdf$|\.png$|^\.env$|tests/helpers/axe\.min\.js'
  ```

  The three findings `detect-secrets` reports are placeholders, not secrets: two invented strings in `scripts/setup/bootstrap_azure.py`'s own self-test and the fake key `tests/test_secrets.py` assembles at run time to prove the detector works.

## Keyless mode (AUTH_MODE=entra)

`AUTH_MODE=key` (default) uses the keys in `.env`. `AUTH_MODE=entra` uses `DefaultAzureCredential` (`az login` locally, a managed identity when deployed) for Azure AI Search queries and for embeddings, so the running app needs no key. Chat itself already uses Entra (`az login`, role Foundry User). Roles to assign, checked on Microsoft Learn on 21 Sep 2026:

| Resource | Role | Why |
|---|---|---|
| Search service `claims-search-37` (or scope it to the index `claims-kb-v2`) | **Search Index Data Reader** (`1407120a-92aa-4202-b7e9-c0e197c71c8f`) | run queries |
| Foundry / OpenAI resource `claims-agent-project-res` | **Cognitive Services OpenAI User** | embeddings calls with Entra |

Also switch the search service's API access control to **Role-based access control** or **Both** (portal: search service > Settings > Keys). If a request carries an API key the service uses the key. A managed-identity assignment can take hours to apply. **No role has been assigned by the code or by Claude**; you assign them, then set `AUTH_MODE=entra` and remove the two keys from `.env`. The index-building scripts (`create_index.py`, `upload_chunks.py`) still use the admin key on purpose.

## Key-rotation checklist

Do this whenever a key might have leaked (a screenshot of the Keys page, a pasted `.env`, a log, a commit), and once every 90 days.

1. Portal > search service > Settings > Keys: **regenerate the Secondary admin key**, put it in `.env` as `AZURE_SEARCH_KEY`, restart the app, confirm a chat turn works.
2. Regenerate the **Primary** key (the old one is now dead). Repeat for the OpenAI resource: Keys and Endpoint > regenerate KEY 2, switch `.env` to it, regenerate KEY 1.
3. `python scripts/security/scan_secrets.py` must say no secret value is in the tree or history. If a value is in git history, the key is burnt: rotating is the fix, rewriting history is optional.
4. `chmod 600 .env`; check `git status` shows no `.env`.
5. Prefer `AUTH_MODE=entra` so there is nothing to rotate.

## Packaging

`Dockerfile` builds a single-instance image: one uvicorn worker, a non-root user, no `.env` (`.dockerignore` excludes it, the git
directory, the tests, the docs, the scripts and everything under `demo/` except the three sample document sets the app reads at
runtime). `docs/DEPLOY.md` is the deployment path, written out and **not executed**.

## Rules

- Never paste `.env`, a key, a bearer token or a screenshot of a Keys page into chat, an issue, a ticket or a commit. Screenshots of the app are fine; screenshots of the portal Keys or Overview pages are not.
- Secrets go only in `.env`. Only synthetic data is uploaded.
- Do not run with `DEBUG=1` or `DEBUG_TRACE=1` on a shared or public server.
- The demo has no login. Do not expose it to the internet as it is (see `docs/SYSTEM_REPORT.md` P0).
