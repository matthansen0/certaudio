"""
Generate audio episodes for a certification course.

This module orchestrates the full episode generation pipeline:
1. Parse skills outline from discover step
2. Retrieve relevant content from AI Search for each skill domain
3. Generate narration scripts using Azure OpenAI
4. Convert narration to SSML
5. Synthesize audio using Azure AI Speech
6. Upload artifacts to Azure Blob Storage
7. Save episode metadata to Cosmos DB
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from jinja2 import Environment, FileSystemLoader
from openai import AzureOpenAI

# Import sibling tools
from .synthesize_audio import synthesize_audio
from .upload_to_blob import upload_to_blob
from .save_episode import save_episode


def get_embedding(text: str, openai_client: AzureOpenAI) -> list[float]:
    """Generate embedding for text using Azure OpenAI."""
    response = openai_client.embeddings.create(
        model="text-embedding-3-large",
        input=text,
    )
    return response.data[0].embedding


def retrieve_content(
    certification_id: str,
    skill_domain: str,
    skill_topics: list[str],
    search_client: SearchClient,
    openai_client: AzureOpenAI,
) -> dict:
    """
    Retrieve relevant content from Azure AI Search.

    Returns:
        Dict with 'content', 'source_urls', and 'content_hash'
    """
    # Build search query from domain and topics
    query_text = f"{skill_domain}\n" + "\n".join(skill_topics[:10])  # Limit topics in query

    # Generate embedding for hybrid search
    try:
        query_embedding = get_embedding(query_text, openai_client)

        # Perform hybrid search (keyword + vector)
        vector_query = VectorizedQuery(
            vector=query_embedding,
            k_nearest_neighbors=10,
            fields="contentVector",
        )

        results = search_client.search(
            search_text=query_text,
            vector_queries=[vector_query],
            select=["content", "sourceUrl", "title", "chunkId"],
            top=15,
        )
    except Exception as e:
        print(f"Warning: Vector search failed ({e}), falling back to keyword search")
        results = search_client.search(
            search_text=query_text,
            select=["content", "sourceUrl", "title", "chunkId"],
            top=15,
        )

    # Aggregate results
    content_parts = []
    source_urls = set()

    for result in results:
        title = result.get("title", "Content")
        content = result.get("content", "")
        content_parts.append(f"## {title}\n\n{content}")
        if result.get("sourceUrl"):
            source_urls.add(result["sourceUrl"])

    combined_content = "\n\n---\n\n".join(content_parts) if content_parts else "No content found for this topic."

    # Generate content hash for tracking changes
    content_hash = hashlib.sha256(combined_content.encode()).hexdigest()[:16]

    return {
        "content": combined_content,
        "source_urls": list(source_urls),
        "content_hash": content_hash,
    }


def generate_narration(
    episode_number: int,
    skill_domain: str,
    skill_topics: list[str],
    retrieved_content: dict,
    audio_format: str,
    openai_client: AzureOpenAI,
    jinja_env: Environment,
    is_amendment: bool = False,
    amendment_of: int = None,
    prior_episode_summary: str = None,
) -> str:
    """Generate narration script using Azure OpenAI."""
    template = jinja_env.get_template("narration.jinja2")

    prompt = template.render(
        episode_number=episode_number,
        skill_domain=skill_domain,
        skill_topics=skill_topics,
        retrieved_content=retrieved_content,
        audio_format=audio_format,
        is_amendment=is_amendment,
        amendment_of=amendment_of,
        prior_episode_summary=prior_episode_summary,
    )

    # Split system and user parts
    parts = prompt.split("user:")
    system_prompt = parts[0].replace("system:", "").strip()
    user_prompt = parts[1].strip() if len(parts) > 1 else ""

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        max_tokens=4000,
    )

    return response.choices[0].message.content


def generate_ssml(
    narration: str,
    audio_format: str,
    openai_client: AzureOpenAI,
    jinja_env: Environment,
) -> str:
    """Convert narration script to SSML."""
    template = jinja_env.get_template("ssml.jinja2")

    prompt = template.render(
        narration=narration,
        audio_format=audio_format,
    )

    # Split system and user parts
    parts = prompt.split("user:")
    system_prompt = parts[0].replace("system:", "").strip()
    user_prompt = parts[1].strip() if len(parts) > 1 else ""

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,  # Lower temperature for more consistent SSML
        max_tokens=8000,
    )

    ssml = response.choices[0].message.content

    # Clean up any markdown code blocks if present
    if ssml.startswith("```"):
        lines = ssml.split("\n")
        ssml = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return ssml


def get_next_episode_number(
    certification_id: str,
    audio_format: str,
    cosmos_client: CosmosClient,
) -> int:
    """Get the next episode number for a certification/format combo."""
    database = cosmos_client.get_database_client(
        os.environ.get("COSMOS_DB_DATABASE", "certaudio")
    )
    container = database.get_container_client("episodes")

    query = """
        SELECT VALUE MAX(c.sequenceNumber)
        FROM c
        WHERE c.certificationId = @certId
          AND c.format = @format
    """
    params = [
        {"name": "@certId", "value": certification_id},
        {"name": "@format", "value": audio_format},
    ]

    results = list(
        container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )

    max_seq = results[0] if results and results[0] is not None else 0
    return max_seq + 1


def process_skill_domain(
    episode_number: int,
    skill_domain: str,
    skill_topics: list[str],
    source_urls: list[str],
    certification_id: str,
    audio_format: str,
    search_client: SearchClient,
    openai_client: AzureOpenAI,
    cosmos_client: CosmosClient,
    jinja_env: Environment,
) -> dict:
    """Process a single skill domain and generate an episode."""
    print(f"\n{'='*60}")
    print(f"Episode {episode_number}: {skill_domain}")
    print(f"Topics: {len(skill_topics)}")
    print(f"{'='*60}")

    # 1. Retrieve relevant content from AI Search
    print("Step 1: Retrieving content from AI Search...")
    retrieved_content = retrieve_content(
        certification_id=certification_id,
        skill_domain=skill_domain,
        skill_topics=skill_topics,
        search_client=search_client,
        openai_client=openai_client,
    )
    print(f"  - Retrieved {len(retrieved_content['source_urls'])} source URLs")
    print(f"  - Content hash: {retrieved_content['content_hash']}")

    # 2. Generate narration script
    print("Step 2: Generating narration script...")
    narration = generate_narration(
        episode_number=episode_number,
        skill_domain=skill_domain,
        skill_topics=skill_topics,
        retrieved_content=retrieved_content,
        audio_format=audio_format,
        openai_client=openai_client,
        jinja_env=jinja_env,
    )
    word_count = len(narration.split())
    print(f"  - Generated {word_count} words")

    # 3. Convert to SSML
    print("Step 3: Converting to SSML...")
    ssml = generate_ssml(
        narration=narration,
        audio_format=audio_format,
        openai_client=openai_client,
        jinja_env=jinja_env,
    )
    print(f"  - SSML length: {len(ssml)} characters")

    # 4. Synthesize audio
    print("Step 4: Synthesizing audio...")
    audio_result = synthesize_audio(
        ssml_content=ssml,
        episode_number=episode_number,
        certification_id=certification_id,
        audio_format=audio_format,
    )
    print(f"  - Duration: {audio_result['duration_seconds']:.1f} seconds")

    # 5. Upload to blob storage
    print("Step 5: Uploading to blob storage...")
    upload_result = upload_to_blob(
        audio_file_path=audio_result["audio_path"],
        script_content=narration,
        ssml_content=ssml,
        certification_id=certification_id,
        audio_format=audio_format,
        episode_number=episode_number,
    )
    print(f"  - Audio URL: {upload_result['audio_url']}")

    # 6. Save episode metadata to Cosmos DB
    print("Step 6: Saving episode metadata...")

    # Merge source URLs from discovery and retrieval
    all_source_urls = list(set(source_urls + retrieved_content["source_urls"]))

    episode_doc = save_episode(
        certification_id=certification_id,
        audio_format=audio_format,
        episode_number=episode_number,
        skill_domain=skill_domain,
        skill_topics=skill_topics,
        audio_url=upload_result["audio_url"],
        script_url=upload_result["script_url"],
        duration_seconds=audio_result["duration_seconds"],
        is_amendment=False,
        amendment_of=0,
        source_urls=all_source_urls,
        content_hash=retrieved_content["content_hash"],
    )
    print(f"  - Episode ID: {episode_doc['id']}")

    return episode_doc


def main():
    """Main entry point for episode generation."""
    parser = argparse.ArgumentParser(description="Generate audio episodes for certification")
    parser.add_argument("--certification-id", required=True, help="Certification ID (e.g., dp-700)")
    parser.add_argument("--audio-format", default="instructional", choices=["instructional", "podcast"])
    parser.add_argument("--skills-outline", required=True, help="JSON skills outline from discover step")
    parser.add_argument("--batch-index", type=int, default=0, help="Batch index for parallel processing")
    parser.add_argument("--batch-size", type=int, default=10, help="Number of skills per batch")

    args = parser.parse_args()

    # Parse skills outline
    try:
        skills = json.loads(args.skills_outline)
    except json.JSONDecodeError as e:
        print(f"Error parsing skills outline: {e}", file=sys.stderr)
        sys.exit(1)

    # Filter to only skills with topics (main skill domains)
    main_skills = [s for s in skills if s.get("topics") and len(s["topics"]) > 0]

    # Calculate batch slice
    start_idx = args.batch_index * args.batch_size
    end_idx = start_idx + args.batch_size
    batch_skills = main_skills[start_idx:end_idx]

    if not batch_skills:
        print(f"No skills in batch {args.batch_index} (indices {start_idx}-{end_idx})")
        print(f"Total main skills: {len(main_skills)}")
        return

    print(f"\n{'#'*60}")
    print(f"# Episode Generation: {args.certification_id.upper()}")
    print(f"# Format: {args.audio_format}")
    print(f"# Batch: {args.batch_index} (skills {start_idx+1}-{min(end_idx, len(main_skills))} of {len(main_skills)})")
    print(f"{'#'*60}")

    # Get configuration from environment
    search_endpoint = os.environ.get("SEARCH_ENDPOINT")
    openai_endpoint = os.environ.get("OPENAI_ENDPOINT")
    cosmos_endpoint = os.environ.get("COSMOS_DB_ENDPOINT")

    missing = []
    if not search_endpoint:
        missing.append("SEARCH_ENDPOINT")
    if not openai_endpoint:
        missing.append("OPENAI_ENDPOINT")
    if not cosmos_endpoint:
        missing.append("COSMOS_DB_ENDPOINT")

    if missing:
        print(f"Error: Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    # Initialize clients
    credential = DefaultAzureCredential()

    search_client = SearchClient(
        endpoint=search_endpoint,
        index_name=f"{args.certification_id}-content",
        credential=credential,
    )

    openai_client = AzureOpenAI(
        azure_endpoint=openai_endpoint,
        azure_ad_token_provider=lambda: credential.get_token(
            "https://cognitiveservices.azure.com/.default"
        ).token,
        api_version="2024-02-01",
    )

    cosmos_client = CosmosClient(cosmos_endpoint, credential)

    # Set up Jinja2 environment for prompts
    prompts_dir = Path(__file__).parent.parent / "prompts"
    jinja_env = Environment(loader=FileSystemLoader(prompts_dir))

    # Deterministic episode numbering to support parallel batch generation.
    # Episode numbers are based on the global index of the skill domain within the
    # discovered main_skills list.
    base_episode_number = start_idx + 1
    print(f"\nBatch base episode number: {base_episode_number}")

    # Process each skill in the batch
    generated_episodes = []
    errors: list[str] = []
    for i, skill in enumerate(batch_skills):
        episode_number = base_episode_number + i
        try:
            episode = process_skill_domain(
                episode_number=episode_number,
                skill_domain=skill["name"],
                skill_topics=skill.get("topics", []),
                source_urls=skill.get("sourceUrls", []),
                certification_id=args.certification_id,
                audio_format=args.audio_format,
                search_client=search_client,
                openai_client=openai_client,
                cosmos_client=cosmos_client,
                jinja_env=jinja_env,
            )
            generated_episodes.append(episode)
        except Exception as e:
            msg = f"Error processing skill '{skill.get('name', '<unknown>')}' (episode {episode_number}): {e}"
            print(f"\n{msg}", file=sys.stderr)
            errors.append(msg)

    # Summary
    print(f"\n{'='*60}")
    print(f"BATCH COMPLETE")
    print(f"Generated {len(generated_episodes)} of {len(batch_skills)} episodes")
    for ep in generated_episodes:
        print(f"  - {ep['id']}: {ep['title']} ({ep['durationSeconds']:.0f}s)")
    print(f"{'='*60}")

    if errors:
        print(f"\nBatch failed with {len(errors)} error(s).", file=sys.stderr)
        sys.exit(1)

    if len(generated_episodes) == 0:
        print("\nBatch produced 0 episodes (unexpected).", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
