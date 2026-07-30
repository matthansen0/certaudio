"""Unit tests for the generation cost model and the index/generate split."""

from unittest.mock import patch

import pytest

from pipeline import cost, orchestrator

DRAGON = "en-US-Andrew:DragonHDLatestNeural"
NEURAL = "en-US-AndrewNeural"


def test_dragon_hd_is_detected():
    assert cost.is_dragon_hd_voice(DRAGON)
    assert not cost.is_dragon_hd_voice(NEURAL)


def test_dragon_hd_estimates_higher_than_standard_neural():
    """Voice choice is the largest lever on cost, so it must move the number."""
    dragon = cost.estimate(50, "instructional", {"instructional": DRAGON})
    neural = cost.estimate(50, "instructional", {"instructional": NEURAL})

    assert dragon["totalUsd"] > neural["totalUsd"]
    assert dragon["ttsRatePerMChar"] == cost.RATES["dragonHdPerMChar"]
    assert neural["ttsRatePerMChar"] == cost.RATES["neuralPerMChar"]


def test_podcast_blends_both_voice_rates():
    blended = cost.blended_tts_rate(
        "podcast", {"podcastHost": DRAGON, "podcastExpert": NEURAL}
    )
    expected = (cost.RATES["dragonHdPerMChar"] + cost.RATES["neuralPerMChar"]) / 2
    assert blended == expected


def test_estimate_scales_linearly_with_episode_count():
    one = cost.estimate(1, "instructional", {"instructional": DRAGON})
    ten = cost.estimate(10, "instructional", {"instructional": DRAGON})
    assert ten["totalUsd"] == pytest.approx(one["totalUsd"] * 10, rel=0.02)


def test_measured_history_is_preferred_over_the_prior():
    prior = cost.estimate(10, "instructional", {"instructional": DRAGON})
    measured = cost.estimate(
        10, "instructional", {"instructional": DRAGON}, measured_chars_per_episode=20000
    )

    assert prior["basis"] == "prior"
    assert measured["basis"] == "measured"
    assert measured["charsPerEpisode"] == 20000
    assert measured["totalUsd"] != prior["totalUsd"]


def test_actual_cost_prices_metered_usage():
    actual = cost.actual_cost(
        tts_chars=1_000_000,
        gpt_input_tokens=1_000_000,
        gpt_output_tokens=0,
        audio_format="instructional",
        voices={"instructional": DRAGON},
    )
    assert actual["ttsCostUsd"] == pytest.approx(cost.RATES["dragonHdPerMChar"])
    assert actual["llmCostUsd"] == pytest.approx(cost.RATES["gptInputPerMTok"])


# ---------------------------------------------------------------------------
# index / generate split
# ---------------------------------------------------------------------------

def test_generate_refuses_to_run_without_an_index():
    """Generation reuses the stored outline; without one it must not re-crawl."""
    with patch.object(orchestrator, "require_environment"), \
            patch.object(orchestrator, "load_discovery", return_value=None), \
            patch.object(orchestrator, "_discover") as discover:
        with pytest.raises(RuntimeError, match="Run an index job first"):
            orchestrator.run_generate("dp-700")

    discover.assert_not_called()


def test_saved_discovery_excludes_unit_bodies():
    """The full dict is ~1 MB of unit text; only the outline may be persisted."""
    discovery = {
        "skillsOutline": [{"name": "Module", "topics": ["a", "b"]}],
        "sourceUrls": ["https://learn.microsoft.com/x"],
        "totalUnits": 2,
        "totalWords": 900,
        "estimatedEpisodes": 1,
        "learningPaths": [{"modules": [{"units": [{"content": "LOTS OF TEXT"}]}]}],
    }

    captured = {}

    class _Blob:
        def upload_blob(self, data, **kwargs):
            captured["data"] = data

    with patch.object(orchestrator, "_discovery_blob", return_value=_Blob()):
        orchestrator._save_discovery("dp-700", discovery, None, 1)

    assert "LOTS OF TEXT" not in captured["data"]
    assert "learningPaths" not in captured["data"]
    assert "skillsOutline" in captured["data"]


def test_runners_expose_all_three_modes():
    assert set(orchestrator.RUNNERS) == {"index", "generate", "refresh"}


# ---------------------------------------------------------------------------
# usage metering
# ---------------------------------------------------------------------------

class _Usage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


def test_usage_accumulates_then_resets():
    cost.reset_usage()
    cost.record_gpt_usage(_Usage(100, 40))
    cost.record_gpt_usage(_Usage(50, 10))
    cost.record_tts_usage(8000)

    snapshot = cost.snapshot_usage()
    assert snapshot == {
        "ttsChars": 8000,
        "gptInputTokens": 150,
        "gptOutputTokens": 50,
    }

    cost.reset_usage()
    assert cost.snapshot_usage()["ttsChars"] == 0


def test_recording_a_missing_usage_block_is_harmless():
    cost.reset_usage()
    cost.record_gpt_usage(None)
    assert cost.snapshot_usage()["gptInputTokens"] == 0


def test_snapshot_does_not_alias_internal_state():
    cost.reset_usage()
    snapshot = cost.snapshot_usage()
    cost.record_tts_usage(500)
    assert snapshot["ttsChars"] == 0
