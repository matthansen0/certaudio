"""
Unit tests for episode grounding and packing.

Run:  python -m pytest src/functions/test_generation.py -v
"""

from unittest.mock import MagicMock

import pytest

from pipeline import generate_episodes as ge


# ---------------------------------------------------------------------------
# Episode packing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "count,expected",
    [
        (0, []),
        (1, [1]),
        (5, [5]),
        (6, [3, 3]),      # was [5, 1] -- a full episode plus a one-topic stub
        (7, [4, 3]),
        (11, [4, 4, 3]),
        (13, [5, 4, 4]),
    ],
)
def test_topics_are_spread_evenly_across_episodes(count, expected):
    groups = ge._group_topics([f"t{i}" for i in range(count)], 5)
    assert [len(t) for _, t in groups] == expected


def test_grouping_never_drops_or_duplicates_a_topic():
    topics = [f"t{i}" for i in range(23)]
    groups = ge._group_topics(topics, 5)
    assert [t for _, chunk in groups for t in chunk] == topics


def test_start_index_lines_up_with_each_group():
    topics = [f"t{i}" for i in range(11)]
    for start, chunk in ge._group_topics(topics, 5):
        assert topics[start:start + len(chunk)] == chunk


def test_no_group_exceeds_the_target():
    for n in range(1, 60):
        assert all(len(t) <= 5 for _, t in ge._group_topics([f"t{i}" for i in range(n)], 5))


# ---------------------------------------------------------------------------
# Grounding is scoped to the episode's own units
# ---------------------------------------------------------------------------

def _client(*result_sets):
    """A SearchClient returning a different result set per call."""
    client = MagicMock()
    client.search.side_effect = [iter(rs) for rs in result_sets]
    return client


def _docs(n, url="https://learn.microsoft.com/training/modules/a/1-intro/"):
    return [{"title": f"T{i}", "content": f"body {i}", "sourceUrl": url} for i in range(n)]


def _openai():
    client = MagicMock()
    client.embeddings.create.return_value = MagicMock(data=[MagicMock(embedding=[0.1] * 8)])
    return client


URLS = [
    "https://learn.microsoft.com/training/modules/a/1-intro/",
    "https://learn.microsoft.com/training/modules/a/2-build/",
]


# Without this the filter is the whole certification, so an episode competes for
# chunks with every other module and can be narrated from content it is not about.
def test_retrieval_is_filtered_to_the_episodes_own_units():
    client = _client(_docs(5))
    ge.retrieve_content("ai-103", "Domain", ["t1"], client, _openai(), source_urls=URLS)

    flt = client.search.call_args.kwargs["filter"]
    assert "certificationId eq 'ai-103'" in flt
    assert "search.in(sourceUrl, '" + "|".join(URLS) + "', '|')" in flt


def test_without_source_urls_the_filter_stays_certification_wide():
    client = _client(_docs(5))
    ge.retrieve_content("ai-103", "Domain", ["t1"], client, _openai())

    flt = client.search.call_args.kwargs["filter"]
    assert flt == "certificationId eq 'ai-103'"
    assert "search.in" not in flt


def test_a_thin_scoped_result_widens_to_the_certification():
    """An episode indexed before scoping existed would otherwise narrate from nothing."""
    client = _client(_docs(1), _docs(12))
    out = ge.retrieve_content("ai-103", "Domain", ["t1"], client, _openai(), source_urls=URLS)

    assert client.search.call_count == 2
    assert "search.in" in client.search.call_args_list[0].kwargs["filter"]
    assert client.search.call_args_list[1].kwargs["filter"] == "certificationId eq 'ai-103'"
    assert out["content"].count("##") == 12


def test_a_healthy_scoped_result_is_not_widened():
    client = _client(_docs(9))
    ge.retrieve_content("ai-103", "Domain", ["t1"], client, _openai(), source_urls=URLS)
    assert client.search.call_count == 1


# The old code built an escaped cert_filter and then interpolated the raw value
# into the query anyway, so the escaping never applied.
def test_a_quote_in_the_certification_id_is_escaped():
    client = _client(_docs(5))
    ge.retrieve_content("ai'103", "Domain", ["t1"], client, _openai())
    assert client.search.call_args.kwargs["filter"] == "certificationId eq 'ai''103'"


def test_a_quote_in_a_source_url_is_escaped():
    client = _client(_docs(5))
    ge.retrieve_content(
        "ai-103", "Domain", ["t1"], client, _openai(),
        source_urls=["https://learn.microsoft.com/x'y/"],
    )
    assert "'https://learn.microsoft.com/x''y/'" in client.search.call_args.kwargs["filter"]
