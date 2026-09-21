"""Look for the values of the secrets in .env (and for key-shaped strings) in the working tree and in the whole git history, WITHOUT printing any secret.
Prints only file names, commit ids and counts.

    python scripts/security/scan_secrets.py             # exact values from .env, tree and history
    python scripts/security/scan_secrets.py --shapes    # key-shaped strings only (what the pre-commit hook and the test use; needs no .env)
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}
SKIP_FILES = {".env"}
# shapes: a 32+ character key next to a key-like word, an Azure storage or connection string, a bearer token, a private key block
SHAPES = [
    re.compile(r"(?i)\b[A-Za-z_]*(?:api[-_ ]?key|key|secret|token|password|passwd)\b['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9+/_\-]{32,}={0,2})['\"]?"),
    re.compile(r"(?i)(?:AccountKey|SharedAccessSignature|Authorization: Bearer)\s*[=:]\s*[A-Za-z0-9+/_\-.]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|pk)-[A-Za-z0-9]{32,}\b"),
]
PLACEHOLDER = re.compile(r"(?i)x{6,}|<[^>]+>|your[-_ ]|example|changeme|placeholder|fake|dummy|test[-_]?key|0{8,}|\.\.\.|\*{4,}")


def env_secrets() -> dict[str, str]:
    """name -> value for every KEY, SECRET, TOKEN or PASSWORD variable in .env (values never leave this process)."""
    out = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                v = v.split(" #")[0].strip().strip("'\"")
                if re.search(r"KEY|SECRET|TOKEN|PASSWORD", k, re.I) and len(v) >= 8:
                    out[k.strip()] = v
    return out


def tree_files():
    for p in ROOT.rglob("*"):
        if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS) and p.name not in SKIP_FILES and p.stat().st_size < 3_000_000:
            yield p


def shape_hits(text: str) -> int:
    n = 0
    for rx in SHAPES:
        for m in rx.finditer(text):
            if not PLACEHOLDER.search(m.group(0)):
                n += 1
    return n


def scan_shapes(paths) -> list[str]:
    bad = []
    for p in paths:
        try:
            t = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if shape_hits(t):
            bad.append(str(p.relative_to(ROOT)))
    return bad


def scan_values() -> int:
    secrets = env_secrets()
    print(f".env: {len(secrets)} secret-like values (sha256 prefixes: " + ", ".join(hashlib.sha256(v.encode()).hexdigest()[:8] for v in secrets.values()) + ")")
    found = 0
    for p in tree_files():
        try:
            t = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, v in secrets.items():
            if v in t:
                print(f"TREE  {name} value found in {p.relative_to(ROOT)}")
                found += 1
    log = subprocess.Popen(["git", "log", "--all", "-p", "--no-color", "--format=commit %H"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="ignore")
    commit = "?"
    seen = set()
    for line in log.stdout:
        if line.startswith("commit "):
            commit = line[7:15]
            continue
        if line[:1] in "+-":
            for name, v in secrets.items():
                if v in line and (name, commit) not in seen:
                    seen.add((name, commit))
                    print(f"HISTORY  {name} value in commit {commit}")
                    found += 1
    print(f"history commits scanned: {subprocess.run(['git', 'rev-list', '--all', '--count'], cwd=ROOT, capture_output=True, text=True).stdout.strip()}")
    return found


if __name__ == "__main__":
    if "--shapes" in sys.argv:
        bad = scan_shapes(tree_files())
        print("key-shaped strings in: " + (", ".join(bad) if bad else "none"))
        sys.exit(1 if bad else 0)
    n = scan_values()
    bad = scan_shapes(tree_files())
    print("key-shaped strings in the tree: " + (", ".join(bad) if bad else "none"))
    print("RESULT:", "SECRETS FOUND (rotate them)" if n else "no secret value from .env is in the tree or in the git history")
    sys.exit(1 if n or bad else 0)
