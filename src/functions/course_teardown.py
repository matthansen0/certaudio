"""Remove every trace of a certification: episodes, sources, blobs, search docs.

Kept out of admin.py because a partial delete is worse than none -- each step
reports what it removed so a failure part-way through is visible in the
response rather than silently leaving orphans behind.

The AI Search index is shared across certifications and filtered by
certificationId, so its documents must be deleted individually; dropping the
index would take every other course with it.
"""

import logging
import os

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient

from pipeline.generate_episodes import SHARED_SEARCH_INDEX
from pipeline.upload_to_blob import get_blob_service_client

logger = logging.getLogger(__name__)

_SEARCH_DELETE_BATCH = 500


def _database():
    client = CosmosClient(os.environ["COSMOS_DB_ENDPOINT"], DefaultAzureCredential())
    return client.get_database_client(os.environ.get("COSMOS_DB_DATABASE", "certaudio"))


def _purge_cosmos(certification_id: str, container_name: str) -> int:
    container = _database().get_container_client(container_name)
    items = list(
        container.query_items(
            query="SELECT c.id FROM c WHERE c.certificationId = @cert",
            parameters=[{"name": "@cert", "value": certification_id}],
            enable_cross_partition_query=True,
        )
    )
    for item in items:
        container.delete_item(item["id"], partition_key=certification_id)
    return len(items)


def _purge_blobs(certification_id: str, container_name: str) -> int:
    container = get_blob_service_client().get_container_client(container_name)
    names = [b.name for b in container.list_blobs(name_starts_with=f"{certification_id}/")]
    for name in names:
        container.delete_blob(name)
    return len(names)


def _purge_search(certification_id: str) -> int:
    client = SearchClient(
        endpoint=os.environ["SEARCH_ENDPOINT"],
        index_name=SHARED_SEARCH_INDEX,
        credential=DefaultAzureCredential(),
    )
    ids = [
        doc["id"]
        for doc in client.search(
            search_text="*",
            filter=f"certificationId eq '{certification_id}'",
            select=["id"],
            top=100000,
        )
    ]
    for start in range(0, len(ids), _SEARCH_DELETE_BATCH):
        batch = ids[start:start + _SEARCH_DELETE_BATCH]
        client.delete_documents([{"id": doc_id} for doc_id in batch])
    return len(ids)


def purge_certification(certification_id: str) -> dict:
    """Delete all content for a certification. Returns per-store counts."""
    summary = {}
    for name, fn in (
        ("episodes", lambda: _purge_cosmos(certification_id, "episodes")),
        ("sources", lambda: _purge_cosmos(certification_id, "sources")),
        ("audioBlobs", lambda: _purge_blobs(certification_id, "audio")),
        ("scriptBlobs", lambda: _purge_blobs(certification_id, "scripts")),
        ("searchDocs", lambda: _purge_search(certification_id)),
    ):
        try:
            summary[name] = fn()
        except Exception as exc:
            logger.error("Purge of %s for %s failed: %s", name, certification_id, exc)
            summary[name] = f"failed: {exc}"
    return summary
