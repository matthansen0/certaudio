"""Per-source tracking that answers "which episodes are out of date?".

Two hashes with deliberately different meanings:

  indexedHash  what the Search index currently holds for this URL
  contentHash  what the audio was actually generated from

Staleness compares the live page against ``contentHash`` only, so re-indexing
as often as you like never claims episodes are fresh when they are not, and a
generation that fails leaves the source still marked stale instead of silently
absorbing the change.
"""

import os
from datetime import datetime, timezone
from typing import Optional

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

_client = None
_credential = None


def _get_credential():
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


def _sources(cosmos_endpoint: Optional[str] = None, database_name: Optional[str] = None):
    global _client
    if _client is None:
        endpoint = cosmos_endpoint or os.environ["COSMOS_DB_ENDPOINT"]
        _client = CosmosClient(endpoint, _get_credential())
    database = _client.get_database_client(
        database_name or os.environ.get("COSMOS_DB_DATABASE", "certaudio")
    )
    return database.get_container_client("sources")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_id(certification_id: str, url: str) -> str:
    """Deterministic id so re-indexing updates rather than duplicates."""
    import hashlib

    return f"{certification_id}-{hashlib.sha256(url.encode()).hexdigest()[:16]}"


def list_sources(certification_id: str, **kwargs) -> list[dict]:
    query = "SELECT * FROM c WHERE c.certificationId = @certId"
    return list(
        _sources(**kwargs).query_items(
            query=query,
            parameters=[{"name": "@certId", "value": certification_id}],
            enable_cross_partition_query=True,
        )
    )


def record_indexed(certification_id: str, url: str, indexed_hash: str, **kwargs) -> None:
    """Note what the Search index now holds. Never moves the generation baseline."""
    container = _sources(**kwargs)
    doc_id = source_id(certification_id, url)
    try:
        doc = container.read_item(doc_id, partition_key=doc_id)
    except Exception:
        doc = {
            "id": doc_id,
            "certificationId": certification_id,
            "url": url,
            "contentHash": "",
            "episodeRefs": [],
        }
    doc["indexedHash"] = indexed_hash
    doc["lastIndexedAt"] = _now()
    container.upsert_item(doc)


def record_generated(
    certification_id: str, url: str, episode_id: str, content_hash: str, **kwargs
) -> None:
    """Move the generation baseline. Called only once an episode has saved."""
    container = _sources(**kwargs)
    doc_id = source_id(certification_id, url)
    try:
        doc = container.read_item(doc_id, partition_key=doc_id)
    except Exception:
        doc = {
            "id": doc_id,
            "certificationId": certification_id,
            "url": url,
            "indexedHash": "",
            "episodeRefs": [],
        }
    doc["contentHash"] = content_hash
    doc["lastGeneratedAt"] = _now()
    refs = doc.setdefault("episodeRefs", [])
    if episode_id not in refs:
        refs.append(episode_id)
    container.upsert_item(doc)
