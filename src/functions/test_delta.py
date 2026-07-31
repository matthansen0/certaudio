"""
Unit tests for content staleness tracking.

Run:  python -m pytest src/functions/test_delta.py -v
"""

from unittest.mock import patch

import pytest

from pipeline import check_content_delta, content_hash, index_content, orchestrator


PAGE = """
<html><body>
  <nav>Home | Docs | Sign in</nav>
  <main><h1>Configure a lakehouse</h1><p>Body text that matters.</p></main>
  <footer>&copy; Microsoft 2026</footer>
</body></html>
"""


# ---------------------------------------------------------------------------
# The two sides must agree
# ---------------------------------------------------------------------------

def test_indexer_and_delta_check_share_one_hash_function():
    """If these ever diverge, every page reads as changed on every refresh and a
    selective refresh silently becomes a full-cost regeneration."""
    assert check_content_delta.compute_content_hash is content_hash.compute_content_hash
    assert index_content.compute_content_hash is content_hash.compute_content_hash


def test_the_hash_the_indexer_stores_matches_what_the_checker_computes():
    with patch.object(index_content.requests, "get") as get:
        get.return_value.text = PAGE
        get.return_value.raise_for_status = lambda: None
        _, indexed = index_content.fetch_and_chunk_content("https://example.invalid/a")

    assert indexed == content_hash.compute_content_hash(PAGE)


def test_chrome_changes_do_not_count_as_content_changes():
    """Microsoft redeploys nav and footers constantly; only the article counts."""
    noisy = PAGE.replace("Home | Docs | Sign in", "Home | Docs | Products | Sign in")
    noisy = noisy.replace("&copy; Microsoft 2026", "&copy; Microsoft 2027")
    assert content_hash.compute_content_hash(noisy) == content_hash.compute_content_hash(PAGE)


def test_article_changes_do_count():
    edited = PAGE.replace("Body text that matters.", "Body text that has been revised.")
    assert content_hash.compute_content_hash(edited) != content_hash.compute_content_hash(PAGE)


def test_a_failed_fetch_yields_no_hash_rather_than_a_wrong_one():
    with patch.object(index_content.requests, "get", side_effect=OSError("boom")):
        chunks, page_hash = index_content.fetch_and_chunk_content("https://example.invalid/a")
    assert chunks == []
    assert page_hash == ""


# ---------------------------------------------------------------------------
# Re-indexing replaces rather than accumulates
# ---------------------------------------------------------------------------

class _SearchClient:
    """Just enough SearchClient to observe what a run writes and deletes."""

    def __init__(self, existing_ids=()):
        self.existing = [{"id": i} for i in existing_ids]
        self.uploaded: list[dict] = []
        self.deleted: list[str] = []

    def search(self, **kwargs):
        return iter(self.existing)

    def upload_documents(self, docs):
        self.uploaded.extend(docs)

    def delete_documents(self, docs):
        self.deleted.extend(d["id"] for d in docs)


def test_existing_ids_are_read_back_for_the_certification():
    client = _SearchClient(["ai-103-aaa-0", "ai-103-bbb-1"])
    assert index_content._existing_document_ids(client, "ai-103") == {
        "ai-103-aaa-0",
        "ai-103-bbb-1",
    }


def test_deletes_are_batched():
    client = _SearchClient()
    index_content._delete_documents(client, [f"doc-{n}" for n in range(1100)])
    assert len(client.deleted) == 1100


# ai-103's syllabus went from 795 units to 237. upload_documents only upserts,
# so without this the dropped 558 keep grounding generation and the chat.
def test_documents_outside_the_new_syllabus_are_deleted():
    client = _SearchClient(["ai-103-old-0", "ai-103-old-1", "ai-103-kept-0"])
    previous = index_content._existing_document_ids(client, "ai-103")
    written = {"ai-103-kept-0", "ai-103-new-0"}

    index_content._delete_documents(client, previous - written)

    assert sorted(client.deleted) == ["ai-103-old-0", "ai-103-old-1"]
    assert "ai-103-kept-0" not in client.deleted


def test_an_odata_quote_cannot_break_out_of_the_filter():
    captured = {}

    class _Capturing(_SearchClient):
        def search(self, **kwargs):
            captured.update(kwargs)
            return iter([])

    index_content._existing_document_ids(_Capturing(), "ai'103")
    assert captured["filter"] == "certificationId eq 'ai''103'"


# ---------------------------------------------------------------------------
# Baseline semantics
# ---------------------------------------------------------------------------

def _delta(sources, live_hash):
    container = _Container(sources)
    with patch.object(check_content_delta, "CosmosClient"), \
            patch.object(check_content_delta, "DefaultAzureCredential"), \
            patch.object(check_content_delta, "fetch_page_content", return_value="<html/>"), \
            patch.object(check_content_delta, "compute_content_hash", return_value=live_hash):
        check_content_delta.CosmosClient.return_value \
            .get_database_client.return_value.get_container_client.return_value = container
        result = check_content_delta.check_content_delta("dp-700", "https://cosmos.invalid")
    return result, container


class _Container:
    def __init__(self, items):
        self.items = items
        self.upserted = []

    def query_items(self, **kwargs):
        return iter(self.items)

    def upsert_item(self, item):
        self.upserted.append(dict(item))


def test_staleness_is_measured_against_what_the_audio_was_generated_from():
    """indexedHash moves on every index run; using it would call stale audio fresh."""
    sources = [{
        "id": "s1", "url": "https://learn.microsoft.com/a",
        "contentHash": "generated-from-this",
        "indexedHash": "reindexed-since",
        "episodeRefs": ["dp-700-instructional-003"],
    }]
    result, _ = _delta(sources, live_hash="reindexed-since")

    assert result.has_updates, "re-indexing must not mark stale episodes as fresh"
    assert result.changed_sources[0].affected_episodes == ["dp-700-instructional-003"]


def test_unchanged_content_reports_no_updates():
    sources = [{
        "id": "s1", "url": "https://learn.microsoft.com/a",
        "contentHash": "same", "episodeRefs": [],
    }]
    result, _ = _delta(sources, live_hash="same")
    assert not result.has_updates
    assert result.unchanged_count == 1


def test_the_baseline_is_not_advanced_by_the_check_itself():
    """A run that fails after the check must leave the change outstanding."""
    sources = [{
        "id": "s1", "url": "https://learn.microsoft.com/a",
        "contentHash": "old", "episodeRefs": [],
    }]
    result, container = _delta(sources, live_hash="new")

    assert result.has_updates
    assert container.upserted, "lastChecked should still be recorded"
    assert container.upserted[0]["contentHash"] == "old", \
        "only save_episode may move the generation baseline"


# ---------------------------------------------------------------------------
# Read-only update check
# ---------------------------------------------------------------------------

def test_check_updates_lists_stale_episodes_without_generating():
    sources = [
        {"url": "https://learn.microsoft.com/a", "contentHash": "old",
         "episodeRefs": ["dp-700-instructional-001"]},
        {"url": "https://learn.microsoft.com/b", "contentHash": "current",
         "episodeRefs": ["dp-700-instructional-002"]},
        # Indexed but never generated from: not stale, just untracked.
        {"url": "https://learn.microsoft.com/c", "contentHash": "", "episodeRefs": []},
    ]
    hashes = {"https://learn.microsoft.com/a": "changed",
              "https://learn.microsoft.com/b": "current"}

    with patch("pipeline.source_store.list_sources", return_value=sources), \
            patch("pipeline.content_hash.fetch_page_content", side_effect=lambda u: u), \
            patch("pipeline.content_hash.compute_content_hash", side_effect=lambda u: hashes[u]):
        report = orchestrator.check_updates("dp-700")

    assert report["staleEpisodes"] == ["dp-700-instructional-001"]
    assert report["changedSources"] == 1
    assert report["unchangedSources"] == 1
    assert report["untracked"] == 1


def test_check_updates_survives_an_unreachable_page():
    sources = [{"url": "https://learn.microsoft.com/a", "contentHash": "old", "episodeRefs": []}]
    with patch("pipeline.source_store.list_sources", return_value=sources), \
            patch("pipeline.content_hash.fetch_page_content", side_effect=OSError("boom")):
        report = orchestrator.check_updates("dp-700")

    assert report["errors"] == 1
    assert report["staleEpisodes"] == []
