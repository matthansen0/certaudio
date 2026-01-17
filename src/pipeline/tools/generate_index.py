"""
Generate episode index JSON and upload to blob storage.
"""

import argparse
import json
import os
from datetime import datetime, timezone

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings


def generate_index(
    certification_id: str,
    audio_format: str,
    cosmos_endpoint: str,
    storage_account_name: str,
    database_name: str = "certaudio",
    min_episodes: int = 1,
) -> dict:
    """
    Generate episode index from Cosmos DB and upload to blob storage.
    
    Args:
        certification_id: Certification ID
        audio_format: Audio format
        cosmos_endpoint: Cosmos DB endpoint
        storage_account_name: Storage account name
        database_name: Cosmos DB database name
    
    Returns:
        Index data
    """
    credential = DefaultAzureCredential()
    
    # Get episodes from Cosmos DB
    cosmos_client = CosmosClient(cosmos_endpoint, credential)
    database = cosmos_client.get_database_client(database_name)
    container = database.get_container_client("episodes")
    
    query = """
        SELECT * FROM c 
        WHERE c.certificationId = @certId AND c.format = @format
        ORDER BY c.sequenceNumber
    """
    params = [
        {"name": "@certId", "value": certification_id},
        {"name": "@format", "value": audio_format},
    ]
    
    episodes = list(
        container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )

    # Validation gate: require a complete base set of episodes when min_episodes is provided.
    # This avoids "success" when only a subset was generated (or when gaps exist due to
    # partial failures/overwrites).
    base_episodes = [ep for ep in episodes if not ep.get("isAmendment", False)]
    base_sequence_numbers = {int(ep.get("sequenceNumber", 0)) for ep in base_episodes}

    if min_episodes and len(base_episodes) < min_episodes:
        raise RuntimeError(
            f"Refusing to publish index: found {len(base_episodes)} base episode(s) for {certification_id}/{audio_format} "
            f"(min required: {min_episodes})."
        )

    if min_episodes:
        missing = [n for n in range(1, min_episodes + 1) if n not in base_sequence_numbers]
        if missing:
            raise RuntimeError(
                f"Refusing to publish index: missing base episode sequenceNumber(s) {missing} for {certification_id}/{audio_format}."
            )
    
    # Group by skill domain
    domains = {}
    total_duration = 0
    
    for ep in episodes:
        domain = ep.get("skillDomain", "Other")
        if domain not in domains:
            domains[domain] = []
        
        domains[domain].append({
            "id": ep["id"],
            "sequenceNumber": ep["sequenceNumber"],
            "title": ep["title"],
            "durationSeconds": ep.get("durationSeconds", 0),
            "isAmendment": ep.get("isAmendment", False),
            "amendmentOf": ep.get("amendmentOf"),
            "skillTopics": ep.get("skillTopics", []),
            "createdAt": ep.get("createdAt"),
        })
        
        total_duration += ep.get("durationSeconds", 0)
    
    # Create index
    index_data = {
        "certificationId": certification_id,
        "format": audio_format,
        "totalEpisodes": len(episodes),
        "totalDurationSeconds": total_duration,
        "totalDurationMinutes": round(total_duration / 60, 1),
        "domains": domains,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }


    
    # Upload to blob storage
    blob_service = BlobServiceClient(
        account_url=f"https://{storage_account_name}.blob.core.windows.net",
        credential=credential,
    )
    
    container_name = f"{certification_id}-{audio_format}"
    container_client = blob_service.get_container_client(container_name)
    
    # Ensure container exists
    try:
        container_client.create_container()
    except:
        pass
    
    # Upload index
    blob_client = container_client.get_blob_client("metadata/index.json")
    blob_client.upload_blob(
        json.dumps(index_data, indent=2),
        overwrite=True,
        content_settings=ContentSettings(content_type="application/json"),
    )
    
    print(f"Index generated: {len(episodes)} episodes, {index_data['totalDurationMinutes']} minutes total")
    
    return index_data


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate episode index")
    parser.add_argument("--certification-id", required=True)
    parser.add_argument("--audio-format", required=True)
    parser.add_argument(
        "--min-episodes",
        type=int,
        default=1,
        help="Fail if fewer than this many episodes exist (default: 1)",
    )
    
    args = parser.parse_args()
    
    cosmos_endpoint = os.environ.get("COSMOS_DB_ENDPOINT")
    storage_account = os.environ.get("STORAGE_ACCOUNT_NAME")
    
    if not cosmos_endpoint or not storage_account:
        raise ValueError("COSMOS_DB_ENDPOINT and STORAGE_ACCOUNT_NAME required")
    
    generate_index(
        certification_id=args.certification_id,
        audio_format=args.audio_format,
        cosmos_endpoint=cosmos_endpoint,
        storage_account_name=storage_account,
        min_episodes=args.min_episodes,
    )


if __name__ == "__main__":
    main()
