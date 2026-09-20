"""Create the clause-level search index (run once). Free tier allows 3 indexes; this is your second.

    python scripts/setup/create_index.py            # creates or updates claims-kb-v2
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration, SearchableField, SearchField, SearchFieldDataType, SearchIndex, SemanticConfiguration,
    SemanticField, SemanticPrioritizedFields, SemanticSearch, SimpleField, VectorSearch, VectorSearchProfile)

from app.config import settings

S = SearchFieldDataType
fields = [
    SimpleField(name="id", type=S.String, key=True),                      # sanitized chunk_key (keys cannot contain ':' or '.')
    SimpleField(name="chunk_key", type=S.String, filterable=True),        # doc_id:chunk_id  (what the agent cites)
    SimpleField(name="chunk_id", type=S.String, filterable=True),         # e.g. B1.1.1-Note-iii  (used for exact clause lookup)
    SimpleField(name="doc_id", type=S.String, filterable=True, facetable=True),
    SimpleField(name="uin", type=S.String, filterable=True, facetable=True),
    SimpleField(name="effective_from", type=S.String, filterable=True),
    SimpleField(name="authority", type=S.String, filterable=True),
    SimpleField(name="doc_type", type=S.String, filterable=True),
    SimpleField(name="chunk_type", type=S.String, filterable=True),
    SearchableField(name="clause", type=S.String, filterable=True),
    SearchableField(name="title", type=S.String),
    SearchableField(name="text", type=S.String),
    SimpleField(name="citation", type=S.String),
    SimpleField(name="page_start", type=S.Int32, filterable=True),
    SearchField(name="embedding", type=S.Collection(S.Single), searchable=True,
                vector_search_dimensions=settings.embedding_dimensions, vector_search_profile_name="vprofile"),   # must equal EMBEDDING_DIMENSIONS
]
index = SearchIndex(
    name=settings.search_index, fields=fields,
    vector_search=VectorSearch(algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
                               profiles=[VectorSearchProfile(name="vprofile", algorithm_configuration_name="hnsw")]),
    semantic_search=SemanticSearch(configurations=[SemanticConfiguration(
        name="default",
        prioritized_fields=SemanticPrioritizedFields(title_field=SemanticField(field_name="title"),
                                                     content_fields=[SemanticField(field_name="text")],
                                                     keywords_fields=[SemanticField(field_name="clause")]))]))

client = SearchIndexClient(settings.search_endpoint, AzureKeyCredential(settings.search_key))
client.create_or_update_index(index)
print(f"Index '{settings.search_index}' is ready on {settings.search_endpoint}")
