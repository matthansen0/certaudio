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
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from xml.sax.saxutils import escape
from pathlib import Path
import requests

from azure.cosmos import CosmosClient
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from jinja2 import Environment, FileSystemLoader
from openai import AzureOpenAI, RateLimitError

# Import sibling tools
from .synthesize_audio import synthesize_audio, synthesize_audio_segments
from .upload_to_blob import upload_to_blob
from .save_episode import save_episode


def _get_speech_region() -> str:
    return os.environ.get("SPEECH_REGION") or "centralus"


def _get_speech_headers() -> dict:
    speech_key = os.environ.get("SPEECH_KEY")
    if speech_key:
        return {"Ocp-Apim-Subscription-Key": speech_key}

    # Fall back to Entra auth - need to exchange for a Speech token via issueToken endpoint.
    # When using custom subdomain resources (required for Entra auth), we must use
    # the resource's own issueToken endpoint, not the regional one.
    speech_endpoint = os.environ.get("SPEECH_ENDPOINT", "")
    if not speech_endpoint:
        raise ValueError(
            "SPEECH_ENDPOINT is required for Entra authentication with the Voice List API. "
            "Set SPEECH_KEY to use API key auth instead."
        )
    
    credential = DefaultAzureCredential()
    aad_token = credential.get_token("https://cognitiveservices.azure.com/.default")
    
    # Exchange the Entra token for a Speech service token via the resource's issueToken endpoint
    # Custom subdomain endpoint format: https://<resource>.cognitiveservices.azure.com/
    issue_token_url = speech_endpoint.rstrip("/") + "/sts/v1.0/issueToken"
    resp = requests.post(
        issue_token_url,
        headers={"Authorization": f"Bearer {aad_token.token}"},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to get Speech token via issueToken ({resp.status_code}): {resp.text[:200]}"
        )
    speech_token = resp.text
    return {"Authorization": f"Bearer {speech_token}"}


def _fetch_speech_voices() -> tuple[str, set[str]]:
    region = _get_speech_region()
    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list"
    headers = _get_speech_headers()
    resp = requests.get(url, headers=headers, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Voice list request failed ({resp.status_code}): {resp.text[:200]}"
        )
    data = resp.json()
    # Use ShortName which is the format used in SSML voice names (e.g., "en-US-JennyNeural")
    voice_names = {v.get("ShortName") for v in data if v.get("ShortName")}
    return region, voice_names


def preflight_validate_voices(voices: list[str]) -> None:
    """
    Validate that the requested voices are available in the configured Speech region.
    
    This check uses the Speech Voice List API, which requires either:
    - SPEECH_KEY (API key auth), or
    - SPEECH_ENDPOINT + Cognitive Services Speech User role with issueToken permission
    
    If auth fails, a warning is printed but execution continues (fail-open for local dev).
    Set SKIP_VOICE_PREFLIGHT=true to skip entirely.
    """
    if not voices:
        return

    if os.environ.get("SKIP_VOICE_PREFLIGHT", "false").lower() == "true":
        print("Voice preflight skipped (SKIP_VOICE_PREFLIGHT=true)")
        return

    try:
        region, available = _fetch_speech_voices()
    except Exception as e:
        # Fail-open: warn but don't block if we can't reach the Voice List API.
        # The actual synthesis call will fail later if the voice is invalid.
        print(f"Voice preflight warning: Could not validate voices ({e}). Continuing anyway.")
        return

    missing = [v for v in voices if v not in available]
    if missing:
        raise ValueError(
            "Voice preflight failed. The following voice(s) are not available in "
            f"region '{region}': {', '.join(missing)}. "
            "Check your Speech region or choose a supported voice."
        )

    print(f"Voice preflight OK for region '{region}': {', '.join(voices)}")


def create_cosmos_client_with_retry(
    cosmos_endpoint: str,
    credential,
    database_name: str = "certaudio",
    max_wait_seconds: int = 600,
    poll_seconds: int = 30,
) -> CosmosClient:
    deadline = time.time() + max_wait_seconds
    last_error: Exception | None = None

    while time.time() < deadline:
        try:
            client = CosmosClient(cosmos_endpoint, credential)
            # Touch the account/database to validate auth (fails fast if RBAC missing)
            client.get_database_client(database_name).read()
            return client
        except Exception as e:
            last_error = e
            msg = str(e)
            if "Request blocked by Auth" in msg or "Microsoft.DocumentDB" in msg or "Forbidden" in msg:
                remaining = int(deadline - time.time())
                print(
                    f"Cosmos RBAC not ready/assigned yet; retrying in {poll_seconds}s (remaining ~{remaining}s)...",
                    file=sys.stderr,
                )
                time.sleep(poll_seconds)
                continue
            raise

    raise last_error or TimeoutError("Timed out waiting for Cosmos RBAC")


def call_openai_with_retry(openai_client: AzureOpenAI, max_retries: int = 5, **kwargs):
    """
    Call OpenAI API with exponential backoff retry for rate limits.
    
    Args:
        openai_client: The Azure OpenAI client
        max_retries: Maximum number of retry attempts
        **kwargs: Arguments to pass to chat.completions.create
    
    Returns:
        The API response
    """
    base_delay = 2  # Start with 2 second delay
    
    for attempt in range(max_retries):
        try:
            return openai_client.chat.completions.create(**kwargs)
        except RateLimitError as e:
            if attempt == max_retries - 1:
                raise  # Re-raise on final attempt
            
            # Extract retry-after if available, otherwise use exponential backoff
            delay = base_delay * (2 ** attempt)  # 2, 4, 8, 16, 32 seconds
            print(f"  Rate limit hit, retrying in {delay}s (attempt {attempt + 1}/{max_retries})...")
            time.sleep(delay)
    
    # Should not reach here, but just in case
    raise Exception("Max retries exceeded for OpenAI API call")


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
    is_continuation: bool = False,
    part_number: int = 1,
    topics_covered_so_far: str = None,
    min_words: int = 1200,
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
        is_continuation=is_continuation,
        part_number=part_number,
        topics_covered_so_far=topics_covered_so_far,
        min_words=min_words,
    )

    # Split system and user parts
    parts = prompt.split("user:")
    system_prompt = parts[0].replace("system:", "").strip()
    user_prompt = parts[1].strip() if len(parts) > 1 else ""

    response = call_openai_with_retry(
        openai_client,
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        max_tokens=4000,
    )

    return response.choices[0].message.content


CONTINUATION_MARKER = "[END OF PART - CONTINUE IN NEXT PART]"


def needs_continuation(narration: str) -> bool:
    """Check if narration indicates more content is needed."""
    return CONTINUATION_MARKER in narration


def clean_narration(narration: str) -> str:
    """Remove continuation marker from narration."""
    return narration.replace(CONTINUATION_MARKER, "").strip()


def generate_ssml(
    narration: str,
    audio_format: str,
    openai_client: AzureOpenAI,
    jinja_env: Environment,
    instructional_voice: str = "en-US-AndrewNeural",
    podcast_host_voice: str = "en-US-GuyNeural",
    podcast_expert_voice: str = "en-US-TonyNeural",
) -> str:
    """Convert narration script to SSML."""
    # LLM-generated SSML has proven brittle (Speech rejects it with SSML parsing errors).
    # Default to deterministic SSML generation; keep LLM path for experimentation.
    use_llm = os.environ.get("USE_LLM_SSML", "false").lower() == "true"
    if not use_llm:
        return build_ssml_from_narration(
            narration, audio_format,
            instructional_voice=instructional_voice,
            podcast_host_voice=podcast_host_voice,
            podcast_expert_voice=podcast_expert_voice,
        )

    template = jinja_env.get_template("ssml.jinja2")
    prompt = template.render(
        narration=narration,
        audio_format=audio_format,
        instructional_voice=instructional_voice,
        podcast_host_voice=podcast_host_voice,
        podcast_expert_voice=podcast_expert_voice,
    )

    parts = prompt.split("user:")
    system_prompt = parts[0].replace("system:", "").strip()
    user_prompt = parts[1].strip() if len(parts) > 1 else ""

    response = call_openai_with_retry(
        openai_client,
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=8000,
    )

    ssml = response.choices[0].message.content
    if ssml.startswith("```"):
        lines = ssml.split("\n")
        ssml = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return sanitize_ssml(
        ssml,
        audio_format,
        instructional_voice=instructional_voice,
        podcast_host_voice=podcast_host_voice,
        podcast_expert_voice=podcast_expert_voice,
    )


def build_ssml_from_narration(
    narration: str,
    audio_format: str,
    instructional_voice: str = "en-US-AndrewNeural",
    podcast_host_voice: str = "en-US-GuyNeural",
    podcast_expert_voice: str = "en-US-TonyNeural",
) -> str:
    """Generate conservative, Speech-compatible SSML from plain narration text."""

    def _normalize_text(text: str) -> str:
        # Remove speaker markers if they appear in instructional.
        text = text.replace("[HOST]", "").replace("[EXPERT]", "")
        # Convert blank lines into slightly longer pauses.
        lines = [ln.rstrip() for ln in text.splitlines()]
        out_parts: list[str] = []
        for ln in lines:
            if not ln.strip():
                out_parts.append('<break time="300ms"/>')
                continue
            # Escape the line FIRST to handle special characters, then replace [PAUSE]
            escaped_ln = escape(ln)
            # Now convert [PAUSE] markers to SSML break tags (after escaping)
            escaped_ln = escaped_ln.replace("[PAUSE]", '<break time="500ms"/>')
            out_parts.append(escaped_ln)
            out_parts.append('<break time="200ms"/>')
        return " ".join(out_parts).strip()

    speak_open = (
        '<speak version="1.0" '
        'xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="http://www.w3.org/2001/mstts" '
        'xml:lang="en-US">'
    )
    speak_close = "</speak>"

    if audio_format == "podcast":
        # Two voices, switched by [HOST]/[EXPERT] markers.
        host_voice = podcast_host_voice
        expert_voice = podcast_expert_voice

        # Split while keeping markers.
        tokens = re.split(r"(\[HOST\]|\[EXPERT\])", narration)
        current = "HOST"
        chunks: list[str] = []
        for tok in tokens:
            if tok == "[HOST]":
                current = "HOST"
                continue
            if tok == "[EXPERT]":
                current = "EXPERT"
                continue
            if not tok.strip():
                continue
            voice = host_voice if current == "HOST" else expert_voice
            inner = _normalize_text(tok)
            # Apply same prosody rate to both voices for consistency
            inner = f'<prosody rate="-5%">{inner}</prosody>'
            chunks.append(f'<voice name="{voice}">{inner}</voice>')

        ssml = speak_open + " ".join(chunks) + speak_close
        ET.fromstring(ssml)  # validate
        return ssml

    # Instructional: single voice using the selected neural voice.
    voice = instructional_voice
    inner = _normalize_text(narration)
    # Apply slight rate reduction for clarity and comprehension
    inner = f'<prosody rate="-5%">{inner}</prosody>'
    ssml = speak_open + f'<voice name="{voice}">{inner}</voice>' + speak_close
    ET.fromstring(ssml)  # validate
    return ssml


def sanitize_ssml(
    ssml: str,
    audio_format: str,
    instructional_voice: str = "en-US-AndrewNeural",
    podcast_host_voice: str = "en-US-GuyNeural",
    podcast_expert_voice: str = "en-US-TonyNeural",
) -> str:
    ssml_out = ssml.strip()

    # Remove ASCII control chars not allowed in XML.
    ssml_out = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", ssml_out)

    # Remove any <lang> wrappers entirely (keep inner content).
    ssml_out = re.sub(r"</?lang\b[^>]*>", "", ssml_out, flags=re.IGNORECASE)

    # Force speak root to en-US.
    # If xml:lang exists, normalize it. If not, add it.
    if re.search(r"<speak\b[^>]*\bxml:lang=", ssml_out, flags=re.IGNORECASE):
        ssml_out = re.sub(
            r"(<speak\b[^>]*\bxml:lang=)(['\"]).*?\2",
            r"\1\2en-US\2",
            ssml_out,
            flags=re.IGNORECASE,
        )
    else:
        ssml_out = re.sub(
            r"<speak\b",
            '<speak xml:lang="en-US"',
            ssml_out,
            count=1,
            flags=re.IGNORECASE,
        )

    # Allow the user-selected voices plus common Dragon HD and Neural voices
    # This list needs to include all voices that might be used in either format
    common_voices = {
        "en-US-GuyNeural", "en-US-TonyNeural", "en-US-AndrewNeural", "en-US-BrianNeural",
        "en-US-AvaNeural", "en-US-JennyNeural", "en-US-AriaNeural",
        # Dragon HD voices (high quality)
        "en-US-Andrew:DragonHDLatestNeural", "en-US-Ava:DragonHDLatestNeural",
        "en-US-Brian:DragonHDLatestNeural", "en-US-Emma:DragonHDLatestNeural",
    }
    allowed_voices = common_voices | {instructional_voice, podcast_host_voice, podcast_expert_voice}

    # Replace any unexpected voice with the default neural voice.
    default_voice = instructional_voice

    def _voice_repl(match: re.Match) -> str:
        prefix = match.group(1)
        quote = match.group(2)
        voice_name = match.group(3)
        if voice_name in allowed_voices:
            return match.group(0)
        return f"{prefix}{quote}{default_voice}{quote}"

    ssml_out = re.sub(
        r"(<voice\b[^>]*\bname=)(['\"])([^'\"]+)(\2)",
        _voice_repl,
        ssml_out,
        flags=re.IGNORECASE,
    )

    # Escape stray '&' that aren't part of XML entities.
    ssml_out = re.sub(
        r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)",
        "&amp;",
        ssml_out,
    )

    # Best-effort XML validation so we fail fast with a clearer local error if SSML is malformed.
    try:
        ET.fromstring(ssml_out)
    except ET.ParseError as e:
        snippet = ssml_out[:500].replace("\n", " ")
        raise ValueError(f"Generated SSML is not well-formed XML: {e}. Snippet: {snippet}")

    return ssml_out


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


def episode_exists(
    certification_id: str,
    audio_format: str,
    episode_number: int,
    cosmos_client: CosmosClient,
) -> bool:
    """Check if an episode already exists in Cosmos DB."""
    database = cosmos_client.get_database_client(
        os.environ.get("COSMOS_DB_DATABASE", "certaudio")
    )
    container = database.get_container_client("episodes")
    
    episode_id = f"{certification_id}-{audio_format}-{episode_number:03d}"
    
    try:
        container.read_item(item=episode_id, partition_key=certification_id)
        return True
    except Exception:
        return False


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
    instructional_voice: str = "en-US-AndrewNeural",
    podcast_host_voice: str = "en-US-GuyNeural",
    podcast_expert_voice: str = "en-US-TonyNeural",
    episode_title: str = None,
) -> list[dict]:
    """
    Process a single skill domain and generate episode(s).
    
    Returns a list of episode documents. If content requires multiple parts,
    multiple episodes are generated with titles like "Domain (Part 1)", "Domain (Part 2)".
    """
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

    # Merge source URLs from discovery and retrieval
    all_source_urls = list(set(source_urls + retrieved_content["source_urls"]))
    
    # Generate episode parts (may be 1 or more)
    episode_docs = []
    part_number = 1
    current_episode_number = episode_number
    topics_covered_so_far = ""
    max_parts = 5  # Safety limit
    base_min_words = int(os.environ.get("MIN_WORDS_PER_PART", "1200"))
    max_length_retries = 1
    
    while part_number <= max_parts:
        part_suffix = f" (Part {part_number})" if part_number > 1 else ""
        # Use episode_title if provided (first part), otherwise build from skill_domain
        if part_number == 1 and episode_title:
            domain_title = episode_title
        else:
            domain_title = f"{skill_domain}{part_suffix}"
        
        print(f"\nStep 2: Generating narration script{part_suffix}...")
        min_words = base_min_words
        for attempt in range(max_length_retries + 1):
            narration = generate_narration(
                episode_number=current_episode_number,
                skill_domain=skill_domain,
                skill_topics=skill_topics,
                retrieved_content=retrieved_content,
                audio_format=audio_format,
                openai_client=openai_client,
                jinja_env=jinja_env,
                is_continuation=(part_number > 1),
                part_number=part_number,
                topics_covered_so_far=topics_covered_so_far,
                min_words=min_words,
            )

            # Check if continuation is needed
            has_more = needs_continuation(narration)
            narration = clean_narration(narration)

            word_count = len(narration.split())
            print(f"  - Generated {word_count} words")
            if has_more:
                print("  - Content continues in next part...")

            if word_count >= min_words or attempt == max_length_retries:
                break

            min_words = int(min_words * 1.15)
            print(f"  - Narration too short; retrying with min_words={min_words}")

        # 3. Convert to SSML
        print(f"Step 3: Converting to SSML{part_suffix}...")
        ssml = generate_ssml(
            narration=narration,
            audio_format=audio_format,
            openai_client=openai_client,
            jinja_env=jinja_env,
            instructional_voice=instructional_voice,
            podcast_host_voice=podcast_host_voice,
            podcast_expert_voice=podcast_expert_voice,
        )
        print(f"  - SSML length: {len(ssml)} characters")

        # 4. Synthesize audio
        print(f"Step 4: Synthesizing audio{part_suffix}...")
        audio_result = synthesize_audio_with_chunking(
            narration=narration,
            ssml=ssml,
            episode_number=current_episode_number,
            certification_id=certification_id,
            audio_format=audio_format,
        )
        print(f"  - Duration: {audio_result['duration_seconds']:.1f} seconds")

        # 5. Upload to blob storage
        print(f"Step 5: Uploading to blob storage{part_suffix}...")
        upload_result = upload_to_blob(
            audio_file_path=audio_result["audio_path"],
            script_content=narration,
            ssml_content=ssml,
            certification_id=certification_id,
            audio_format=audio_format,
            episode_number=current_episode_number,
        )
        print(f"  - Audio URL: {upload_result['audio_url']}")

        # 6. Save episode metadata to Cosmos DB
        print(f"Step 6: Saving episode metadata{part_suffix}...")
        episode_doc = save_episode(
            certification_id=certification_id,
            audio_format=audio_format,
            episode_number=current_episode_number,
            skill_domain=skill_domain,  # Original domain for grouping
            skill_topics=skill_topics,
            audio_url=upload_result["audio_url"],
            script_url=upload_result["script_url"],
            duration_seconds=audio_result["duration_seconds"],
            is_amendment=False,
            amendment_of=0,
            source_urls=all_source_urls,
            content_hash=retrieved_content["content_hash"],
            title=domain_title,  # Title with part number for display
        )
        print(f"  - Episode ID: {episode_doc['id']}")
        
        episode_docs.append(episode_doc)
        
        if not has_more:
            break
            
        # Prepare for next part
        part_number += 1
        current_episode_number += 1
        # Track what we've covered (first ~100 words of previous narration as context)
        topics_covered_so_far = narration[:500] + "..."
    
    return episode_docs


# Maximum concurrent TTS requests (Azure Speech S0 tier supports 20, we use 10 for safety)
TTS_MAX_WORKERS = int(os.environ.get("TTS_MAX_WORKERS", "10"))


def split_narration_for_tts(narration: str, max_words_per_segment: int = 850) -> list[str]:
    """Split narration into segments to keep each TTS request under the Speech service limit."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", narration) if p.strip()]
    segments: list[str] = []
    current: list[str] = []
    current_words = 0

    for p in paragraphs:
        words = len(p.split())
        if current and current_words + words > max_words_per_segment:
            segments.append("\n\n".join(current).strip())
            current = []
            current_words = 0
        current.append(p)
        current_words += words

    if current:
        segments.append("\n\n".join(current).strip())

    return segments


def synthesize_audio_with_chunking(
    narration: str,
    ssml: str,
    episode_number: int,
    certification_id: str,
    audio_format: str,
) -> dict:
    """Synthesize audio, splitting into multiple Speech requests when narration is long."""
    # If the SSML is already short, use the simple path.
    narration_words = len(narration.split())
    if narration_words <= 900:
        return synthesize_audio(
            ssml_content=ssml,
            episode_number=episode_number,
            certification_id=certification_id,
            audio_format=audio_format,
        )

    segments = split_narration_for_tts(narration)
    ssml_segments = [build_ssml_from_narration(seg, audio_format) for seg in segments]

    # Build output path consistent with synthesize_audio.
    import tempfile
    temp_dir = tempfile.mkdtemp()
    filename = f"{certification_id}_{audio_format}_{episode_number:03d}.mp3"
    output_path = os.path.join(temp_dir, filename)

    print(f"Narration is long ({narration_words} words); synthesizing in {len(ssml_segments)} segment(s)...")

    ok, duration = synthesize_audio_segments(ssml_segments, output_path)
    if not ok:
        raise RuntimeError(f"Audio synthesis failed for episode {episode_number}")

    return {
        "audio_path": output_path,
        "duration_seconds": duration,
        "filename": filename,
    }


def prepare_episode(
    episode_number: int,
    skill_domain: str,
    skill_topics: list[str],
    source_urls: list[str],
    certification_id: str,
    audio_format: str,
    search_client: SearchClient,
    openai_client: AzureOpenAI,
    jinja_env: Environment,
    instructional_voice: str,
    podcast_host_voice: str,
    podcast_expert_voice: str,
    episode_title: str,
) -> dict:
    """
    Prepare episode content (retrieval + narration + SSML) without synthesizing audio.
    Returns a dict with all data needed for TTS and finalization.
    """
    print(f"\n--- Episode {episode_number}: {episode_title} ---")
    
    # 1. Retrieve relevant content from AI Search
    print("  [1/3] Retrieving content...")
    retrieved_content = retrieve_content(
        certification_id=certification_id,
        skill_domain=skill_domain,
        skill_topics=skill_topics,
        search_client=search_client,
        openai_client=openai_client,
    )
    
    all_source_urls = list(set(source_urls + retrieved_content["source_urls"]))
    
    # 2. Generate narration
    print("  [2/3] Generating narration...")
    base_min_words = int(os.environ.get("MIN_WORDS_PER_PART", "1200"))
    max_length_retries = 1
    
    min_words = base_min_words
    for attempt in range(max_length_retries + 1):
        narration = generate_narration(
            episode_number=episode_number,
            skill_domain=skill_domain,
            skill_topics=skill_topics,
            retrieved_content=retrieved_content,
            audio_format=audio_format,
            openai_client=openai_client,
            jinja_env=jinja_env,
            is_continuation=False,
            part_number=1,
            topics_covered_so_far="",
            min_words=min_words,
        )
        
        narration = clean_narration(narration)
        word_count = len(narration.split())
        
        if word_count >= min_words or attempt == max_length_retries:
            break
        
        min_words = int(min_words * 1.15)
        print(f"    Narration too short ({word_count} words); retrying...")
    
    print(f"    Generated {word_count} words")
    
    # 3. Convert to SSML
    print("  [3/3] Converting to SSML...")
    ssml = generate_ssml(
        narration=narration,
        audio_format=audio_format,
        openai_client=openai_client,
        jinja_env=jinja_env,
        instructional_voice=instructional_voice,
        podcast_host_voice=podcast_host_voice,
        podcast_expert_voice=podcast_expert_voice,
    )
    
    return {
        "episode_number": episode_number,
        "skill_domain": skill_domain,
        "skill_topics": skill_topics,
        "episode_title": episode_title,
        "narration": narration,
        "ssml": ssml,
        "source_urls": all_source_urls,
        "content_hash": retrieved_content["content_hash"],
    }


def synthesize_episode_audio(
    prepared: dict,
    certification_id: str,
    audio_format: str,
) -> dict:
    """
    Synthesize audio for a prepared episode. This is the slow step that runs in parallel.
    Thread-safe: only uses local state and Azure Speech API calls.
    """
    return synthesize_audio_with_chunking(
        narration=prepared["narration"],
        ssml=prepared["ssml"],
        episode_number=prepared["episode_number"],
        certification_id=certification_id,
        audio_format=audio_format,
    )


def finalize_episode(
    prepared: dict,
    certification_id: str,
    audio_format: str,
    cosmos_client: CosmosClient,
) -> dict:
    """
    Upload audio and save episode to Cosmos DB.
    """
    audio_result = prepared["audio_result"]
    
    # Upload to blob storage
    upload_result = upload_to_blob(
        audio_file_path=audio_result["audio_path"],
        script_content=prepared["narration"],
        ssml_content=prepared["ssml"],
        certification_id=certification_id,
        audio_format=audio_format,
        episode_number=prepared["episode_number"],
    )
    
    # Save to Cosmos DB
    episode_doc = save_episode(
        certification_id=certification_id,
        audio_format=audio_format,
        episode_number=prepared["episode_number"],
        skill_domain=prepared["skill_domain"],
        skill_topics=prepared["skill_topics"],
        audio_url=upload_result["audio_url"],
        script_url=upload_result["script_url"],
        duration_seconds=audio_result["duration_seconds"],
        is_amendment=False,
        amendment_of=0,
        source_urls=prepared["source_urls"],
        content_hash=prepared["content_hash"],
        title=prepared["episode_title"],
    )
    
    return episode_doc


def main() -> None:
    """Main entry point for episode generation."""
    parser = argparse.ArgumentParser(description="Generate audio episodes for certification")
    parser.add_argument("--certification-id", required=True, help="Certification ID (e.g., dp-700)")
    parser.add_argument("--audio-format", default="instructional", choices=["instructional", "podcast"])
    parser.add_argument("--instructional-voice", default="en-US-Andrew:DragonHDLatestNeural", 
                        help="Voice for instructional format (see .env.example for options)")
    parser.add_argument("--podcast-host-voice", default="en-US-Ava:DragonHDLatestNeural",
                        help="Host voice for podcast format (see .env.example for options)")
    parser.add_argument("--podcast-expert-voice", default="en-US-Andrew:DragonHDLatestNeural",
                        help="Expert voice for podcast format (see .env.example for options)")
    parser.add_argument("--skills-outline", required=True, help="JSON skills outline from discover step")
    parser.add_argument("--batch-index", type=int, default=0, help="Batch index for parallel processing")
    parser.add_argument("--batch-size", type=int, default=10, help="Number of episode units per batch")
    parser.add_argument("--topics-per-episode", type=int, default=5, help="Target topics per episode for optimal length")
    parser.add_argument("--force-regenerate", action="store_true",
                        help="Regenerate episodes even if they already exist (e.g., to change voice)")

    args = parser.parse_args()

    # Preflight voice availability to fail fast on unsupported voices/regions.
    voices_to_check = [args.instructional_voice]
    if args.audio_format == "podcast":
        voices_to_check = [args.podcast_host_voice, args.podcast_expert_voice]
    preflight_validate_voices(voices_to_check)

    # Parse skills outline
    try:
        skills = json.loads(args.skills_outline)
    except json.JSONDecodeError as e:
        print(f"Error parsing skills outline: {e}", file=sys.stderr)
        sys.exit(1)

    # Filter to only skills with topics (main skill domains)
    main_skills = [s for s in skills if s.get("topics") and len(s["topics"]) > 0]

    # Expand domains into episode units (groups of topics for ~8-10 min episodes)
    # Each episode unit = (domain_name, topic_subset, source_urls)
    episode_units = []
    for skill in main_skills:
        domain_name = skill["name"]
        topics = skill.get("topics", [])
        source_urls = skill.get("sourceUrls", [])
        
        # Split topics into chunks of args.topics_per_episode
        for chunk_idx in range(0, len(topics), args.topics_per_episode):
            chunk_topics = topics[chunk_idx:chunk_idx + args.topics_per_episode]
            episode_units.append({
                "domain": domain_name,
                "topics": chunk_topics,
                "sourceUrls": source_urls,
                "part": (chunk_idx // args.topics_per_episode) + 1,
                "total_parts": (len(topics) + args.topics_per_episode - 1) // args.topics_per_episode,
            })

    print(f"Expanded {len(main_skills)} domains into {len(episode_units)} episode units")

    # Calculate batch slice
    start_idx = args.batch_index * args.batch_size
    end_idx = start_idx + args.batch_size
    batch_units = episode_units[start_idx:end_idx]

    if not batch_units:
        print(f"No episode units in batch {args.batch_index} (indices {start_idx}-{end_idx})")
        print(f"Total episode units: {len(episode_units)}")
        return

    print(f"\n{'#'*60}")
    print(f"# Episode Generation: {args.certification_id.upper()}")
    print(f"# Format: {args.audio_format}")
    print(f"# Batch: {args.batch_index} (units {start_idx+1}-{min(end_idx, len(episode_units))} of {len(episode_units)})")
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
    token_credential = DefaultAzureCredential()
    search_admin_key = os.environ.get("SEARCH_ADMIN_KEY")
    search_credential = AzureKeyCredential(search_admin_key) if search_admin_key else token_credential

    search_client = SearchClient(
        endpoint=search_endpoint,
        index_name=f"{args.certification_id}-content",
        credential=search_credential,
    )

    openai_api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")
    if openai_api_key:
        openai_client = AzureOpenAI(
            azure_endpoint=openai_endpoint,
            api_key=openai_api_key,
            api_version="2024-02-01",
        )
    else:
        openai_client = AzureOpenAI(
            azure_endpoint=openai_endpoint,
            azure_ad_token_provider=lambda: token_credential.get_token(
                "https://cognitiveservices.azure.com/.default"
            ).token,
            api_version="2024-02-01",
        )

    # Cosmos DB account has disableLocalAuth=true, so we must use Entra ID tokens.
    cosmos_client = create_cosmos_client_with_retry(cosmos_endpoint, token_credential)

    # Set up Jinja2 environment for prompts
    prompts_dir = Path(__file__).parent.parent / "prompts"
    jinja_env = Environment(loader=FileSystemLoader(prompts_dir))

    # Deterministic episode numbering to support parallel batch generation.
    # Episode numbers are based on the global index of the episode unit.
    base_episode_number = start_idx + 1
    print(f"\nBatch base episode number: {base_episode_number}")
    if args.force_regenerate:
        print("Force regenerate mode: will overwrite existing episodes")

    # Process each episode unit in the batch
    # Phase 1: Prepare all episodes (content retrieval + narration generation)
    # This is done sequentially as GPT calls benefit from rate limit backoff
    prepared_episodes = []
    skipped_episodes = []
    errors: list[str] = []
    
    print(f"\n{'='*60}")
    print(f"PHASE 1: Preparing narrations for {len(batch_units)} episodes")
    print(f"{'='*60}")
    
    for i, unit in enumerate(batch_units):
        episode_number = base_episode_number + i
        # Build episode title with part number if multi-part domain
        if unit["total_parts"] > 1:
            episode_title = f"{unit['domain']} (Part {unit['part']})"
        else:
            episode_title = unit["domain"]
        
        # Check if episode already exists (skip unless force-regenerate)
        if not args.force_regenerate and episode_exists(
            args.certification_id, args.audio_format, episode_number, cosmos_client
        ):
            print(f"\nSkipping episode {episode_number}: {episode_title} (already exists)")
            skipped_episodes.append({"number": episode_number, "title": episode_title})
            continue
        
        try:
            # Prepare episode (content retrieval + narration + SSML)
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
            msg = f"Error preparing '{episode_title}' (episode {episode_number}): {e}"
            print(f"\n{msg}", file=sys.stderr)
            errors.append(msg)
    
    # Phase 2: Synthesize audio in parallel (this is the slow part)
    print(f"\n{'='*60}")
    print(f"PHASE 2: Synthesizing {len(prepared_episodes)} episodes in parallel (max {TTS_MAX_WORKERS} concurrent)")
    print(f"{'='*60}")
    
    synthesized_episodes = []
    with ThreadPoolExecutor(max_workers=TTS_MAX_WORKERS) as executor:
        future_to_episode = {
            executor.submit(
                synthesize_episode_audio,
                ep,
                args.certification_id,
                args.audio_format,
            ): ep
            for ep in prepared_episodes
        }
        
        for future in as_completed(future_to_episode):
            ep = future_to_episode[future]
            try:
                audio_result = future.result()
                ep["audio_result"] = audio_result
                synthesized_episodes.append(ep)
                print(f"  ✓ Episode {ep['episode_number']}: {audio_result['duration_seconds']:.1f}s")
            except Exception as e:
                msg = f"Error synthesizing episode {ep['episode_number']}: {e}"
                print(f"  ✗ {msg}", file=sys.stderr)
                errors.append(msg)
    
    # Phase 3: Upload and save (relatively fast, done sequentially)
    print(f"\n{'='*60}")
    print(f"PHASE 3: Uploading and saving {len(synthesized_episodes)} episodes")
    print(f"{'='*60}")
    
    generated_episodes = []
    for ep in synthesized_episodes:
        try:
            episode_doc = finalize_episode(
                ep,
                args.certification_id,
                args.audio_format,
                cosmos_client,
            )
            generated_episodes.append(episode_doc)
            print(f"  ✓ Saved {episode_doc['id']}")
        except Exception as e:
            msg = f"Error finalizing episode {ep['episode_number']}: {e}"
            print(f"  ✗ {msg}", file=sys.stderr)
            errors.append(msg)

    # Summary
    print(f"\n{'='*60}")
    print(f"BATCH COMPLETE")
    print(f"Generated: {len(generated_episodes)} | Skipped: {len(skipped_episodes)} | Errors: {len(errors)}")
    if skipped_episodes:
        print(f"\nSkipped (already exist):")
        for ep in skipped_episodes:
            print(f"  - Episode {ep['number']}: {ep['title']}")
    if generated_episodes:
        print(f"\nGenerated:")
        for ep in generated_episodes:
            print(f"  - {ep['id']}: {ep['title']} ({ep['durationSeconds']:.0f}s)")
    print(f"{'='*60}")

    if errors:
        print(f"\nBatch failed with {len(errors)} error(s).", file=sys.stderr)
        sys.exit(1)

    # Success if we generated OR skipped episodes (skipped = already done)
    if len(generated_episodes) == 0 and len(skipped_episodes) == 0:
        print("\nBatch produced 0 episodes (unexpected).", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
