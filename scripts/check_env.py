"""Check .env before touching Azure.

    python scripts/check_env.py            # format checks, then live checks (Search, embeddings, Foundry)
    python scripts/check_env.py --offline  # format checks only

Never prints secret values.
"""
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings as s

problems = []


def bad_secret(v):
    return (not v) or "<" in v or ">" in v or " " in v or len(v) < 20


def check(ok, label, fix):
    print(("  ok    " if ok else "  FIX   ") + label + ("" if ok else f"\n          -> {fix}"))
    if not ok:
        problems.append(label)


print("Format checks")
check(re.fullmatch(r"https://[\w-]+\.search\.windows\.net/?", s.search_endpoint or ""), "AZURE_SEARCH_ENDPOINT",
      "Copy the Url from the search service Overview page (https://<name>.search.windows.net).")
check(not bad_secret(s.search_key), "AZURE_SEARCH_KEY", "Paste the Primary admin key (Settings > Keys). No < > brackets, no spaces.")
check(re.fullmatch(r"https://[\w-]+\.(openai|cognitiveservices)\.azure\.com/?", s.openai_endpoint or ""), "AZURE_OPENAI_ENDPOINT",
      "Copy the Endpoint from the OpenAI resource's Keys and Endpoint page.")
check(not bad_secret(s.openai_key), "AZURE_OPENAI_KEY", "Paste KEY 1 from Keys and Endpoint. No < > brackets, no spaces.")
check(bool(s.embedding_deployment) and "<" not in s.embedding_deployment, "EMBEDDING_DEPLOYMENT", "Use the deployment NAME shown in Foundry portal > Build > Deployments.")
check(bool(s.model_deployment) and "<" not in s.model_deployment, "MODEL_DEPLOYMENT", "Use the deployment NAME shown in Foundry portal > Build > Deployments.")
check(re.fullmatch(r"https://[\w-]+\.(services\.ai\.azure|ai\.azure)\.com/api/projects/[\w-]+/?", s.project_endpoint or ""), "FOUNDRY_PROJECT_ENDPOINT",
      "Copy the project endpoint from the project's welcome/Overview page in the Foundry portal. Replace <resource> and <project-name>.")

if "--offline" in sys.argv or problems:
    print("\n" + ("Fix the lines above, then run this again." if problems else "Format looks fine."))
    sys.exit(1 if problems else 0)

print("\nLive checks")
try:
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents.indexes import SearchIndexClient
    names = list(SearchIndexClient(s.search_endpoint, AzureKeyCredential(s.search_key)).list_index_names())
    print(f"  ok    Search reachable. Indexes: {names}")
    if s.search_index in names:
        print(f"        note: '{s.search_index}' already exists (create_index.py will update it)")
except Exception as e:
    print(f"  FIX   Search: {type(e).__name__}: {str(e)[:200]}"); problems.append("search")
try:
    from app.retrieval.azure_search import embed
    v = embed(["waiting period"])[0]
    print(f"  ok    Embeddings work (vector length {len(v)}; EMBEDDING_DIMENSIONS={s.embedding_dimensions})")
    if len(v) != s.embedding_dimensions:
        print("  FIX   Vector length differs from EMBEDDING_DIMENSIONS. Use a text-embedding-3 model, or set EMBEDDING_DIMENSIONS to the length above and re-run create_index.py."); problems.append("dims")
except Exception as e:
    print(f"  FIX   Embeddings: {type(e).__name__}: {str(e)[:200]}\n          -> check AZURE_OPENAI_ENDPOINT/KEY and that EMBEDDING_DEPLOYMENT is the deployment name in THAT resource."); problems.append("embeddings")
try:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential
    proj = AIProjectClient(endpoint=s.project_endpoint, credential=DefaultAzureCredential())
    n = len(list(proj.agents.list()))
    print(f"  ok    Foundry project reachable ({n} agents visible)")
except Exception as e:
    print(f"  WARN  Foundry: {type(e).__name__}: {str(e)[:200]}\n          -> run `az login` with the account that owns the project and confirm the Foundry User role; wait ~5 minutes after assigning it.")
print("\n" + ("All good." if not problems else "Fix the items marked FIX."))
sys.exit(1 if problems else 0)
