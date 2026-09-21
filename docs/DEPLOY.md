# Deploying ARC (one instance, student subscription)

**Nothing in this file has been run.** It is the path I would take, written out so you can follow it click by click when you want to.
Everything here costs money on a student subscription, so read section 0 first.

ARC is a single-instance app on purpose: sessions live in memory (`app/main.py: SESSIONS`) and the rate limit is per process. Two
instances, or two workers, would lose half the visits and double the allowed request rate. Before you run more than one instance you
need a session store (Redis) and a real limiter in front.

## 0. Before you deploy anything

| Question | Answer for this project |
|---|---|
| Does it need to be on the internet? | For a demo, no. `scripts/run_demo.sh` on your laptop is the safest demo there is. |
| Is there a login? | **No.** Anyone with the URL can upload documents and spend your model quota. Do not put it on a public URL without adding authentication first (section 5). |
| What does a turn cost? | Two model calls minimum (a conversation and a response), three or four with a tool or a rewrite, plus up to eight Search queries. The turn log records the token counts. |
| Where do the keys live? | Only in the container's environment, never in the image (`.dockerignore` excludes `.env`). Prefer `AUTH_MODE=entra` and a managed identity, so there is no key to leak. |
| Which regions? | The subscription policy allows Korea Central, Central India, East Asia, Malaysia West and UAE North. The Foundry project is in Korea Central and the Search service in Central India; put the app in one of those two to keep the hops short. |

## 1. Build the image

```bash
docker build -t arc:latest .
docker run --rm -p 8000:8000 --env-file .env arc:latest      # check http://127.0.0.1:8000/ first
```

The `Dockerfile` runs one uvicorn worker as a non-root user, exposes 8000, and has a health check on `/health`.
`/health` answers `{"status":"ok"}` without touching Azure; `/ready` says whether Search and the agent can actually be reached.

## 2. Push it to a registry

```bash
az acr create  --resource-group rg-claims-agent --name <yourregistry> --sku Basic
az acr login   --name <yourregistry>
docker tag arc:latest <yourregistry>.azurecr.io/arc:1
docker push <yourregistry>.azurecr.io/arc:1
```

Basic ACR is a small monthly cost. If you would rather not pay it, skip the registry and deploy with
`az containerapp up --source .`, which builds in Azure and stores the image for you.

## 3. Run it as one container app

```bash
az extension add --name containerapp --upgrade
az containerapp env create --name arc-env --resource-group rg-claims-agent --location centralindia
az containerapp create \
  --name arc --resource-group rg-claims-agent --environment arc-env \
  --image <yourregistry>.azurecr.io/arc:1 \
  --target-port 8000 --ingress external \
  --min-replicas 1 --max-replicas 1 \
  --system-assigned \
  --env-vars RETRIEVER=azure AGENT_MODE=foundry AUTH_MODE=entra \
             AZURE_SEARCH_ENDPOINT=<...> AZURE_SEARCH_INDEX=claims-kb-v2 \
             AZURE_OPENAI_ENDPOINT=<...> EMBEDDING_DEPLOYMENT=text-embedding-3-large EMBEDDING_DIMENSIONS=1536 \
             FOUNDRY_PROJECT_ENDPOINT=<...> MODEL_DEPLOYMENT=gpt-5-mini AGENT_NAME=claims-adjudication-agent-v2
```

`--min-replicas 1 --max-replicas 1` is the whole point: one instance, no scaling, no lost sessions. Leave `DEBUG` and
`DEBUG_TRACE` unset: the developer routes and the tool trace are then off.

With `AUTH_MODE=key` instead, pass the two keys as secrets and never as plain env vars:

```bash
az containerapp secret set --name arc --resource-group rg-claims-agent \
  --secrets search-key=<value> openai-key=<value>
az containerapp update --name arc --resource-group rg-claims-agent \
  --set-env-vars AZURE_SEARCH_KEY=secretref:search-key AZURE_OPENAI_KEY=secretref:openai-key
```

## 4. Give the instance its own identity (keyless)

The container app's system-assigned identity needs two data-plane roles. **Assign them yourself; nothing in this repository
does it for you** (`docs/SECURITY.md` has the same table and the reason):

| Resource | Role | Why |
|---|---|---|
| Search service `claims-search-37` (or scoped to index `claims-kb-v2`) | **Search Index Data Reader** | run queries |
| Foundry / OpenAI resource `claims-agent-project-res` | **Cognitive Services OpenAI User** | embeddings |
| Foundry project `jashanpreetsingh3999-6322` | **Foundry User** (as your own account already has) | run the agent |

Also set the search service's API access control to **Role-based access control** or **Both**
(portal: search service → Settings → Keys). A new assignment can take a while to apply; until then `/ready` says `degraded`.

## 5. Before the URL is public

1. **Put authentication in front.** Container Apps can do this without code: `az containerapp auth microsoft update ...`,
   or front the app with Azure Front Door / API Management. Until then the app is open to anyone who finds the URL.
2. Keep the rate limit meaningful: it is per process, so with one instance it is the real limit. Behind a proxy, set
   `--forwarded-allow-ips` (the Dockerfile does) or every request looks like it comes from one IP.
3. Set `CORS_ORIGINS` only if another origin must call the API. Empty (the default) means no cross-origin access at all.
4. Watch the cost: the Free Search tier has hard query limits, and each chat turn spends model quota. If you see 429s,
   raise the model deployment's tokens-per-minute in the Foundry portal (Deployments → `gpt-5-mini` → Edit) rather than
   retrying harder.
5. Sessions are in memory: a restart ends every visit. That is acceptable for a demo and not for anything else.

## 6. When you are done

```bash
az containerapp delete --name arc --resource-group rg-claims-agent
az containerapp env delete --name arc-env --resource-group rg-claims-agent
az acr delete --name <yourregistry> --resource-group rg-claims-agent
```

The Search service, the index, the Foundry project and the agent are **not** part of this and must not be deleted: the
demo needs them.
