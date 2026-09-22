# Deploying ARC (one instance, student subscription)

**Nothing in this file has been run** (the commands were written against the `az` CLI 2.60+ documentation, not executed). It is the path I would take, written out so you can follow it click by click when you want to.
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

## 0a. What you need before any command below (prerequisites)

| # | You provide | How to check / get it |
|---|---|---|
| 1 | Azure CLI 2.60+ signed in to the **Azure for Students** subscription | `az version`, then `az login` and `az account show --query name -o tsv` |
| 2 | The subscription selected | `az account set --subscription "Azure for Students"` |
| 3 | Resource providers registered (once per subscription) | `az provider register -n Microsoft.ContainerRegistry`, `-n Microsoft.Web` (App Service), `-n Microsoft.ContainerInstance` (ACI), `-n Microsoft.App` (Container Apps) |
| 4 | A registry name that is globally unique, lowercase letters and digits only | e.g. `arcregistry<yourinitials>` → used as `<acr>` below |
| 5 | An app name that is globally unique (App Service) or a DNS label (ACI) | e.g. `arc-<yourinitials>` → `<app>` below |
| 6 | The non-secret settings from your `.env` | `AZURE_SEARCH_ENDPOINT`, `AZURE_OPENAI_ENDPOINT`, `FOUNDRY_PROJECT_ENDPOINT` (never paste keys into chat or into a shell history you share) |
| 7 | Permission to assign roles | You must be **Owner** or **User Access Administrator** on the resource group, or ask the owner to run the role commands in section 4 |
| 8 | Docker running locally **only if** you build locally | Not needed with `az acr build`, which builds the image inside Azure from this folder |

The Foundry agent always signs in with `DefaultAzureCredential` (`app/agent/runner.py`), so **every** deployed option needs a managed
identity with the **Foundry User** role on the project, whatever `AUTH_MODE` is. `AUTH_MODE` only decides whether Search and the
embeddings use keys (`key`) or that same identity (`entra`).

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

No Docker on your machine (or the daemon is not running)? Build in Azure instead; it reads this folder and the `.dockerignore`:

```bash
az acr build --registry <yourregistry> --image arc:1 .
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


---

## 7. Option B: Azure App Service (Web App for Containers)

Always-on HTTPS URL, one instance, pulls the image from ACR with its own identity. A B1 Linux plan is a fixed monthly cost.

```bash
RG=rg-claims-agent; LOC=centralindia; ACR=<acr>; APP=<app>

# 1. registry + image (built in Azure, no local Docker needed)
az acr create --resource-group $RG --name $ACR --sku Basic --location $LOC
az acr build  --registry $ACR --image arc:1 .

# 2. plan + web app, one worker
az appservice plan create --resource-group $RG --name arc-plan --is-linux --sku B1 --location $LOC --number-of-workers 1
az webapp create --resource-group $RG --plan arc-plan --name $APP \
  --container-image-name $ACR.azurecr.io/arc:1

# 3. the app's own identity: pull from ACR, and (section 4) talk to Foundry / Search / OpenAI
PRINCIPAL=$(az webapp identity assign --resource-group $RG --name $APP --query principalId -o tsv)
az role assignment create --assignee $PRINCIPAL --role AcrPull \
  --scope $(az acr show --name $ACR --query id -o tsv)
az webapp config set --resource-group $RG --name $APP \
  --generic-configurations '{"acrUseManagedIdentityCreds": true}' --always-on true

# 4. settings (the container listens on 8000)
az webapp config appsettings set --resource-group $RG --name $APP --settings \
  WEBSITES_PORT=8000 RETRIEVER=azure AGENT_MODE=foundry AUTH_MODE=entra \
  AZURE_SEARCH_ENDPOINT=<...> AZURE_SEARCH_INDEX=claims-kb-v2 \
  AZURE_OPENAI_ENDPOINT=<...> EMBEDDING_DEPLOYMENT=text-embedding-3-large EMBEDDING_DIMENSIONS=1536 \
  FOUNDRY_PROJECT_ENDPOINT=<...> MODEL_DEPLOYMENT=gpt-5-mini AGENT_NAME=claims-adjudication-agent-v2 DEFAULT_UIN=HDFHLIP25041V062425

# 5. roles for $PRINCIPAL: the three rows of section 4 (Search Index Data Reader, Cognitive Services OpenAI User, Foundry User)

# 6. check
az webapp restart --resource-group $RG --name $APP
curl -s https://$APP.azurewebsites.net/health      # {"status":"ok"}
curl -s https://$APP.azurewebsites.net/ready       # both true once the roles have applied
az webapp log tail --resource-group $RG --name $APP
```

With `AUTH_MODE=key` instead, add `AZURE_SEARCH_KEY` and `AZURE_OPENAI_KEY` as app settings from your own terminal (app settings are
encrypted at rest; better still, store them in Key Vault and use `@Microsoft.KeyVault(SecretUri=...)` references). Do not scale
the plan out: sessions live in memory.

Remove it: `az webapp delete -g $RG -n $APP && az appservice plan delete -g $RG -n arc-plan --yes`.

## 8. Option C: Azure Container Instances (quickest, no HTTPS)

One container with a public IP and a DNS name. Good for a short live demo; it serves plain HTTP on port 8000, so do not use it
for anything but a demo, and delete it afterwards (it bills per second while running).

```bash
RG=rg-claims-agent; LOC=centralindia; ACR=<acr>; DNS=<app>

az acr build --registry $ACR --image arc:1 .                     # skip if the image is already there

# a user-assigned identity, so the roles can be granted BEFORE the container starts
az identity create --resource-group $RG --name arc-id --location $LOC
ID=$(az identity show -g $RG -n arc-id --query id -o tsv)
PRINCIPAL=$(az identity show -g $RG -n arc-id --query principalId -o tsv)
CLIENT=$(az identity show -g $RG -n arc-id --query clientId -o tsv)
az role assignment create --assignee $PRINCIPAL --role AcrPull --scope $(az acr show -n $ACR --query id -o tsv)
# + the three roles of section 4 for $PRINCIPAL; wait a few minutes for them to apply

az container create --resource-group $RG --name arc --location $LOC \
  --image $ACR.azurecr.io/arc:1 --acr-identity $ID --assign-identity $ID \
  --os-type Linux --cpu 1 --memory 1.5 --ports 8000 --dns-name-label $DNS \
  --environment-variables RETRIEVER=azure AGENT_MODE=foundry AUTH_MODE=entra AZURE_CLIENT_ID=$CLIENT \
     AZURE_SEARCH_ENDPOINT=<...> AZURE_SEARCH_INDEX=claims-kb-v2 \
     AZURE_OPENAI_ENDPOINT=<...> EMBEDDING_DEPLOYMENT=text-embedding-3-large EMBEDDING_DIMENSIONS=1536 \
     FOUNDRY_PROJECT_ENDPOINT=<...> MODEL_DEPLOYMENT=gpt-5-mini AGENT_NAME=claims-adjudication-agent-v2 DEFAULT_UIN=HDFHLIP25041V062425

az container show -g $RG -n arc --query ipAddress.fqdn -o tsv   # then open http://<fqdn>:8000/
az container logs -g $RG -n arc
```

`AZURE_CLIENT_ID` tells `DefaultAzureCredential` which user-assigned identity to use. With `AUTH_MODE=key`, pass the two keys with
`--secure-environment-variables AZURE_SEARCH_KEY=... AZURE_OPENAI_KEY=...` (hidden from `az container show`), typed in your own terminal.

Remove it: `az container delete -g $RG -n arc --yes` (and `az identity delete -g $RG -n arc-id` if you are done).

## Which one?

| | Container Apps (section 3) | App Service (7) | ACI (8) |
|---|---|---|---|
| HTTPS URL | yes | yes | no (HTTP, port 8000) |
| Cost when idle | low (min 1 replica) | fixed plan price | per second while running |
| Best for | the recommended path | an always-on demo URL | a one-hour live demo |
