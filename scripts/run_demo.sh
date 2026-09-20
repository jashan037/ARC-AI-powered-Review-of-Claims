#!/usr/bin/env bash
# Start ARC for a demo on port 8765, but only against the REAL agent.
#
#   scripts/run_demo.sh            start the app (refuses unless RETRIEVER=azure and AGENT_MODE=foundry)
#   scripts/run_demo.sh --check    only check the settings, then exit (0 = ready, 1 = refused)
#
# The settings are read the same way the app reads them: a variable already set in your shell wins over the .env file.
# Environment: ENV_FILE (default .env), PORT (default 8765).
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="${ENV_FILE:-.env}"
PORT="${PORT:-8765}"

refuse() {
  echo "REFUSING TO START: $1" >&2
  echo "A demo must use the real agent. In $ENV_FILE set  RETRIEVER=azure  and  AGENT_MODE=foundry  (and unset them in your shell if you exported others), then run this again." >&2
  exit 1
}

value() {   # last KEY=value line of the env file, without a trailing comment or spaces
  grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//' | xargs || true
}

[ -f "$ENV_FILE" ] || [ -n "${RETRIEVER:-}" ] || refuse "$ENV_FILE not found"
retriever="${RETRIEVER:-$(value RETRIEVER)}"
agent_mode="${AGENT_MODE:-$(value AGENT_MODE)}"

[ "$retriever" = "azure" ]   || refuse "RETRIEVER is '${retriever:-unset}', not 'azure'"
[ "$agent_mode" = "foundry" ] || refuse "AGENT_MODE is '${agent_mode:-unset}', not 'foundry'"

echo "Settings OK: RETRIEVER=azure, AGENT_MODE=foundry (live agent)."
[ "${1:-}" = "--check" ] && exit 0

if lsof -i ":$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  refuse "port $PORT is already in use"
fi

UVICORN=".venv/bin/uvicorn"
[ -x "$UVICORN" ] || UVICORN="uvicorn"
echo "Starting ARC. Open  http://127.0.0.1:$PORT/   (Ctrl+C to stop)"
exec "$UVICORN" app.main:app --port "$PORT"
