"""
Unit tests for catalog-driven content discovery.

Run:  python -m pytest src/functions/test_discovery.py -v
"""

import pytest

from pipeline import deep_discover, orchestrator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _catalog() -> dict:
    """A miniature Learn catalog with the shapes the real API returns."""
    return {
        "learningPaths": [
            {
                "uid": "lp.curated-one",
                "title": "Curated one",
                "roles": ["data-engineer"],
                "products": ["fabric"],
                "modules": ["mod.a"],
            },
            {
                "uid": "lp.curated-two",
                "title": "Curated two",
                "roles": ["data-engineer"],
                "products": ["fabric"],
                "modules": ["mod.b"],
            },
            {
                "uid": "lp.tagged-only",
                "title": "Tagged but not in the study guide",
                "roles": ["data-engineer"],
                "products": ["power-bi"],
                "modules": ["mod.c"],
            },
            {
                "uid": "lp.unrelated",
                "title": "Unrelated",
                "roles": ["business-user"],
                "products": ["office-word"],
                "modules": [],
            },
        ],
        "modules": [
            {"uid": "mod.a", "title": "Module A", "units": ["u.a1"]},
            {"uid": "mod.b", "title": "Module B", "units": ["u.b1"]},
            {"uid": "mod.c", "title": "Module C", "units": ["u.c1"]},
            {"uid": "mod.standalone", "title": "Standalone module", "units": ["u.s1"]},
        ],
        "units": [
            {"uid": "u.a1", "title": "Unit A1"},
            {"uid": "u.b1", "title": "Unit B1"},
            {"uid": "u.c1", "title": "Unit C1"},
            {"uid": "u.s1", "title": "Unit S1"},
        ],
        "exams": [
            {
                "uid": "exam.dp-700",
                "display_name": "DP-700",
                "title": "Implementing Data Engineering Solutions",
                "url": "https://learn.microsoft.com/credentials/certifications/exams/dp-700/",
                "pdf_download_url": "https://example.invalid/dp-700.pdf",
                "roles": ["data-engineer"],
                "products": ["fabric", "power-bi"],
                "study_guide": [
                    {"uid": "lp.curated-one", "type": "learningPath"},
                    {"uid": "mod.standalone", "type": "module"},
                ],
            },
            {
                "uid": "exam.zz-000",
                "display_name": "ZZ-000",
                "title": "Exam with no study content",
                "roles": [],
                "products": [],
                "study_guide": [],
            },
        ],
        "mergedCertifications": [
            {
                "uid": "certification.fabric-data-engineer-associate",
                "url": "https://learn.microsoft.com/en-us/credentials/certifications/fabric-data-engineer-associate/?WT.mc_id=api_CatalogApi",
                "roles": ["data-engineer"],
                "products": ["fabric"],
                "skills": [
                    "Ingest and transform data",
                    "Monitor and optimize an analytics solution",
                ],
                "study_guide": [{"uid": "lp.curated-two", "type": "learningPath"}],
            }
        ],
        "certifications": [],
    }


SLUG = "fabric-data-engineer-associate"


def _result(paths=(), units=0, words=0, failed=0, warnings=()):
    return deep_discover.DeepDiscoveryResult(
        certification_id="dp-700",
        certification_url="",
        learning_paths=list(paths),
        total_modules=len(paths),
        total_units=units,
        total_words=words,
        estimated_episodes=1,
        units_failed=failed,
        resolution={"warnings": list(warnings)},
    )


# ---------------------------------------------------------------------------
# Certification resolution
# ---------------------------------------------------------------------------

def test_resolves_an_exam_by_uid():
    ref = deep_discover.resolve_certification("dp-700", _catalog(), slug=SLUG)
    assert ref is not None
    assert ref.exam_uid == "exam.dp-700"
    assert ref.skills_pdf_url == "https://example.invalid/dp-700.pdf"


def test_resolves_an_exam_by_display_name():
    catalog = _catalog()
    catalog["exams"][0]["uid"] = "exam.something-else"
    ref = deep_discover.resolve_certification("DP-700", catalog, slug="")
    assert ref is not None
    assert ref.title.startswith("Implementing")


def test_a_modern_cert_resolves_from_the_slug_alone():
    """az-104, dp-700 and friends have no record in the catalog's exams array."""
    catalog = _catalog()
    catalog["exams"] = []
    ref = deep_discover.resolve_certification("dp-700", catalog, slug=SLUG)
    assert ref is not None
    assert ref.exam_uid == ""
    assert "lp.curated-two" in ref.study_guide_paths
    assert ref.skills_measured


def test_unknown_exam_resolves_to_none():
    """Cheapest possible rejection of a certification ID that does not exist."""
    assert deep_discover.resolve_certification("zz-999", _catalog(), slug="") is None


def test_study_guide_splits_paths_from_standalone_modules():
    ref = deep_discover.resolve_certification("dp-700", _catalog(), slug=SLUG)
    assert "lp.curated-one" in ref.study_guide_paths
    assert ref.study_guide_modules == ["mod.standalone"]


def test_certification_record_contributes_skills_and_paths():
    """Single-exam certs live in mergedCertifications, which has no exams field."""
    ref = deep_discover.resolve_certification("dp-700", _catalog(), slug=SLUG)
    assert "lp.curated-two" in ref.study_guide_paths
    assert "Ingest and transform data" in ref.skills_measured


def test_roles_and_products_come_from_the_catalog():
    """This is what removes the six-exam limit: every exam carries its own tags."""
    ref = deep_discover.resolve_certification("dp-700", _catalog(), slug=SLUG)
    assert "data-engineer" in ref.roles
    assert "fabric" in ref.products


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/en-us/credentials/certifications/azure-administrator/", "azure-administrator"),
        ("/en-us/credentials/certifications/exams/az-102/", ""),
        ("/en-us/credentials/certifications/resources/study-guides/az-104", ""),
        ("/en-us/training/", ""),
    ],
)
def test_slug_extraction(path, expected, monkeypatch):
    class _Resp:
        status_code = 200
        url = f"https://learn.microsoft.com{path}"

    monkeypatch.setattr(deep_discover.requests, "get", lambda *a, **k: _Resp())
    assert deep_discover.resolve_certification_slug("az-104") == expected


def test_slug_lookup_survives_a_network_failure(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("no route to host")

    monkeypatch.setattr(deep_discover.requests, "get", _boom)
    assert deep_discover.resolve_certification_slug("az-104") == ""


# ---------------------------------------------------------------------------
# Content source resolution
# ---------------------------------------------------------------------------

def test_the_study_guide_wins_over_tag_matching():
    """Tag matching is broad by nature: role+product alone matches 116 paths for
    az-104, most of them off-syllabus. It only runs when nothing curated exists.
    """
    paths, modules, report = deep_discover.resolve_content_sources(
        "dp-700", _catalog(), slug=SLUG
    )
    assert set(paths) == {"lp.curated-one", "lp.curated-two"}
    assert modules == ["mod.standalone"]
    assert report["sources"]["studyGuide"]["paths"] == 2
    assert "tagFilter" not in report["sources"]


def test_tag_matching_is_capped(monkeypatch):
    monkeypatch.setattr(deep_discover, "MAX_TAG_FILTER_PATHS", 1)
    catalog = _catalog()
    catalog["exams"][0]["study_guide"] = []
    catalog["mergedCertifications"][0]["study_guide"] = []
    paths, _, report = deep_discover.resolve_content_sources(
        "dp-700", catalog, slug=SLUG
    )
    assert len(paths) == 1
    assert report["sources"]["tagFilter"]["matchedBeforeCap"] > 1
    assert any("capped" in w for w in report["warnings"])


def test_unrelated_paths_are_not_pulled_in():
    catalog = _catalog()
    catalog["exams"][0]["study_guide"] = []
    catalog["mergedCertifications"][0]["study_guide"] = []
    paths, _, _ = deep_discover.resolve_content_sources("dp-700", catalog, slug=SLUG)
    assert paths
    assert "lp.unrelated" not in paths


def test_an_exam_with_no_curated_mapping_still_resolves():
    """The old code returned zero paths for anything outside a six-exam list.

    Nothing about this exam appears in CERTIFICATION_ROLE_PRODUCTS or
    CERTIFICATION_PATH_UIDS; resolution has to come from the catalog alone.
    """
    catalog = _catalog()
    catalog["exams"][0]["uid"] = "exam.xy-123"
    catalog["exams"][0]["display_name"] = "XY-123"

    assert "xy-123" not in deep_discover.CERTIFICATION_ROLE_PRODUCTS
    assert "xy-123" not in deep_discover.CERTIFICATION_PATH_UIDS

    paths, modules, report = deep_discover.resolve_content_sources(
        "xy-123", catalog, slug=SLUG
    )
    assert paths
    assert modules == ["mod.standalone"]
    assert report["examFound"] is True

def test_stale_uids_are_dropped_and_reported():
    catalog = _catalog()
    catalog["exams"][0]["study_guide"].append(
        {"uid": "lp.deleted-by-microsoft", "type": "learningPath"}
    )
    paths, _, report = deep_discover.resolve_content_sources(
        "dp-700", catalog, slug=SLUG
    )
    assert "lp.deleted-by-microsoft" not in paths
    assert any("no longer in the catalog" in w for w in report["warnings"])


def test_a_fully_stale_tier_falls_through_instead_of_resolving_to_nothing():
    """az-104's curated UIDs have all been restructured away by Microsoft.

    Validating after tier selection produced an empty syllabus and a hard
    failure; validating per tier lets the next one take over.
    """
    catalog = _catalog()
    catalog["exams"][0]["study_guide"] = []
    catalog["mergedCertifications"][0]["study_guide"] = []
    deep_discover.CERTIFICATION_PATH_UIDS["dp-700"] = ["lp.renamed-away"]
    try:
        paths, _, report = deep_discover.resolve_content_sources(
            "dp-700", catalog, slug=SLUG
        )
        assert paths, "should have fallen through to tag matching"
        assert "curatedUids" not in report["sources"]
        assert "tagFilter" in report["sources"]
    finally:
        del deep_discover.CERTIFICATION_PATH_UIDS["dp-700"]


def test_missing_exam_is_reported_rather_than_guessed():
    _, _, report = deep_discover.resolve_content_sources("zz-999", _catalog(), slug="")
    assert report["examFound"] is False
    assert report["resolvedPaths"] == 0
    assert any("zz-999" in w for w in report["warnings"])


def test_exam_with_empty_study_guide_falls_back_to_curated_uids():
    catalog = _catalog()
    catalog["exams"] = [catalog["exams"][1]]
    catalog["mergedCertifications"] = []
    deep_discover.CERTIFICATION_PATH_UIDS["zz-000"] = ["lp.curated-one"]
    try:
        paths, _, report = deep_discover.resolve_content_sources(
            "zz-000", catalog, slug=""
        )
        assert paths == ["lp.curated-one"]
        assert report["sources"]["curatedUids"]["paths"] == 1
    finally:
        del deep_discover.CERTIFICATION_PATH_UIDS["zz-000"]


# ---------------------------------------------------------------------------
# Exam skills
# ---------------------------------------------------------------------------

def test_catalog_skills_become_checkable_topics():
    ref = deep_discover.resolve_certification("dp-700", _catalog(), slug=SLUG)
    skills = deep_discover.exam_skills_from_catalog(ref)
    assert [s["name"] for s in skills] == ref.skills_measured
    assert all(s["topics"] for s in skills), "coverage_sweep iterates topics"


def test_merge_prefers_the_scraped_outline_on_overlap():
    """The scrape carries weights and sub-bullets; the catalog list does not."""
    scraped = [
        {
            "name": "Ingest and transform data",
            "weight": "30-35%",
            "topics": ["Ingest by using a pipeline", "Transform with dataflows"],
            "sourceUrls": [],
            "isExamSkill": True,
        }
    ]
    catalog_skills = deep_discover.exam_skills_from_catalog(
        deep_discover.resolve_certification("dp-700", _catalog(), slug=SLUG)
    )
    merged = deep_discover.merge_exam_skills(scraped, catalog_skills)
    assert merged[0]["weight"] == "30-35%"
    assert len(merged[0]["topics"]) == 2
    names = [s["name"] for s in merged]
    assert "Monitor and optimize an analytics solution" in names


def test_merge_survives_an_empty_scrape():
    catalog_skills = deep_discover.exam_skills_from_catalog(
        deep_discover.resolve_certification("dp-700", _catalog(), slug=SLUG)
    )
    assert len(deep_discover.merge_exam_skills([], catalog_skills)) == 2


# ---------------------------------------------------------------------------
# Fail-fast gates
# ---------------------------------------------------------------------------

def _path_with_content():
    unit = deep_discover.Unit(
        uid="u.a1", title="Unit A1", url="https://example.invalid", duration_minutes=5
    )
    module = deep_discover.Module(
        uid="mod.a", title="Module A", url="", duration_minutes=5, description="",
        units=[unit],
    )
    return deep_discover.LearningPath(
        uid="lp.a", title="Path A", url="", duration_minutes=5, description="",
        modules=[module],
    )


def test_zero_learning_paths_is_an_error_not_an_empty_success():
    with pytest.raises(RuntimeError, match="No learning paths resolved"):
        orchestrator._assert_discovery_is_usable(
            "dp-700", _result(warnings=["nothing matched"]), [], {}
        )


def test_mostly_failed_downloads_is_an_error():
    """An outline with no content behind it used to be reported as success."""
    result = _result(paths=[_path_with_content()], units=100, words=500, failed=60)
    with pytest.raises(RuntimeError, match="failed to download"):
        orchestrator._assert_discovery_is_usable("dp-700", result, [], {})


def test_a_few_failed_downloads_is_tolerated():
    result = _result(paths=[_path_with_content()], units=100, words=50000, failed=5)
    orchestrator._assert_discovery_is_usable("dp-700", result, [{"topics": ["x"]}], {})


def test_units_without_any_text_is_an_error():
    result = _result(paths=[_path_with_content()], units=100, words=0, failed=0)
    with pytest.raises(RuntimeError, match="no text"):
        orchestrator._assert_discovery_is_usable("dp-700", result, [], {})


def test_missing_exam_skills_warns_but_does_not_fail(capsys):
    result = _result(paths=[_path_with_content()], units=10, words=5000)
    orchestrator._assert_discovery_is_usable("dp-700", result, [], {})
    assert "coverage could not be verified" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def test_discovery_report_summarises_gaps_and_coverage():
    report = orchestrator.discovery_report(
        {
            "totalUnits": 120,
            "unitsFailed": 3,
            "resolution": {
                "examFound": True,
                "examTitle": "Implementing Data Engineering Solutions",
                "resolvedPaths": 6,
                "resolvedStandaloneModules": 1,
                "sources": {"studyGuide": {"paths": 5}},
                "warnings": [],
            },
            "confidence": {"grade": "B", "overallScore": 84.2},
            "coverageReport": {
                "coveredCount": 40,
                "supplementedCount": 6,
                "gapCount": 2,
                "gaps": [{"skill": "s", "topic": "t"}],
            },
        }
    )
    assert report["coverageGrade"] == "B"
    assert report["unitsFailed"] == 3
    assert report["topicsUncovered"] == 2
    assert report["gaps"] == [{"skill": "s", "topic": "t"}]


def test_discovery_report_tolerates_a_bare_result():
    assert orchestrator.discovery_report({})["coverageGrade"] == ""


# ---------------------------------------------------------------------------
# Exam skills outline
# ---------------------------------------------------------------------------

STUDY_GUIDE_HTML = """
<h3>Design identity, governance, and monitoring solutions (25-30%)</h3>
<ul><li>Design a solution for logging and monitoring</li>
    <li>Design authentication and authorization solutions</li></ul>
"""


def _outline_pages(monkeypatch, pages):
    """Serve canned HTML per URL and record the order pages were requested in."""
    requested = []

    def _fetch(url, *a, **k):
        requested.append(url)
        if url not in pages:
            raise RuntimeError(f"404 {url}")
        return pages[url]

    monkeypatch.setattr(deep_discover, "fetch_page", _fetch)
    return requested


# An exam landing page parses to zero skills, which used to surface as grade F
# with 0% coverage on a discovery that had in fact worked.
def test_exam_url_falls_back_to_the_canonical_study_guide(monkeypatch):
    exam_url = "https://learn.microsoft.com/en-us/credentials/certifications/exams/az-305/"
    canonical = deep_discover.study_guide_url_for("az-305")
    requested = _outline_pages(
        monkeypatch, {exam_url: "<h2>Exam AZ-305</h2>", canonical: STUDY_GUIDE_HTML}
    )

    skills = deep_discover.fetch_exam_skills_outline("az-305", study_guide_url=exam_url)

    assert requested == [exam_url, canonical]
    assert sum(len(s["topics"]) for s in skills) == 2


def test_a_usable_supplied_study_guide_is_not_refetched(monkeypatch):
    supplied = "https://learn.microsoft.com/en-us/credentials/certifications/resources/study-guides/custom"
    requested = _outline_pages(monkeypatch, {supplied: STUDY_GUIDE_HTML})

    assert deep_discover.fetch_exam_skills_outline("az-305", study_guide_url=supplied)
    assert requested == [supplied]


def test_the_canonical_url_is_tried_once_when_supplied(monkeypatch):
    canonical = deep_discover.study_guide_url_for("az-305")
    requested = _outline_pages(monkeypatch, {})

    assert deep_discover.fetch_exam_skills_outline("az-305", study_guide_url=canonical) == []
    assert requested == [canonical]


def test_unmeasurable_coverage_is_not_a_failing_grade():
    empty = {"covered": [], "supplemented": [], "gaps": []}
    assert deep_discover.compute_confidence_score(empty, [])["grade"] == "n/a"


def test_a_real_gap_still_grades_f():
    coverage = {"covered": [], "supplemented": [], "gaps": [{"skill": "s", "topic": "t"}]}
    score = deep_discover.compute_confidence_score(coverage, [{"name": "s"}])
    assert score["grade"] == "F"
    assert score["overallScore"] == 0.0


def _catalog_skills(*names):
    return [
        {"name": n, "weight": None, "topics": [n], "sourceUrls": [], "isExamSkill": True}
        for n in names
    ]


# ai-103's five skills collapsed to two: sibling domains share "Implement ...
# solutions", which clears the 0.45 fuzzy threshold against each other.
def test_similarly_phrased_catalog_skills_are_all_kept():
    catalog = _catalog_skills(
        "Plan and manage an Azure AI solution",
        "Implement generative AI and agentic solutions",
        "Implement computer vision solutions",
        "Implement text analysis solutions",
        "Implement information extraction solutions",
    )
    assert len(deep_discover.merge_exam_skills([], catalog)) == 5


def test_catalog_skills_already_in_the_scrape_are_dropped():
    scraped = [
        {
            "name": "Implement computer vision solutions",
            "weight": "20-25%",
            "topics": ["Analyse images"],
            "sourceUrls": [],
            "isExamSkill": True,
        }
    ]
    merged = deep_discover.merge_exam_skills(
        scraped, _catalog_skills("Implement computer vision solutions", "Plan an AI solution")
    )
    assert [s["name"] for s in merged] == [
        "Implement computer vision solutions",
        "Plan an AI solution",
    ]
    assert merged[0]["weight"] == "20-25%"


def test_exact_duplicate_catalog_skills_are_deduped():
    catalog = _catalog_skills("Plan an AI solution", "Plan an AI solution")
    assert len(deep_discover.merge_exam_skills([], catalog)) == 1
