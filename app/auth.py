"""How the runtime authenticates to Azure AI Search and to the embeddings endpoint.

AUTH_MODE=key    (default) the API keys from .env, as before.
AUTH_MODE=entra  Microsoft Entra ID tokens from DefaultAzureCredential (az login locally, a managed identity when deployed): the runtime needs NO keys.
                 The signed-in identity needs data-plane roles, which YOU assign (see docs/SECURITY.md): "Search Index Data Reader" on the search service and
                 "Cognitive Services OpenAI User" on the Foundry/OpenAI resource. The Search service must allow role-based access control (Keys > API Access control).
Setup scripts (create_index, upload_chunks) still use the admin key: writing an index is not a runtime job.
"""
from __future__ import annotations

from .config import settings

OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"


def search_credential():
    if settings.auth_mode == "entra":
        from azure.identity import DefaultAzureCredential
        return DefaultAzureCredential()
    from azure.core.credentials import AzureKeyCredential
    return AzureKeyCredential(settings.search_key)


def openai_client_args() -> dict:
    """The auth part of the AzureOpenAI(...) arguments."""
    if settings.auth_mode == "entra":
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        return dict(azure_ad_token_provider=get_bearer_token_provider(DefaultAzureCredential(), OPENAI_SCOPE))
    return dict(api_key=settings.openai_key)
