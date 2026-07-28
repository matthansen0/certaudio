"""In-process orchestration for content generation and refresh.

This runs inside the Functions queue trigger rather than in GitHub Actions,
because GitHub-hosted runners cannot reach the private data plane. Batches run
sequentially instead of as a workflow matrix, and progress is reported through
a callback so the admin portal can surface it.
"""

import math
import os
import re
from typing import Callable, Optional

from . import deep_discover
from .check_content_delta import check_content_delta, get_affected_episodes
from .generate_episodes import SHARED_SEARCH_INDEX, run_generation
from .generate_index import generate_index
from .index_content import index_content

REQUIRED_ENV = (
    "OPENAI_ENDPOINT",
    "SPEECH_ENDPOINT",
    "SPEECH_REGION",
    "SEARCH_ENDPOINT",
    "COSMOS_DB_ENDPOINT",
    "STORAGE_ACCOUNT_NAME",
)

DEFAULT_BATCH_SIZE = 10
DEFAULT_TOPICS_PER_EPISODE = 5

# progress(phase, current, total, message)
ProgressFn = Callable[[str, int, int, str], None]


def _noop_progress(phase: str, current: int, total: int, message: str) -> None:
    print(f"[{phase}] {current}/{total} {message}")


def require_environment() -> None:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )


def episode_unit_count(skills: list[dict], topics_per_episode: int = DEFAULT_TOPICS_PER_EPISODE) -> int:
    """Count deterministic episode units in a discovery outline."""
    return sum(
        math.ceil(len(skill.get("topics", [])) / topics_per_episode)
        for skill in skills
        if isinstance(skill, dict) and skill.get("topics")
    )


def affected_batch_indices(
    episode_ids: list[str],
    certification_id: str,
    audio_format: str,
    batch_size: int,
    total_batches: int,
) -> list[int]:
    """Map stored episode IDs back to the batches that own them."""
    pattern = re.compile(
        rf"^{re.escape(certification_id)}-{re.escape(audio_format)}-(\d+)$"
    )
    batches = set()
    for episode_id in episode_ids:
        match = pattern.match(episode_id)
        if not match:
            continue
        episode_number = int(match.group(1))
        batch_index = (episode_number - 1) // batch_size
        if episode_number > 0 and batch_index < total_batches:
            batches.add(batch_index)
    return sorted(batches)


def _discover(certification_id: str, progress: ProgressFn) -> dict:
    progress("discover", 0, 1, f"Discovering content for {certification_id}")

    if certification_id == "test":
        result = deep_discover.discover_test_content()
        discovery = deep_discover.result_to_dict(result)
    else:
        catalog = deep_discover.fetch_catalog()
        result = deep_discover.deep_discover(
            certification_id=certification_id,
            catalog=catalog,
        )
        exam_skills = deep_discover.fetch_exam_skills_outline(certification_id)
        discovery = deep_discover.result_to_dict(result, exam_skills)

    if not isinstance(discovery.get("skillsOutline"), list):
        raise RuntimeError("Discovery did not produce a skillsOutline array")
    if not isinstance(discovery.get("sourceUrls"), list):
        raise RuntimeError("Discovery did not produce a sourceUrls array")
    if not discovery["sourceUrls"]:
        raise RuntimeError("Discovery did not produce any source URLs")

    progress(
        "discover",
        1,
        1,
        f"Discovered {len(discovery['skillsOutline'])} domains, "
        f"{len(discovery['sourceUrls'])} sources",
    )
    return discovery


def _index(certification_id: str, source_urls: list[str], progress: ProgressFn) -> None:
    """Index grounding content into the shared AI Search index.

    A single index tagged by certificationId is used rather than one index per
    certification: retrieval filters on the tag, and the Study Partner reads the
    same content instead of maintaining a duplicate copy.
    """
    progress("index", 0, 1, f"Indexing {len(source_urls)} sources")
    index_content(
        certification_id=certification_id,
        source_urls=source_urls,
        search_endpoint=os.environ["SEARCH_ENDPOINT"],
        openai_endpoint=os.environ["OPENAI_ENDPOINT"],
        update_mode=True,
        index_name=SHARED_SEARCH_INDEX,
    )
    progress("index", 1, 1, "Indexing complete")


def _run_batches(
    certification_id: str,
    audio_format: str,
    skills: list[dict],
    batch_indices: list[int],
    voices: dict,
    batch_size: int,
    topics_per_episode: int,
    force_regenerate: bool,
    progress: ProgressFn,
) -> dict:
    generated: list[dict] = []
    skipped: list[dict] = []
    total = len(batch_indices)

    for position, batch_index in enumerate(batch_indices, start=1):
        progress(
            "generate",
            position - 1,
            total,
            f"Starting batch {position} of {total}",
        )
        result = run_generation(
            certification_id=certification_id,
            skills=skills,
            audio_format=audio_format,
            instructional_voice=voices["instructional"],
            podcast_host_voice=voices["podcastHost"],
            podcast_expert_voice=voices["podcastExpert"],
            batch_index=batch_index,
            batch_size=batch_size,
            topics_per_episode=topics_per_episode,
            force_regenerate=force_regenerate,
        )
        generated.extend(result["generated"])
        skipped.extend(result["skipped"])
        progress(
            "generate",
            position,
            total,
            f"Batch {position} complete ({len(result['generated'])} generated)",
        )

    return {"generated": generated, "skipped": skipped}


def _publish(certification_id: str, audio_format: str, minimum_episodes: int, progress: ProgressFn) -> dict:
    progress("publish", 0, 1, "Publishing episode index")
    index_data = generate_index(
        certification_id=certification_id,
        audio_format=audio_format,
        cosmos_endpoint=os.environ["COSMOS_DB_ENDPOINT"],
        storage_account_name=os.environ["STORAGE_ACCOUNT_NAME"],
        database_name=os.environ.get("COSMOS_DB_DATABASE", "certaudio"),
        min_episodes=minimum_episodes,
    )
    progress("publish", 1, 1, "Episode index published")
    return index_data


def _voices(overrides: Optional[dict]) -> dict:
    overrides = overrides or {}
    return {
        "instructional": overrides.get("instructional")
        or "en-US-Andrew:DragonHDLatestNeural",
        "podcastHost": overrides.get("podcastHost")
        or "en-US-Ava:DragonHDLatestNeural",
        "podcastExpert": overrides.get("podcastExpert")
        or "en-US-Andrew:DragonHDLatestNeural",
    }


def run_generate(
    certification_id: str,
    audio_format: str = "instructional",
    voices: Optional[dict] = None,
    force: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    topics_per_episode: int = DEFAULT_TOPICS_PER_EPISODE,
    progress: Optional[ProgressFn] = None,
) -> dict:
    """Full generation: discover, index, generate every batch, publish."""
    require_environment()
    progress = progress or _noop_progress
    resolved_voices = _voices(voices)

    discovery = _discover(certification_id, progress)
    _index(certification_id, discovery["sourceUrls"], progress)

    unit_count = episode_unit_count(discovery["skillsOutline"], topics_per_episode)
    if not unit_count:
        raise RuntimeError("Discovery produced zero episode units")
    batch_count = math.ceil(unit_count / batch_size)

    outcome = _run_batches(
        certification_id,
        audio_format,
        discovery["skillsOutline"],
        list(range(batch_count)),
        resolved_voices,
        batch_size,
        topics_per_episode,
        force,
        progress,
    )
    index_data = _publish(certification_id, audio_format, unit_count, progress)

    return {
        "mode": "generate",
        "certificationId": certification_id,
        "audioFormat": audio_format,
        "episodesGenerated": len(outcome["generated"]),
        "episodesSkipped": len(outcome["skipped"]),
        "totalEpisodes": index_data.get("totalEpisodes", 0),
        "totalDurationMinutes": index_data.get("totalDurationMinutes", 0),
    }


def run_refresh(
    certification_id: str,
    audio_format: str = "instructional",
    voices: Optional[dict] = None,
    force: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    topics_per_episode: int = DEFAULT_TOPICS_PER_EPISODE,
    progress: Optional[ProgressFn] = None,
) -> dict:
    """Selective refresh: regenerate only batches affected by changed sources."""
    require_environment()
    progress = progress or _noop_progress
    resolved_voices = _voices(voices)

    progress("discover", 0, 1, "Checking for upstream content changes")
    delta = check_content_delta(
        certification_id=certification_id,
        cosmos_endpoint=os.environ["COSMOS_DB_ENDPOINT"],
        force_refresh=force,
        database_name=os.environ.get("COSMOS_DB_DATABASE", "certaudio"),
    )

    if not delta.has_updates and not force:
        progress("publish", 1, 1, "No content updates found")
        return {
            "mode": "refresh",
            "certificationId": certification_id,
            "audioFormat": audio_format,
            "episodesGenerated": 0,
            "episodesSkipped": 0,
            "message": "No content updates found",
        }

    discovery = _discover(certification_id, progress)
    _index(certification_id, discovery["sourceUrls"], progress)

    unit_count = episode_unit_count(discovery["skillsOutline"], topics_per_episode)
    if not unit_count:
        raise RuntimeError("Discovery produced zero episode units")
    batch_count = math.ceil(unit_count / batch_size)

    batch_indices = affected_batch_indices(
        get_affected_episodes(delta.changed_sources),
        certification_id,
        audio_format,
        batch_size,
        batch_count,
    )
    if force or not batch_indices:
        batch_indices = list(range(batch_count))

    outcome = _run_batches(
        certification_id,
        audio_format,
        discovery["skillsOutline"],
        batch_indices,
        resolved_voices,
        batch_size,
        topics_per_episode,
        True,
        progress,
    )
    index_data = _publish(certification_id, audio_format, unit_count, progress)

    return {
        "mode": "refresh",
        "certificationId": certification_id,
        "audioFormat": audio_format,
        "batchesRefreshed": len(batch_indices),
        "episodesGenerated": len(outcome["generated"]),
        "episodesSkipped": len(outcome["skipped"]),
        "totalEpisodes": index_data.get("totalEpisodes", 0),
    }


RUNNERS = {"generate": run_generate, "refresh": run_refresh}
