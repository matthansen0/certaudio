"""
Check for content changes (delta) between current Microsoft Learn content
and previously indexed content.
"""

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from bs4 import BeautifulSoup


@dataclass
class ContentDelta:
    """Represents a change detected in source content."""

    url: str
    old_hash: str
    new_hash: str
    affected_episodes: list[str]


@dataclass
class DeltaCheckResult:
    """Results from delta checking."""

    has_updates: bool
    changed_sources: list[ContentDelta]
    unchanged_count: int
    error_count: int


def compute_content_hash(html: str) -> str:
    """
    Compute a hash of the main content, ignoring navigation, ads, etc.
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove elements that change frequently but aren't content
    for element in soup.find_all(["nav", "footer", "aside", "script", "style"]):
        element.decompose()

    # Find the main content area
    main_content = soup.find("main") or soup.find("article") or soup.find("div", class_="content")

    if main_content:
        text = main_content.get_text(separator=" ", strip=True)
    else:
        text = soup.get_text(separator=" ", strip=True)

    # Normalize whitespace
    text = " ".join(text.split())

    return hashlib.sha256(text.encode()).hexdigest()


def fetch_page_content(url: str) -> str:
    """Fetch HTML content from a URL."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.text


def check_content_delta(
    certification_id: str,
    cosmos_endpoint: str,
    force_refresh: bool = False,
    database_name: str = "certaudio",
) -> DeltaCheckResult:
    """
    Check for content changes against stored hashes.

    Args:
        certification_id: Microsoft certification ID
        cosmos_endpoint: Cosmos DB endpoint
        force_refresh: If True, mark all content as changed
        database_name: Cosmos DB database name

    Returns:
        DeltaCheckResult with change information
    """
    credential = DefaultAzureCredential()
    client = CosmosClient(cosmos_endpoint, credential)
    database = client.get_database_client(database_name)
    sources_container = database.get_container_client("sources")

    # Query all sources for this certification
    query = "SELECT * FROM c WHERE c.certificationId = @certId"
    parameters = [{"name": "@certId", "value": certification_id}]

    sources = list(
        sources_container.query_items(query=query, parameters=parameters, enable_cross_partition_query=True)
    )

    changed_sources = []
    unchanged_count = 0
    error_count = 0

    for source in sources:
        url = source["url"]
        old_hash = source.get("contentHash", "")
        episode_refs = source.get("episodeRefs", [])

        if force_refresh:
            # Mark everything as changed
            changed_sources.append(
                ContentDelta(
                    url=url,
                    old_hash=old_hash,
                    new_hash="forced_refresh",
                    affected_episodes=episode_refs,
                )
            )
            continue

        try:
            # Fetch current content and compute hash
            html = fetch_page_content(url)
            new_hash = compute_content_hash(html)

            if new_hash != old_hash:
                changed_sources.append(
                    ContentDelta(
                        url=url,
                        old_hash=old_hash,
                        new_hash=new_hash,
                        affected_episodes=episode_refs,
                    )
                )

                # Update the stored hash
                source["contentHash"] = new_hash
                source["lastChecked"] = datetime.now(timezone.utc).isoformat()
                sources_container.upsert_item(source)

                print(f"Changed: {url}")
            else:
                unchanged_count += 1

                # Update last checked timestamp
                source["lastChecked"] = datetime.now(timezone.utc).isoformat()
                sources_container.upsert_item(source)

        except Exception as e:
            print(f"Error checking {url}: {e}")
            error_count += 1

    return DeltaCheckResult(
        has_updates=len(changed_sources) > 0,
        changed_sources=changed_sources,
        unchanged_count=unchanged_count,
        error_count=error_count,
    )


def get_affected_episodes(changed_sources: list[ContentDelta]) -> list[str]:
    """Get deduplicated list of episode IDs affected by changes."""
    affected = set()
    for delta in changed_sources:
        affected.update(delta.affected_episodes)
    return list(affected)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Check for content updates")
    parser.add_argument(
        "--certification-id",
        required=True,
        help="Microsoft certification ID (e.g., ai-102)",
    )
    parser.add_argument(
        "--force-refresh",
        type=lambda x: x.lower() == "true",
        default=False,
        help="Force refresh all content",
    )
    parser.add_argument(
        "--output-file",
        default="delta_results.json",
        help="Output JSON file path",
    )

    args = parser.parse_args()

    # Get endpoints from environment
    cosmos_endpoint = os.environ.get("COSMOS_DB_ENDPOINT")
    if not cosmos_endpoint:
        raise ValueError("COSMOS_DB_ENDPOINT environment variable required")

    # Run delta check
    result = check_content_delta(
        certification_id=args.certification_id,
        cosmos_endpoint=cosmos_endpoint,
        force_refresh=args.force_refresh,
    )

    # Get affected episodes
    affected_episodes = get_affected_episodes(result.changed_sources)

    # Output results
    output = {
        "hasUpdates": result.has_updates,
        "changedSources": [
            {"url": d.url, "oldHash": d.old_hash, "newHash": d.new_hash}
            for d in result.changed_sources
        ],
        "affectedEpisodes": affected_episodes,
        "unchangedCount": result.unchanged_count,
        "errorCount": result.error_count,
    }

    with open(args.output_file, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Delta check complete:")
    print(f"  - Changed sources: {len(result.changed_sources)}")
    print(f"  - Unchanged sources: {result.unchanged_count}")
    print(f"  - Errors: {result.error_count}")
    print(f"  - Affected episodes: {len(affected_episodes)}")


if __name__ == "__main__":
    main()
