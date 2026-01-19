"""
Generate all episodes for a certification in a single run.

This is the entry point for Azure Container Instance jobs, which don't have
the 6-hour timeout limitation of GitHub Actions. It runs the full pipeline:

1. Discover content (learning paths + exam skills)
2. Deploy AI Search index (if needed)
3. Index content
4. Generate all episodes (with parallel TTS)
5. Cleanup Search index

Usage:
    python -m tools.generate_all --certification-id dp-700
"""

import argparse
import json
import os
import subprocess
import sys
import time

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from jinja2 import Environment, FileSystemLoader
from openai import AzureOpenAI

from .discover_exam_content import discover_exam_content
from .index_content import index_content, create_search_index
from .generate_episodes import (
    TTS_MAX_WORKERS,
    prepare_episode,
    synthesize_episode_audio,
    finalize_episode,
    episode_exists,
)


def main():
    parser = argparse.ArgumentParser(description="Generate all certification audio content")
    parser.add_argument("--certification-id", required=True, help="Certification ID (e.g., dp-700)")
    parser.add_argument("--audio-format", default="instructional", 
                       choices=["instructional", "podcast-solo", "podcast-duo"])
    parser.add_argument("--discovery-mode", default="comprehensive",
                       choices=["quick", "deep", "comprehensive"])
    parser.add_argument("--instructional-voice", default="en-US-AndrewNeural")
    parser.add_argument("--podcast-host-voice", default="en-US-GuyNeural")
    parser.add_argument("--podcast-expert-voice", default="en-US-TonyNeural")
    parser.add_argument("--force-regenerate", action="store_true",
                       help="Regenerate episodes even if they exist")
    parser.add_argument("--skip-indexing", action="store_true",
                       help="Skip content indexing (use existing index)")
    args = parser.parse_args()

    print("=" * 60)
    print("CERTAUDIO - Full Generation Pipeline")
    print("=" * 60)
    print(f"Certification:   {args.certification_id}")
    print(f"Audio Format:    {args.audio_format}")
    print(f"Discovery Mode:  {args.discovery_mode}")
    print(f"TTS Workers:     {TTS_MAX_WORKERS}")
    print("=" * 60)

    # Get environment variables
    resource_group = os.environ.get("AZURE_RESOURCE_GROUP", "rg-certaudio-dev")
    location = os.environ.get("AZURE_LOCATION", "centralus")
    search_endpoint = os.environ.get("SEARCH_ENDPOINT", "")
    
    # Initialize credential
    credential = DefaultAzureCredential()
    
    openai_client = AzureOpenAI(
        azure_endpoint=os.environ["OPENAI_ENDPOINT"],
        azure_ad_token_provider=lambda: credential.get_token(
            "https://cognitiveservices.azure.com/.default"
        ).token,
        api_version="2024-02-01",
    )
    
    cosmos_client = CosmosClient(
        url=os.environ["COSMOS_DB_ENDPOINT"],
        credential=credential,
    )
    
    jinja_env = Environment(loader=FileSystemLoader("prompts"))

    # Phase 1: Discover content
    print("\n" + "=" * 60)
    print("PHASE 1: Content Discovery")
    print("=" * 60)
    
    skills_outline = discover_exam_content(
        certification_id=args.certification_id,
        mode=args.discovery_mode,
        openai_client=openai_client,
    )
    
    # Collect all source URLs
    all_source_urls = []
    for domain in skills_outline.get("domains", []):
        all_source_urls.extend(domain.get("sourceUrls", []))
    all_source_urls = list(set(all_source_urls))
    
    print(f"Discovered {len(skills_outline.get('domains', []))} domains")
    print(f"Collected {len(all_source_urls)} unique source URLs")

    # Phase 2: Deploy ephemeral AI Search (if needed)
    search_name = None
    if not search_endpoint:
        print("\n" + "=" * 60)
        print("PHASE 2a: Deploying ephemeral AI Search")
        print("=" * 60)
        
        search_name = f"search-{args.certification_id}-{int(time.time())}"
        print(f"Creating search service: {search_name}")
        
        result = subprocess.run([
            "az", "search", "service", "create",
            "--name", search_name,
            "--resource-group", resource_group,
            "--location", location,
            "--sku", "basic",
            "--partition-count", "1",
            "--replica-count", "1",
            "-o", "none"
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"ERROR: Failed to create search service: {result.stderr}")
            sys.exit(1)
        
        search_endpoint = f"https://{search_name}.search.windows.net"
        print(f"Search endpoint: {search_endpoint}")
        
        # Wait for service to be ready
        print("Waiting for search service to be ready...")
        time.sleep(30)
    
    index_name = f"{args.certification_id}-content"
    
    search_index_client = SearchIndexClient(
        endpoint=search_endpoint,
        credential=credential,
    )
    
    search_client = SearchClient(
        endpoint=search_endpoint,
        index_name=index_name,
        credential=credential,
    )

    # Phase 2b: Index content
    if not args.skip_indexing:
        print("\n" + "=" * 60)
        print("PHASE 2b: Content Indexing")
        print("=" * 60)
        
        index_content(
            certification_id=args.certification_id,
            source_urls=all_source_urls,
            search_endpoint=search_endpoint,
            openai_endpoint=os.environ["OPENAI_ENDPOINT"],
            update_mode=False,
        )
    else:
        print("\n[Skipping indexing - using existing index]")

    # Phase 3: Generate episodes
    print("\n" + "=" * 60)
    print("PHASE 3: Episode Generation")
    print("=" * 60)
    
    # Expand domains into episode units
    episode_units = []
    for domain in skills_outline.get("domains", []):
        topics = domain.get("topics", [])
        source_urls = domain.get("sourceUrls", [])
        
        # For now, 1 domain = 1 episode (multi-part handled inside)
        episode_units.append({
            "domain": domain["name"],
            "topics": topics,
            "sourceUrls": source_urls,
            "total_parts": 1,
            "part": 1,
        })
    
    print(f"Processing {len(episode_units)} episode units...")
    
    # Prepare all episodes (sequential - GPT calls)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    prepared_episodes = []
    skipped = 0
    errors = []
    
    for i, unit in enumerate(episode_units):
        episode_number = i + 1
        episode_title = unit["domain"]
        
        if not args.force_regenerate and episode_exists(
            args.certification_id, args.audio_format, episode_number, cosmos_client
        ):
            print(f"  Skipping episode {episode_number}: {episode_title} (exists)")
            skipped += 1
            continue
        
        try:
            prepared = prepare_episode(
                episode_number=episode_number,
                skill_domain=unit["domain"],
                skill_topics=unit["topics"],
                source_urls=unit.get("sourceUrls", []),
                certification_id=args.certification_id,
                audio_format=args.audio_format,
                search_client=search_client,
                openai_client=openai_client,
                jinja_env=jinja_env,
                instructional_voice=args.instructional_voice,
                podcast_host_voice=args.podcast_host_voice,
                podcast_expert_voice=args.podcast_expert_voice,
                episode_title=episode_title,
            )
            prepared_episodes.append(prepared)
        except Exception as e:
            msg = f"Error preparing episode {episode_number}: {e}"
            print(f"  ERROR: {msg}")
            errors.append(msg)
    
    print(f"\nPrepared: {len(prepared_episodes)} | Skipped: {skipped} | Errors: {len(errors)}")
    
    # Synthesize audio in parallel
    print(f"\nSynthesizing {len(prepared_episodes)} episodes (max {TTS_MAX_WORKERS} concurrent)...")
    
    synthesized = []
    with ThreadPoolExecutor(max_workers=TTS_MAX_WORKERS) as executor:
        future_to_ep = {
            executor.submit(
                synthesize_episode_audio,
                ep,
                args.certification_id,
                args.audio_format,
            ): ep
            for ep in prepared_episodes
        }
        
        for future in as_completed(future_to_ep):
            ep = future_to_ep[future]
            try:
                audio_result = future.result()
                ep["audio_result"] = audio_result
                synthesized.append(ep)
                print(f"  ✓ Episode {ep['episode_number']}: {audio_result['duration_seconds']:.1f}s")
            except Exception as e:
                msg = f"TTS failed for episode {ep['episode_number']}: {e}"
                print(f"  ✗ {msg}")
                errors.append(msg)
    
    # Finalize (upload + save to Cosmos)
    print(f"\nFinalizing {len(synthesized)} episodes...")
    
    generated = []
    for ep in synthesized:
        try:
            doc = finalize_episode(ep, args.certification_id, args.audio_format, cosmos_client)
            generated.append(doc)
            print(f"  ✓ Saved {doc['id']}")
        except Exception as e:
            msg = f"Finalize failed for episode {ep['episode_number']}: {e}"
            print(f"  ✗ {msg}")
            errors.append(msg)

    # Summary
    print("\n" + "=" * 60)
    print("GENERATION COMPLETE")
    print("=" * 60)
    print(f"Generated: {len(generated)}")
    print(f"Skipped:   {skipped}")
    print(f"Errors:    {len(errors)}")
    
    if generated:
        total_duration = sum(ep.get("durationSeconds", 0) for ep in generated)
        print(f"Total Duration: {total_duration / 3600:.1f} hours")
    
    # Phase 4: Cleanup ephemeral AI Search
    if search_name:
        print("\n" + "=" * 60)
        print("PHASE 4: Cleanup")
        print("=" * 60)
        print(f"Deleting ephemeral search service: {search_name}")
        
        result = subprocess.run([
            "az", "search", "service", "delete",
            "--name", search_name,
            "--resource-group", resource_group,
            "--yes"
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("  ✓ Search service deleted")
        else:
            print(f"  ⚠ Failed to delete search service: {result.stderr}")
    
    if errors:
        print("\nErrors:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    
    print("\nSuccess!")


if __name__ == "__main__":
    main()
