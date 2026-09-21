"""Azure AI Search retriever.

Not exercised in the offline test-suite (needs your Azure resources). It follows the documented
azure-search-documents 11.x API: hybrid query (BM25 text + vector) re-ranked by the semantic ranker.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

from ..config import settings
from ..resilience import call_with_retry
from .base import Chunk, LocalRetriever, Retriever, chunk_from_record

SELECT = ["chunk_key", "chunk_id", "doc_id", "uin", "clause", "title", "citation", "page_start", "text"]


def _q(v: str) -> str:
    return v.replace("'", "''")


def embed(texts: list[str], timeout: float | None = None) -> list[list[float]]:
    from openai import AzureOpenAI

    # max_retries=0: the SDK default (2 silent retries, 10 minute timeout) is replaced by call_with_retry, which knows the turn deadline
    from ..auth import openai_client_args
    client = AzureOpenAI(azure_endpoint=settings.openai_endpoint, api_version=settings.openai_api_version, max_retries=0, **openai_client_args())
    out = call_with_retry(
        lambda t: client.embeddings.create(model=settings.embedding_deployment, input=texts, dimensions=settings.embedding_dimensions, timeout=t),
        label="embeddings", timeout=timeout or settings.embed_timeout_s)
    return [d.embedding for d in out.data]


class AzureSearchRetriever(Retriever):
    def __init__(self):
        from azure.search.documents import SearchClient

        from ..auth import search_credential

        # retry_total=0 turns off azure-core's own retries (default 10); call_with_retry does them within the turn deadline
        self.client = SearchClient(settings.search_endpoint, settings.search_index, search_credential(), retry_total=0)
        self._cache: OrderedDict = OrderedDict()   # (chunk_id, uin) -> Chunk; the same clauses are fetched by a tool and again by the renderer, 11 to 25 requests per answer before this
        self._lock = threading.Lock()

    def _run(self, **query) -> list:
        """One search, fully read inside the timeout (results are paged lazily), retried on 429 / transient errors."""
        return call_with_retry(lambda t: list(self.client.search(read_timeout=t, connection_timeout=min(t, 5), **query)),
                               label="search", timeout=settings.search_timeout_s)

    def _filter(self, uin: str | None) -> str | None:
        return f"uin eq '{_q(uin)}'" if uin else None

    def search(self, query: str, uin: str | None = None, top_k: int = 5) -> list[Chunk]:
        from azure.search.documents.models import VectorizedQuery

        vec = embed([query])[0]
        results = self._run(
            search_text=query,
            vector_queries=[VectorizedQuery(vector=vec, k_nearest_neighbors=40, fields="embedding")],
            filter=self._filter(uin), select=SELECT, top=top_k,
            query_type="semantic", semantic_configuration_name="default")
        out = []
        for r in results:
            score = r.get("@search.reranker_score") or r.get("@search.score") or 0.0
            out.append(chunk_from_record(r, float(score)))
        return out

    def _one(self, flt: str) -> Chunk | None:
        for r in self._run(search_text="*", filter=flt, select=SELECT, top=1):
            return chunk_from_record(r)
        return None

    def get_by_chunk_id(self, chunk_id: str, uin: str | None = None) -> Chunk | None:
        size, key = settings.chunk_cache_size, (chunk_id, uin)
        if size > 0:
            with self._lock:
                if key in self._cache:
                    self._cache.move_to_end(key)
                    return self._cache[key]
        flt = f"chunk_id eq '{_q(chunk_id)}'" + (f" and uin eq '{_q(uin)}'" if uin else "")
        chunk = self._one(flt)
        if chunk is not None and size > 0:   # a miss is not cached: a clause that is not there yet may be uploaded later
            with self._lock:
                self._cache[key] = chunk
                while len(self._cache) > size:
                    self._cache.popitem(last=False)
        return chunk

    def get_by_key(self, chunk_key: str) -> Chunk | None:
        size, key = settings.chunk_cache_size, ("key", chunk_key)
        if size > 0:
            with self._lock:
                if key in self._cache:
                    self._cache.move_to_end(key)
                    return self._cache[key]
        chunk = self._one(f"chunk_key eq '{_q(chunk_key)}'")
        if chunk is not None and size > 0:
            with self._lock:
                self._cache[key] = chunk
                while len(self._cache) > size:
                    self._cache.popitem(last=False)
        return chunk


_instance: Retriever | None = None


def get_retriever() -> Retriever:
    global _instance
    if _instance is None:
        _instance = AzureSearchRetriever() if settings.retriever == "azure" else \
            LocalRetriever(settings.data_dir / "policy_clauses.jsonl")
    return _instance
