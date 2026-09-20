"""Azure AI Search retriever.

Not exercised in the offline test-suite (needs your Azure resources). It follows the documented
azure-search-documents 11.x API: hybrid query (BM25 text + vector) re-ranked by the semantic ranker.
"""
from __future__ import annotations

from ..config import settings
from .base import Chunk, LocalRetriever, Retriever, chunk_from_record

SELECT = ["chunk_key", "chunk_id", "doc_id", "uin", "clause", "title", "citation", "page_start", "text"]


def _q(v: str) -> str:
    return v.replace("'", "''")


def embed(texts: list[str]) -> list[list[float]]:
    from openai import AzureOpenAI

    client = AzureOpenAI(azure_endpoint=settings.openai_endpoint, api_key=settings.openai_key,
                         api_version=settings.openai_api_version)
    out = client.embeddings.create(model=settings.embedding_deployment, input=texts, dimensions=settings.embedding_dimensions)
    return [d.embedding for d in out.data]


class AzureSearchRetriever(Retriever):
    def __init__(self):
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient

        self.client = SearchClient(settings.search_endpoint, settings.search_index,
                                   AzureKeyCredential(settings.search_key))

    def _filter(self, uin: str | None) -> str | None:
        return f"uin eq '{_q(uin)}'" if uin else None

    def search(self, query: str, uin: str | None = None, top_k: int = 5) -> list[Chunk]:
        from azure.search.documents.models import VectorizedQuery

        vec = embed([query])[0]
        results = self.client.search(
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
        for r in self.client.search(search_text="*", filter=flt, select=SELECT, top=1):
            return chunk_from_record(r)
        return None

    def get_by_chunk_id(self, chunk_id: str, uin: str | None = None) -> Chunk | None:
        flt = f"chunk_id eq '{_q(chunk_id)}'" + (f" and uin eq '{_q(uin)}'" if uin else "")
        return self._one(flt)

    def get_by_key(self, chunk_key: str) -> Chunk | None:
        return self._one(f"chunk_key eq '{_q(chunk_key)}'")


_instance: Retriever | None = None


def get_retriever() -> Retriever:
    global _instance
    if _instance is None:
        _instance = AzureSearchRetriever() if settings.retriever == "azure" else \
            LocalRetriever(settings.data_dir / "policy_clauses.jsonl")
    return _instance
