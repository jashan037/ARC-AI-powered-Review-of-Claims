# ARC security notes

Synthetic data only. These are the rules the code enforces and the steps you follow. Nothing here needs a key to be written down anywhere except `.env`.

## What the code enforces

- **No developer surface by default.** `/docs`, `/openapi.json`, `/samples`, `/assess` and `/sessions/{id}/claim` answer 404 unless the server starts with `DEBUG=1`. `/health` returns only `{"status":"ok"}`.
- **Headers on every response** (`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`, `frame-ancestors 'none'`, `Cache-Control`); the page adds a strict CSP (no inline script or style, no CDN, same-origin requests only).
- **Sessions:** deleted after 30 minutes idle (`SESSION_TTL_S`), at most 100 documents (`MAX_DOCS_PER_SESSION`) and 200 chat turns (`MAX_MESSAGES_PER_SESSION`) each. `DELETE /sessions/{id}` erases everything for that session.
- **Rate limit** per IP: 120 requests a minute overall, 20 chat turns a minute (`RATE_LIMIT_PER_MIN`, `CHAT_RATE_LIMIT_PER_MIN`). It is in memory: with several workers or servers put a real limiter in front.
- **Logs and errors:** one JSON line per turn with no question, claim data or answer; every log line passes through a redactor (keys, bearer tokens, auth headers); error responses carry a fixed sentence and never an exception message.
- **Secrets:** `.env` is git-ignored and mode 600. `scripts/security/scan_secrets.py` looks for the values in `.env` and for key-shaped strings in the working tree and the whole git history, printing only file names. A git pre-commit hook (`sh scripts/security/install_hooks.sh`) and a test (`tests/test_secrets.py`) block a commit that contains one.

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

## Rules

- Never paste `.env`, a key, a bearer token or a screenshot of a Keys page into chat, an issue, a ticket or a commit. Screenshots of the app are fine; screenshots of the portal Keys or Overview pages are not.
- Secrets go only in `.env`. Only synthetic data is uploaded.
- Do not run with `DEBUG=1` or `DEBUG_TRACE=1` on a shared or public server.
- The demo has no login. Do not expose it to the internet as it is (see `docs/SYSTEM_REPORT.md` P0).
