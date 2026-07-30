"""Cost model for content generation.

Deliberately dependency-free so the HTTP API can import it without pulling in
the OpenAI and Speech SDKs. Rates are served to the admin portal rather than
duplicated in JavaScript, so the browser preview and the figure recorded
against a job cannot drift apart.
"""

# List prices in USD per 1,000,000 units.
RATES = {
    "gptInputPerMTok": 2.50,
    "gptOutputPerMTok": 10.00,
    "embeddingPerMTok": 0.13,
    "dragonHdPerMChar": 22.00,
    "neuralPerMChar": 15.00,
}

# An episode targets ~1400 narrated words; MIN_WORDS_PER_PART defaults to 1200.
DEFAULT_WORDS_PER_EPISODE = 1400
CHARS_PER_WORD = 6.1

# Narration, quality check and SSML conversion together, per episode.
GPT_INPUT_TOKENS_PER_EPISODE = 9000
GPT_OUTPUT_TOKENS_PER_EPISODE = 4200


def is_dragon_hd_voice(voice_name: str) -> bool:
    """Dragon HD voices cost more per character and need simplified SSML."""
    return "DragonHD" in voice_name or ":Dragon" in voice_name


def _tts_rate_per_mchar(voice_name: str) -> float:
    if is_dragon_hd_voice(voice_name):
        return RATES["dragonHdPerMChar"]
    return RATES["neuralPerMChar"]


def blended_tts_rate(audio_format: str, voices: dict) -> float:
    """Podcast splits narration across two voices, so blend their rates evenly."""
    if audio_format == "podcast":
        host = _tts_rate_per_mchar(voices.get("podcastHost", ""))
        expert = _tts_rate_per_mchar(voices.get("podcastExpert", ""))
        return (host + expert) / 2
    return _tts_rate_per_mchar(voices.get("instructional", ""))


def estimate(
    episode_count: int,
    audio_format: str,
    voices: dict,
    words_per_episode: float = DEFAULT_WORDS_PER_EPISODE,
    measured_chars_per_episode: float | None = None,
) -> dict:
    """Estimate the USD cost of generating `episode_count` episodes.

    Episode count is exact (it comes from the discovery outline); only the
    per-episode size is modelled, so prefer a measured figure when one exists.
    """
    if measured_chars_per_episode and measured_chars_per_episode > 0:
        chars_per_episode = float(measured_chars_per_episode)
        basis = "measured"
    else:
        chars_per_episode = words_per_episode * CHARS_PER_WORD
        basis = "prior"

    tts_rate = blended_tts_rate(audio_format, voices)
    total_chars = episode_count * chars_per_episode
    tts_cost = total_chars * tts_rate / 1_000_000

    gpt_cost = episode_count * (
        GPT_INPUT_TOKENS_PER_EPISODE * RATES["gptInputPerMTok"]
        + GPT_OUTPUT_TOKENS_PER_EPISODE * RATES["gptOutputPerMTok"]
    ) / 1_000_000

    return {
        "episodeCount": episode_count,
        "basis": basis,
        "charsPerEpisode": round(chars_per_episode),
        "ttsRatePerMChar": tts_rate,
        "ttsCostUsd": round(tts_cost, 2),
        "llmCostUsd": round(gpt_cost, 2),
        "totalUsd": round(tts_cost + gpt_cost, 2),
    }


def actual_cost(tts_chars: int, gpt_input_tokens: int, gpt_output_tokens: int,
                audio_format: str, voices: dict) -> dict:
    """Price metered usage from a completed run."""
    tts_rate = blended_tts_rate(audio_format, voices)
    tts_cost = tts_chars * tts_rate / 1_000_000
    gpt_cost = (
        gpt_input_tokens * RATES["gptInputPerMTok"]
        + gpt_output_tokens * RATES["gptOutputPerMTok"]
    ) / 1_000_000
    return {
        "ttsChars": tts_chars,
        "gptInputTokens": gpt_input_tokens,
        "gptOutputTokens": gpt_output_tokens,
        "ttsCostUsd": round(tts_cost, 2),
        "llmCostUsd": round(gpt_cost, 2),
        "totalUsd": round(tts_cost + gpt_cost, 2),
    }


# Process-global because host.json pins the queue to batchSize 1, so exactly one
# job runs in this worker at a time.
_usage = {"ttsChars": 0, "gptInputTokens": 0, "gptOutputTokens": 0}


def reset_usage() -> None:
    for key in _usage:
        _usage[key] = 0


def record_gpt_usage(usage) -> None:
    """Accumulate token counts from an OpenAI response's `usage` block."""
    if not usage:
        return
    _usage["gptInputTokens"] += getattr(usage, "prompt_tokens", 0) or 0
    _usage["gptOutputTokens"] += getattr(usage, "completion_tokens", 0) or 0


def record_tts_usage(character_count: int) -> None:
    _usage["ttsChars"] += max(0, character_count)


def snapshot_usage() -> dict:
    return dict(_usage)
