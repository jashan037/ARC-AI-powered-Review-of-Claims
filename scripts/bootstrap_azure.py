#!/usr/bin/env python3
"""Discover your Azure settings with the Azure CLI, write .env, and (optionally) run the whole setup.

    az login                                        # once, with the account that owns the project
    python scripts/bootstrap_azure.py env --dry-run # show what it found (secrets masked), change nothing
    python scripts/bootstrap_azure.py env           # write .env (old one saved as .env.bak)
    python scripts/bootstrap_azure.py role          # check the Foundry User role, offer to assign it
    python scripts/bootstrap_azure.py all           # env, role, check_env, index, upload, eval, agent, smoke test
    python scripts/bootstrap_azure.py selftest      # tests the parsing logic with fake `az` output (no Azure needed)

Secrets are written only to .env (which is git-ignored) and are never printed in full.
Written from the documented `az` command shapes and NOT yet run against your subscription:
if a step cannot find something, it says what it looked for.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# "Azure AI User", now renamed "Foundry User". Role IDs are unchanged by the rename.
FOUNDRY_USER_ROLE_ID = "53ca6127-db72-4e31-b599-04dc5da150b4"
SECRET_KEYS = {"AZURE_SEARCH_KEY", "AZURE_OPENAI_KEY"}


class AzError(RuntimeError):
    pass


# ------------------------------------------------------------------ az helpers
def _run(cmd: list[str]) -> str:
    exe = shutil.which(cmd[0])
    if not exe:
        raise AzError("Azure CLI (`az`) not found. Install it (https://learn.microsoft.com/cli/azure/install-azure-cli) and run `az login`.")
    p = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True)
    if p.returncode != 0:
        raise AzError(f"`{' '.join(cmd[:5])} ...` failed: {p.stderr.strip()[:500]}")
    return p.stdout


def az(*args):
    out = _run(["az", *args, "-o", "json"])
    return json.loads(out) if out.strip() else None


def mask(v: str) -> str:
    return "(empty)" if not v else (v[:3] + "…" + v[-3:] if len(v) > 10 else "***")


def pick(items, label, prefer=None, name=lambda x: x["name"]):
    if not items:
        raise AzError(f"No {label} found in the resource group.")
    if prefer:
        for i in items:
            if prefer in name(i):
                return i
    if len(items) > 1:
        print(f"  note: several {label} found {[name(i) for i in items]}; using {name(items[0])}")
    return items[0]


# ------------------------------------------------------------------ discovery
def discover(rg: str, project: str | None = None, embedding: str | None = None) -> dict:
    acct = az("account", "show")
    user = acct["user"]["name"]
    print(f"Signed in as {user}, subscription {acct.get('name', acct['id'])}")

    svc = pick(az("search", "service", "list", "-g", rg) or [], "search service", prefer="claims")
    skey = az("search", "admin-key", "show", "-g", rg, "--service-name", svc["name"])["primaryKey"]
    print(f"  search service: {svc['name']}")

    accounts = az("cognitiveservices", "account", "list", "-g", rg) or []
    with_deps = []
    for a in accounts:
        try:
            deps = az("cognitiveservices", "account", "deployment", "list", "-g", rg, "-n", a["name"]) or []
        except AzError:
            deps = []
        if deps:
            with_deps.append((a, deps))
    if not with_deps:
        raise AzError("No model deployments found in any Foundry/OpenAI resource of this resource group.")

    def model(d):
        return (d.get("properties", {}).get("model", {}) or {}).get("name", "")

    emb = [(a, d) for a, deps in with_deps for d in deps if model(d).startswith("text-embedding")]
    if embedding:
        emb = [x for x in emb if x[1]["name"] == embedding] or emb
    if not emb:
        raise AzError("No text-embedding deployment found. Deploy one in the Foundry portal (Build > Models).")
    if not embedding:  # default to text-embedding-3-large (1536 dims via EMBEDDING_DIMENSIONS) when deployed
        emb.sort(key=lambda x: model(x[1]) != "text-embedding-3-large")
    emb_acct, emb_dep = emb[0]

    chats = [(a, d) for a, deps in with_deps for d in deps if model(d).startswith("gpt")]
    if not chats:
        raise AzError("No gpt deployment found.")
    chat = next((x for x in chats if x[1]["name"] == "gpt-5-mini"), next((x for x in chats if model(x[1]).startswith("gpt-5")), chats[0]))
    chat_acct, chat_dep = chat
    print(f"  embedding deployment: {emb_dep['name']} (model {model(emb_dep)}) in {emb_acct['name']}")
    print(f"  chat deployment:      {chat_dep['name']} (model {model(chat_dep)}) in {chat_acct['name']}")

    props = emb_acct.get("properties", {})
    endpoints = props.get("endpoints") or {}
    oai = next((v for k, v in endpoints.items() if "openai" in k.lower() and "openai.azure.com" in v), None)
    if not oai:
        oai = f"https://{props.get('customSubDomainName') or emb_acct['name']}.openai.azure.com/"
    okey = az("cognitiveservices", "account", "keys", "list", "-g", rg, "-n", emb_acct["name"])["key1"]

    projects = []
    for r in az("resource", "list", "-g", rg, "--resource-type", "Microsoft.CognitiveServices/accounts/projects") or []:
        m = re.search(r"/accounts/([^/]+)/projects/([^/]+)$", r["id"])
        if m:
            projects.append((m.group(1), m.group(2)))
    if project:
        projects = [p for p in projects if p[1] == project] or projects
    if not projects:
        raise AzError("No Foundry project found in the resource group. Create one in the Foundry portal first.")
    proj_acct_name, proj_name = next((p for p in projects if p[0] == chat_acct["name"]), projects[0])
    proj_acct = next((a for a in accounts if a["name"] == proj_acct_name), chat_acct)
    sub = proj_acct.get("properties", {}).get("customSubDomainName") or proj_acct["name"]
    print(f"  foundry project: {proj_name} on {proj_acct_name}")

    return dict(user=user, rg=rg, subscription_id=acct["id"], account_id=proj_acct["id"],
                env={"AZURE_SEARCH_ENDPOINT": f"https://{svc['name']}.search.windows.net", "AZURE_SEARCH_KEY": skey,
                     "AZURE_OPENAI_ENDPOINT": oai, "AZURE_OPENAI_KEY": okey, "EMBEDDING_DEPLOYMENT": emb_dep["name"],
                     "FOUNDRY_PROJECT_ENDPOINT": f"https://{sub}.services.ai.azure.com/api/projects/{proj_name}",
                     "MODEL_DEPLOYMENT": chat_dep["name"]})


# ------------------------------------------------------------------ .env handling
def merge_env(text: str, updates: dict) -> str:
    out, seen = [], set()
    for ln in text.splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", ln)
        if m and m.group(1) in updates:
            k = m.group(1)
            comment = re.search(r"\s+#.*$", m.group(2))
            out.append(f"{k}={updates[k]}" + (comment.group(0) if comment else ""))
            seen.add(k)
        else:
            out.append(ln)
    out += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    return "\n".join(out) + "\n"


def current_env_values(text: str) -> dict:
    vals = {}
    for ln in text.splitlines():
        m = re.match(r"^([A-Z0-9_]+)=([^#\n]*)", ln)
        if m:
            vals[m.group(1)] = m.group(2).strip()
    return vals


def cmd_env(args, found=None) -> dict:
    found = found or discover(args.rg, args.project, args.embedding)
    env_path, example = ROOT / ".env", ROOT / ".env.example"
    old_text = env_path.read_text() if env_path.exists() else (example.read_text() if example.exists() else "")
    old = current_env_values(old_text)
    updates = dict(found["env"])
    if "EMBEDDING_DIMENSIONS" not in old:
        updates["EMBEDDING_DIMENSIONS"] = "1536"
    print("\n.env changes" + (" (dry run, nothing written)" if args.dry_run else ""))
    for k, v in updates.items():
        show = mask if k in SECRET_KEYS else (lambda x: x or "(empty)")
        same = old.get(k) == v
        print(f"  {'=' if same else '~'} {k}: {show(old.get(k, ''))} -> {show(v)}" if not same else f"  = {k}: unchanged")
    if not args.dry_run:
        if env_path.exists():
            shutil.copy(env_path, ROOT / ".env.bak")
        env_path.write_text(merge_env(old_text, updates))
        try:
            os.chmod(env_path, 0o600)
        except OSError:
            pass
        print("Wrote .env" + (" (previous copy saved as .env.bak)" if (ROOT / ".env.bak").exists() else ""))
    return found


# ------------------------------------------------------------------ role
def has_foundry_role(user: str, scope: str) -> bool:
    try:  # personal (gmail) accounts are guests in the tenant: the email is not resolvable, the object id is
        assignee = az("ad", "signed-in-user", "show")["id"]
    except (AzError, KeyError, TypeError):
        assignee = user
    lst = az("role", "assignment", "list", "--assignee", assignee, "--scope", scope, "--include-inherited") or []
    return any(r.get("roleDefinitionName") in ("Azure AI User", "Foundry User") or str(r.get("roleDefinitionId", "")).endswith(FOUNDRY_USER_ROLE_ID) for r in lst)


def cmd_role(args, found=None) -> str:
    """Returns 'had', 'assigned' or 'skipped'."""
    found = found or discover(args.rg, args.project, args.embedding)
    scope, user = found["account_id"], found["user"]
    if has_foundry_role(user, scope):
        print(f"Role check: {user} already has Foundry User (Azure AI User) on the Foundry resource.")
        return "had"
    print(f"Role check: {user} does NOT have Foundry User on the Foundry resource.")
    if not args.yes and input("Assign it now? [y/N] ").strip().lower() != "y":
        print("Skipped. Assign it in the portal: Foundry resource > Access control (IAM) > Add role assignment > Foundry User.")
        return "skipped"
    try:
        oid = az("ad", "signed-in-user", "show")["id"]
        az("role", "assignment", "create", "--assignee-object-id", oid, "--assignee-principal-type", "User", "--role", FOUNDRY_USER_ROLE_ID, "--scope", scope)
    except AzError:
        az("role", "assignment", "create", "--assignee", user, "--role", FOUNDRY_USER_ROLE_ID, "--scope", scope)
    print("Role assigned. It can take about 5 minutes to take effect.")
    return "assigned"


# ------------------------------------------------------------------ the whole setup
def run_script(name: str, extra_env: dict | None = None, capture: bool = False):
    env = {**os.environ, **(extra_env or {})}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / name)], cwd=ROOT, env=env, capture_output=capture, text=True)


def smoke_test() -> bool:
    os.environ.update(RETRIEVER="azure", AGENT_MODE="foundry")
    sys.path.insert(0, str(ROOT))
    from app.agent.runner import FoundryAgent   # imported late so the env vars above are respected
    samples = json.load(open(ROOT / "data" / "sample_claims.json", encoding="utf-8"))
    session = {"claim": samples["TC07"]["claim"], "uin": samples["TC07"]["claim"]["policy_uin"], "history": []}
    res = FoundryAgent().ask(session, "Assess this claim")
    ok = res.answer_type == "claim_assessment" and "₹1,22,125" in res.markdown and "₹1,01,625" in res.markdown
    print(f"  tools used: {' > '.join(t['tool'] for t in res.trace)}")
    print("  " + ("PASS: formatted assessment with ₹1,22,125 and ₹1,01,625" if ok else "FAIL: unexpected answer, first lines below"))
    if not ok:
        print("\n".join(res.markdown.splitlines()[:25]))
    return ok


def cmd_all(args):
    skip = set((args.skip or "").split(","))
    found, role_state = None, "had"
    if "env" not in skip:
        found = cmd_env(args)
    if "role" not in skip:
        role_state = cmd_role(args, found)
    if "check" not in skip:
        print("\n== check_env")
        if run_script("check_env.py").returncode != 0:
            sys.exit("check_env found problems. Fix the lines it lists, then re-run.")
    for label, script, extra in (("index", "create_index.py", None), ("upload", "upload_chunks.py", None), ("eval", "eval_retrieval.py", {"RETRIEVER": "azure"})):
        if label in skip:
            continue
        print(f"\n== {script}")
        if run_script(script, extra).returncode != 0 and label != "eval":
            sys.exit(f"{script} failed. Copy the error above to Claude Code.")
    if "agent" not in skip:
        if role_state == "assigned":
            print("\nWaiting for the new role to take effect (up to 5 minutes; Ctrl+C to skip the wait)...")
            try:
                time.sleep(300)
            except KeyboardInterrupt:
                pass
        print("\n== create_agent.py")
        for attempt in range(6):
            p = run_script("create_agent.py", capture=True)
            print(p.stdout + p.stderr)
            if p.returncode == 0:
                break
            if role_state == "assigned" and re.search(r"401|403|Unauthorized|PermissionDenied|AuthorizationFailed", p.stdout + p.stderr) and attempt < 5:
                print("Role not active yet, retrying in 60s...")
                time.sleep(60)
                continue
            sys.exit("create_agent.py failed. Copy the error above to Claude Code.")
    if "smoke" not in skip:
        print("\n== smoke test with the real agent (claim TC07)")
        if not smoke_test():
            sys.exit(1)
    if args.enable_azure:
        p = ROOT / ".env"
        p.write_text(merge_env(p.read_text(), {"RETRIEVER": "azure", "AGENT_MODE": "foundry"}))
        print("\nSet RETRIEVER=azure and AGENT_MODE=foundry in .env.")
    print("\nAll steps finished. Start the API with:\n  RETRIEVER=azure AGENT_MODE=foundry uvicorn app.main:app --reload")


# ------------------------------------------------------------------ selftest (fake `az`, no Azure needed)
def selftest():
    sub = "/subscriptions/s1/resourceGroups/rg-claims-agent/providers/Microsoft.CognitiveServices/accounts/claims-agent-project-res"
    fake = {
        "account show": {"id": "s1", "name": "Azure for Students", "user": {"name": "me@example.com"}},
        "search service list": [{"name": "claims-search-37"}],
        "search admin-key show": {"primaryKey": "SEARCHKEY0123456789ABCDEFGHIJKL"},
        "cognitiveservices account list": [{"name": "claims-agent-project-res", "id": sub, "kind": "AIServices",
            "properties": {"customSubDomainName": "claims-agent-project-res",
                           "endpoints": {"OpenAI Language Model Instance API": "https://claims-agent-project-res.openai.azure.com/",
                                         "AI Foundry API": "https://claims-agent-project-res.services.ai.azure.com/"}}}],
        "cognitiveservices account deployment": [{"name": "text-embedding-3-large", "properties": {"model": {"name": "text-embedding-3-large"}}},
                                                 {"name": "gpt-5-mini", "properties": {"model": {"name": "gpt-5-mini"}}}],
        "cognitiveservices account keys": {"key1": "OPENAIKEY0123456789ABCDEFGHIJKLM", "key2": "x"},
        "resource list": [{"id": sub + "/projects/jashanpreetsingh3999-6322"}],
        "role assignment list": [{"roleDefinitionName": "Owner", "roleDefinitionId": "/x/8e3af657"}],
        "ad signed-in-user show": {"id": "00000000-0000-0000-0000-000000000001"},
    }
    global _run
    real = _run
    _run = lambda cmd: json.dumps(next(v for k, v in fake.items() if k in " ".join(cmd)))
    try:
        d = discover("rg-claims-agent")
        e = d["env"]
        assert e["AZURE_SEARCH_ENDPOINT"] == "https://claims-search-37.search.windows.net"
        assert e["AZURE_OPENAI_ENDPOINT"] == "https://claims-agent-project-res.openai.azure.com/"
        assert e["EMBEDDING_DEPLOYMENT"] == "text-embedding-3-large" and e["MODEL_DEPLOYMENT"] == "gpt-5-mini"
        assert e["FOUNDRY_PROJECT_ENDPOINT"] == "https://claims-agent-project-res.services.ai.azure.com/api/projects/jashanpreetsingh3999-6322"
        assert not has_foundry_role("me@example.com", sub)          # Owner alone is not the data-plane role
        fake["role assignment list"] = [{"roleDefinitionName": "Foundry User", "roleDefinitionId": ""}]
        assert has_foundry_role("me@example.com", sub)
    finally:
        _run = real
    src = "RETRIEVER=local            # keep this comment\nAZURE_SEARCH_KEY=<admin key>\nEMBEDDING_DEPLOYMENT=text-embedding-3-small   # old name\n"
    merged = merge_env(src, {"AZURE_SEARCH_KEY": "abc", "EMBEDDING_DEPLOYMENT": "text-embedding-3-large", "EMBEDDING_DIMENSIONS": "1536"})
    assert "RETRIEVER=local            # keep this comment" in merged and "AZURE_SEARCH_KEY=abc\n" in merged
    assert "EMBEDDING_DEPLOYMENT=text-embedding-3-large   # old name" in merged and merged.strip().endswith("EMBEDDING_DIMENSIONS=1536")
    assert mask("SEARCHKEY0123456789") == "SEA…789" and "SEARCHKEY0123" not in mask("SEARCHKEY0123456789")
    print("selftest passed: discovery, role detection, .env merge and masking work on fake az output.")
    print("Not covered: the real shapes of `az` output in your subscription. Run `env --dry-run` to check that.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("env", "role", "all", "selftest"):
        sp = sub.add_parser(name)
        sp.add_argument("--rg", default="rg-claims-agent")
        sp.add_argument("--project", help="Foundry project name if the resource group has several")
        sp.add_argument("--embedding", help="embedding deployment name if there are several")
        sp.add_argument("--dry-run", action="store_true")
        sp.add_argument("--yes", action="store_true", help="do not ask before assigning the role")
        if name == "all":
            sp.add_argument("--skip", help="comma list: env,role,check,index,upload,eval,agent,smoke")
            sp.add_argument("--enable-azure", action="store_true", help="set RETRIEVER=azure and AGENT_MODE=foundry in .env at the end")
    args = ap.parse_args()
    try:
        {"env": cmd_env, "role": cmd_role, "all": cmd_all, "selftest": lambda a: selftest()}[args.cmd](args)
    except AzError as e:
        sys.exit(f"\nStopped: {e}")


if __name__ == "__main__":
    main()
