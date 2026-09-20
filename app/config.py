from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    # "local" = BM25 over data/policy_clauses.jsonl (no Azure needed); "azure" = Azure AI Search
    retriever: str = os.getenv("RETRIEVER", "local")
    # "offline" = rule-based stub for development; "foundry" = the Foundry agent
    agent_mode: str = os.getenv("AGENT_MODE", "offline")
    default_uin: str = os.getenv("DEFAULT_UIN", "HDFHLIP25041V062425")
    data_dir: Path = ROOT / "data"

    # Azure AI Search
    search_endpoint: str = os.getenv("AZURE_SEARCH_ENDPOINT", "")
    search_key: str = os.getenv("AZURE_SEARCH_KEY", "")
    search_index: str = os.getenv("AZURE_SEARCH_INDEX", "claims-kb-v2")

    # Azure OpenAI (embeddings)
    openai_endpoint: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    openai_key: str = os.getenv("AZURE_OPENAI_KEY", "")
    openai_api_version: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    embedding_deployment: str = os.getenv("EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
    # text-embedding-3-* can return shorter vectors. 1536 keeps the index small and works for both small and large.
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))

    # Foundry agent
    project_endpoint: str = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
    model_deployment: str = os.getenv("MODEL_DEPLOYMENT", "gpt-5-mini")
    agent_name: str = os.getenv("AGENT_NAME", "claims-adjudication-agent-v2")
    max_agent_steps: int = int(os.getenv("MAX_AGENT_STEPS", "8"))

    # Timeouts, retries and limits. Every Azure call has a timeout; a whole chat turn has a deadline.
    turn_deadline_s: float = float(os.getenv("TURN_DEADLINE_S", "60"))     # the assistant answers or says "try again" within this
    model_timeout_s: float = float(os.getenv("MODEL_TIMEOUT_S", "40"))     # one model call (capped by what is left of the deadline)
    search_timeout_s: float = float(os.getenv("SEARCH_TIMEOUT_S", "10"))
    embed_timeout_s: float = float(os.getenv("EMBED_TIMEOUT_S", "10"))
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))                  # retries after the first try, for 429 and transient 5xx only
    backoff_base_s: float = float(os.getenv("BACKOFF_BASE_S", "0.5"))
    backoff_cap_s: float = float(os.getenv("BACKOFF_CAP_S", "8"))

    # API
    max_request_bytes: int = int(os.getenv("MAX_REQUEST_BYTES", "262144"))
    cors_origins: tuple = tuple(o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip())   # empty = no cross-origin access
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
