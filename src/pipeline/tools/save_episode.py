"""
Save episode metadata to Cosmos DB.
"""

import os
from datetime import datetime, timezone

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from promptflow.core import tool


@tool
def save_episode(
    certification_id: str,
    audio_format: str,
    episode_number: int,
    skill_domain: str,
    skill_topics: list[str],
    audio_url: str,
    script_url: str,
    duration_seconds: float,
    is_amendment: bool,
    amendment_of: int,
    source_urls: list[str],
    content_hash: str,
    title: str = None,
    sync_url: str = None,
) -> dict:
    """
    Save episode metadata to Cosmos DB.

    Args:
        certification_id: Certification ID
        audio_format: 'instructional' or 'podcast'
        episode_number: Sequential episode number
        skill_domain: Skill domain for grouping (without part numbers)
        skill_topics: Topics covered in this episode
        audio_url: URL to audio file
        script_url: URL to script file
        duration_seconds: Audio duration in seconds
        is_amendment: Whether this is an amendment episode
        amendment_of: Original episode number (if amendment)
        source_urls: Source documentation URLs
        content_hash: Hash of source content
        title: Display title (may include part numbers). Defaults to skill_domain.
        sync_url: URL to word-boundary sync JSON for read-along feature

    Returns:
        Saved episode document
    """
    cosmos_endpoint = os.environ.get("COSMOS_DB_ENDPOINT")
    database_name = os.environ.get("COSMOS_DB_DATABASE", "certaudio")

    if not cosmos_endpoint:
        raise ValueError("COSMOS_DB_ENDPOINT environment variable required")

    # Cosmos DB account has disableLocalAuth=true, so we must use Entra ID tokens
    credential = DefaultAzureCredential()
    client = CosmosClient(cosmos_endpoint, credential)
    database = client.get_database_client(database_name)
    container = database.get_container_client("episodes")

    # Create episode document
    episode_id = f"{certification_id}-{audio_format}-{episode_number:03d}"

    # Generate title - use provided title or fall back to skill_domain
    if is_amendment:
        display_title = f"Update: {title or skill_domain}"
    else:
        display_title = title or skill_domain

    episode_doc = {
        "id": episode_id,
        "certificationId": certification_id,
        "format": audio_format,
        "sequenceNumber": episode_number,
        "title": display_title,
        "skillDomain": skill_domain,
        "skillTopics": skill_topics,
        "audioUrl": audio_url,
        "scriptUrl": script_url,
        "syncUrl": sync_url,
        "durationSeconds": duration_seconds,
        "isAmendment": is_amendment,
        "amendmentOf": f"{certification_id}-{audio_format}-{amendment_of:03d}" if is_amendment and amendment_of > 0 else None,
        "sourceUrls": source_urls,
        "contentHash": content_hash,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }

    # Upsert episode
    container.upsert_item(episode_doc)

    # Update source documents with episode reference
    sources_container = database.get_container_client("sources")
    for url in source_urls:
        try:
            # Query for source document
            query = "SELECT * FROM c WHERE c.url = @url AND c.certificationId = @certId"
            params = [
                {"name": "@url", "value": url},
                {"name": "@certId", "value": certification_id},
            ]
            sources = list(
                sources_container.query_items(
                    query=query, parameters=params, enable_cross_partition_query=True
                )
            )

            if sources:
                source_doc = sources[0]
                if episode_id not in source_doc.get("episodeRefs", []):
                    source_doc.setdefault("episodeRefs", []).append(episode_id)
                    sources_container.upsert_item(source_doc)

        except Exception as e:
            print(f"Warning: Could not update source reference for {url}: {e}")

    print(f"Saved episode metadata: {episode_id}")

    return episode_doc
