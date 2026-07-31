"""
Deep discovery: Use Microsoft Learn Catalog API for comprehensive content discovery.

This module uses the official Microsoft Learn Catalog API to discover all learning paths,
modules, and units for a certification, then fetches the actual content from each unit.

Content hierarchy:
- Certification → Learning Paths → Modules → Units → Content

Discovery:
- One path: learning paths plus the exam skills outline, reconciled by a coverage sweep.
- Certification resolution is catalog-driven; see docs/CONTENT_DISCOVERY.md.

See docs/CONTENT_DISCOVERY.md for detailed explanation of content sources.
"""

import argparse
import json
import os
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Constants
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
REQUEST_DELAY = 0.3  # Be respectful to Microsoft's servers
CATALOG_URL = "https://learn.microsoft.com/api/catalog/"
LEARN_SEARCH_URL = "https://learn.microsoft.com/api/search"
# Synthetic parent for study-guide modules that belong to no learning path.
STANDALONE_MODULES_UID = "__exam_study_guide_modules__"
# Ceiling on the tag-matching fallback, which is broad by nature.
MAX_TAG_FILTER_PATHS = 25
# Units between progress callbacks during the crawl.
UNIT_PROGRESS_EVERY = 5

# Dynamic cert → role + product mapping for catalog-based learning path resolution.
# The catalog API tags every learning path with roles and products. By filtering on
# the role + relevant products we dynamically discover ALL current paths, even when
# Microsoft renames or restructures them.
CERTIFICATION_ROLE_PRODUCTS: dict[str, dict] = {
    "dp-700": {
        "roles": ["data-engineer"],
        "products": {
            "fabric", "microsoft-fabric", "power-bi",
        },
    },
    "ai-102": {
        "roles": ["ai-engineer"],
        "products": {
            "azure-ai-services", "azure-cognitive-services",
            "azure-ai-search", "azure-cognitive-search",
            "ai-services", "azure-ai-language", "azure-ai-speech",
            "azure-ai-vision", "azure-ai-document-intelligence",
            "azure-ai-translator", "azure-bot-service", "azure-openai",
        },
    },
    "az-104": {
        "roles": ["administrator"],
        "products": {
            "azure", "azure-virtual-machines", "azure-storage",
            "azure-virtual-network", "azure-resource-manager",
            "azure-monitor", "entra-id",
        },
    },
    "az-900": {
        "roles": ["administrator", "developer", "solution-architect"],
        "products": {"azure"},
        "title_keywords": ["fundamentals", "azure"],
    },
    "sc-300": {
        "roles": ["identity-access-admin"],
        "products": {
            "entra-id", "entra", "azure-active-directory",
        },
    },
    "ab-731": {
        "roles": ["business-owner"],
        "products": {
            "azure-openai", "ai-services",
            "dynamics-365-copilot", "microsoft-copilot",
        },
    },
}

# Fallback: Known certification to learning path mappings (for certs where we know the exact paths).
# Used when dynamic resolution fails or for override purposes.
CERTIFICATION_PATH_UIDS = {
    "dp-700": [
        "learn.wwl.ingest-data-with-microsoft-fabric",
        "learn.wwl.implement-lakehouse-microsoft-fabric",
        "learn.wwl.explore-real-time-analytics-microsoft-fabric",
        "learn-wwl.work-with-data-warehouses-using-microsoft-fabric",
        "learn.wwl.manage-microsoft-fabric-environment",
        "learn.wwl.get-started-fabric",
    ],
    "ai-102": [
        "learn.wwl.prepare-for-ai-engineering",
        "learn.wwl.provision-manage-azure-cognitive-services",
        "learn.wwl.process-translate-text-azure-cognitive-services",
        "learn.wwl.process-translate-speech-azure-cognitive-speech-services",
        "learn.wwl.create-language-solution-azure-cognitive-services",
        "learn.wwl.build-qna-solution-qna-maker",
        "learn.wwl.create-conversational-ai-solutions",
        "learn.wwl.create-computer-vision-solutions-azure-cognitive-services",
        "learn.wwl.extract-data-from-forms-document-intelligence",
        "learn.wwl.implement-knowledge-mining-azure-cognitive-search",
        "learn.wwl.develop-ai-solutions-azure-openai",
    ],
    "az-104": [
        "learn.wwl.az-104-administrator-prerequisites",
        "learn.wwl.az-104-manage-identities-governance",
        "learn.wwl.az-104-manage-storage",
        "learn.wwl.az-104-manage-compute-resources",
        "learn.wwl.az-104-manage-virtual-networks",
        "learn.wwl.az-104-monitor-backup-resources",
    ],
    "az-900": [
        "learn.wwl.azure-fundamentals-describe-cloud-concepts",
        "learn.wwl.azure-fundamentals-describe-azure-architecture-services",
        "learn.wwl.describe-azure-management-governance",
    ],
    "sc-300": [
        "learn.wwl.implement-identity-management-solution",
        "learn.wwl.implement-authentication-access-management-solution",
        "learn.wwl.implement-access-management-for-apps",
        "learn.wwl.plan-implement-identity-governance-strategy",
    ],
    "ab-731": [
        "learn.wwl.explore-business-value-generative-ai-solutions",
        "learn.wwl.drive-value-generative-ai-solutions",
        "learn.wwl.transform-your-business-with-microsoft-ai",
    ],
}


@dataclass
class Unit:
    """A single learning unit (lesson) within a module."""
    uid: str
    title: str
    url: str
    duration_minutes: int
    content: str = ""
    word_count: int = 0


@dataclass
class Module:
    """A learning module containing multiple units."""
    uid: str
    title: str
    url: str
    duration_minutes: int
    description: str
    units: list[Unit] = field(default_factory=list)


@dataclass
class LearningPath:
    """A learning path containing multiple modules."""
    uid: str
    title: str
    url: str
    duration_minutes: int
    description: str
    modules: list[Module] = field(default_factory=list)


@dataclass
class DeepDiscoveryResult:
    """Complete discovery results with full content."""
    certification_id: str
    certification_url: str
    learning_paths: list[LearningPath]
    total_modules: int
    total_units: int
    total_words: int
    estimated_episodes: int
    units_failed: int = 0
    resolution: dict = field(default_factory=dict)


def fetch_catalog() -> dict:
    """Fetch the full Microsoft Learn catalog."""
    print("Fetching Microsoft Learn catalog...")
    resp = requests.get(CATALOG_URL, params={"locale": "en-us"}, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    print(f"  Found {len(data.get('learningPaths', []))} learning paths")
    print(f"  Found {len(data.get('modules', []))} modules")
    print(f"  Found {len(data.get('units', []))} units")
    return data


def resolve_learning_paths_dynamic(
    certification_id: str,
    catalog: dict,
    roles: Optional[list[str]] = None,
    products: Optional[list[str]] = None,
) -> tuple[list[str], str]:
    """
    Resolve learning path UIDs by filtering the catalog on role + product tags.

    *roles* and *products* normally come from the exam's own catalog record, which
    works for every exam. CERTIFICATION_ROLE_PRODUCTS is only consulted when the
    catalog has nothing, and covers a handful of exams by hand.

    Returns:
        (list of learning path UIDs, resolution method description)
    """
    cert_lower = certification_id.lower()
    title_keywords: list[str] = []

    if roles or products:
        target_roles = set(roles or [])
        target_products = set(products or [])
        method = "catalog-tags"
    else:
        mapping = CERTIFICATION_ROLE_PRODUCTS.get(cert_lower)
        if not mapping:
            return [], "no-mapping"
        target_roles = set(mapping.get("roles", []))
        target_products = set(mapping.get("products", []))
        title_keywords = [kw.lower() for kw in mapping.get("title_keywords", [])]
        method = "curated-tags"

    matched: list[tuple[int, str]] = []

    for path in catalog.get("learningPaths", []):
        path_roles = set(path.get("roles", []))
        path_products = set(path.get("products", []))

        # Must share at least one role
        if target_roles and not path_roles.intersection(target_roles):
            continue

        overlap = path_products.intersection(target_products)
        if target_products and not overlap:
            continue

        # Optional title keyword filter (for broad certs like az-900)
        if title_keywords:
            title_lower = path.get("title", "").lower()
            if not any(kw in title_lower for kw in title_keywords):
                continue

        matched.append((len(overlap), path["uid"]))

    # Most product overlap first: role+product alone matches everything Microsoft
    # tags "administrator" + "azure", which is far wider than any one exam.
    matched.sort(key=lambda item: (-item[0], item[1]))
    return [uid for _, uid in matched], method


# ---------------------------------------------------------------------------
# Catalog-driven certification resolution
# ---------------------------------------------------------------------------

@dataclass
class CertificationRef:
    """What the Learn catalog knows about one exam.

    `study_guide` is Microsoft's own curated content list for the exam, so it is
    preferred over any tag heuristic. It contains bare modules as well as
    learning paths.
    """
    certification_id: str
    exam_uid: str = ""
    certification_slug: str = ""
    title: str = ""
    url: str = ""
    skills_pdf_url: str = ""
    skills_measured: list[str] = field(default_factory=list)
    study_guide_paths: list[str] = field(default_factory=list)
    study_guide_modules: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


def _split_study_guide(entries, courses: Optional[dict] = None) -> tuple[list[str], list[str]]:
    paths, modules = [], []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        uid, kind = entry.get("uid"), entry.get("type")
        if not uid:
            continue
        if kind == "learningPath":
            paths.append(uid)
        elif kind == "module":
            modules.append(uid)
        elif kind == "course" and courses is not None:
            # 48 certifications point only at an instructor-led course, whose own
            # study guide holds the curated paths. Dropping these sent ai-103 to
            # tag matching: 25 alphabetically chosen paths instead of its real 4.
            # Not recursed further -- courses list paths and modules directly.
            course_paths, course_modules = _split_study_guide(
                (courses.get(uid) or {}).get("study_guide")
            )
            paths.extend(course_paths)
            modules.extend(course_modules)
    return paths, modules


def resolve_certification_slug(certification_id: str) -> str:
    """Follow the exam page redirect to the certification that owns the exam.

    Only 141 of the ~500 exam codes have a record in the catalog's `exams`
    collection, and the current role-based ones (az-104, dp-700, ai-102 ...) are
    not among them. `mergedCertifications` has no exam field to join on, so this
    redirect is the only reliable exam-code -> certification link.
    """
    url = (
        "https://learn.microsoft.com/en-us/credentials/certifications/exams/"
        f"{certification_id.lower()}/"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
    except Exception as e:
        print(f"  Warning: could not resolve the certification page for {certification_id}: {e}")
        return ""
    if resp.status_code != 200:
        return ""

    parts = [p for p in urlparse(resp.url).path.split("/") if p]
    if "certifications" not in parts:
        return ""
    tail = parts[parts.index("certifications") + 1:]
    # A single segment means we landed on a certification page; two means we
    # stayed on /exams/<code>/, which has no certification of its own.
    return tail[0] if len(tail) == 1 else ""


def _certification_records(slug: str, catalog: dict):
    if not slug:
        return
    needle = f"/certifications/{slug}/"
    for collection in ("mergedCertifications", "certifications"):
        for record in catalog.get(collection, []):
            if needle in (record.get("url") or ""):
                yield collection, record


def resolve_certification(
    certification_id: str, catalog: dict, slug: Optional[str] = None
) -> Optional[CertificationRef]:
    """Look an exam up in the catalog and collect everything it points at.

    Returns None when neither an exam nor a certification can be found, which is
    the cheapest way to reject a certification ID that does not exist.
    """
    cert_lower = certification_id.lower()
    wanted_uid = f"exam.{cert_lower}"
    ref = CertificationRef(certification_id=cert_lower)
    courses = {c["uid"]: c for c in catalog.get("courses") or [] if c.get("uid")}

    for record in catalog.get("exams", []):
        if record.get("uid", "").lower() == wanted_uid or (
            record.get("display_name", "").lower() == cert_lower
        ):
            paths, modules = _split_study_guide(record.get("study_guide"), courses)
            ref.exam_uid = record.get("uid", wanted_uid)
            ref.title = record.get("title", "")
            ref.url = record.get("url", "")
            ref.skills_pdf_url = record.get("pdf_download_url", "")
            ref.study_guide_paths.extend(paths)
            ref.study_guide_modules.extend(modules)
            ref.roles.extend(record.get("roles") or [])
            ref.products.extend(record.get("products") or [])
            ref.sources.append("exam")
            break

    if slug is None:
        slug = resolve_certification_slug(cert_lower)
    ref.certification_slug = slug

    for collection, record in _certification_records(slug, catalog):
        cert_paths, cert_modules = _split_study_guide(record.get("study_guide"), courses)
        ref.study_guide_paths.extend(cert_paths)
        ref.study_guide_modules.extend(cert_modules)
        ref.skills_measured.extend(record.get("skills") or [])
        ref.roles.extend(record.get("roles") or [])
        ref.products.extend(record.get("products") or [])
        ref.title = ref.title or record.get("title", "")
        ref.url = ref.url or record.get("url", "")
        ref.sources.append(f"{collection}:{record.get('uid', '')}")

    # Multi-exam certifications do carry an exams array; use it when the exam has
    # its own record but no certification page of its own.
    if ref.exam_uid and not slug:
        for record in catalog.get("certifications", []):
            if ref.exam_uid not in (record.get("exams") or []):
                continue
            cert_paths, cert_modules = _split_study_guide(record.get("study_guide"), courses)
            ref.study_guide_paths.extend(cert_paths)
            ref.study_guide_modules.extend(cert_modules)
            ref.skills_measured.extend(record.get("skills") or [])
            ref.roles.extend(record.get("roles") or [])
            ref.products.extend(record.get("products") or [])
            ref.sources.append(f"certifications:{record.get('uid', '')}")

    if not ref.sources:
        return None

    ref.study_guide_paths = _dedupe(ref.study_guide_paths)
    ref.study_guide_modules = _dedupe(ref.study_guide_modules)
    ref.roles = _dedupe(ref.roles)
    ref.products = _dedupe(ref.products)
    ref.skills_measured = _dedupe(ref.skills_measured)
    return ref


def _dedupe(values: list[str]) -> list[str]:
    seen, out = set(), []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def resolve_content_sources(
    certification_id: str,
    catalog: dict,
    cert_ref: Optional[CertificationRef] = None,
    slug: Optional[str] = None,
) -> tuple[list[str], list[str], dict]:
    """Union every place the catalog says this exam's content lives.

    Returns (learning path UIDs, standalone module UIDs, report). The report is
    persisted so a thin result is attributable to a source rather than a mystery.
    """
    cert_lower = certification_id.lower()
    paths_by_uid = {p["uid"] for p in catalog.get("learningPaths", [])}
    modules_by_uid = {m["uid"] for m in catalog.get("modules", [])}

    report: dict = {"certificationId": cert_lower, "sources": {}, "warnings": []}
    path_uids: list[str] = []
    module_uids: list[str] = []

    if cert_ref is None:
        cert_ref = resolve_certification(cert_lower, catalog, slug=slug)

    def live(uids: list[str], known: set, label: str) -> list[str]:
        """Keep only UIDs the catalog still publishes, and say so when it doesn't."""
        kept = [uid for uid in _dedupe(uids) if uid in known]
        missing = len(_dedupe(uids)) - len(kept)
        if missing:
            report["warnings"].append(
                f"{missing} {label} UID(s) from the {label} list are no longer in the catalog"
            )
        return kept

    if cert_ref is None:
        report["warnings"].append(
            f"No exam or certification record for '{cert_lower}' in the Microsoft Learn catalog"
        )
        report["examFound"] = False
    else:
        report["examFound"] = True
        report["examUid"] = cert_ref.exam_uid
        report["certificationSlug"] = cert_ref.certification_slug
        report["examTitle"] = cert_ref.title
        report["skillsMeasuredCount"] = len(cert_ref.skills_measured)
        report["skillsPdfUrl"] = cert_ref.skills_pdf_url
        module_uids = live(cert_ref.study_guide_modules, modules_by_uid, "module")
        path_uids = live(cert_ref.study_guide_paths, paths_by_uid, "study guide")
        if path_uids or module_uids:
            report["sources"]["studyGuide"] = {
                "paths": len(path_uids),
                "modules": len(module_uids),
                "from": cert_ref.sources,
            }

    # Each tier is validated before the next is considered: the hand-maintained
    # UIDs go stale as Microsoft restructures, and a tier that resolves to
    # nothing must fall through rather than yield an empty syllabus.
    if not path_uids and cert_lower in CERTIFICATION_PATH_UIDS:
        curated = live(CERTIFICATION_PATH_UIDS[cert_lower], paths_by_uid, "curated")
        if curated:
            path_uids = curated
            report["sources"]["curatedUids"] = {"paths": len(curated)}

    # Tag filtering is the last resort: role + product alone pulls in every path
    # Microsoft tags for the product family, which for az-104 is 116 paths and
    # ~4,200 units of largely off-syllabus content. Precision gap-filling is
    # coverage_sweep's job, not this one's.
    if not path_uids:
        tag_paths, tag_method = resolve_learning_paths_dynamic(
            cert_lower,
            catalog,
            roles=cert_ref.roles if cert_ref else None,
            products=cert_ref.products if cert_ref else None,
        )
        tag_paths = [uid for uid in tag_paths if uid in paths_by_uid]
        path_uids = tag_paths[:MAX_TAG_FILTER_PATHS]
        report["sources"]["tagFilter"] = {
            "method": tag_method,
            "paths": len(path_uids),
            "matchedBeforeCap": len(tag_paths),
        }
        if len(tag_paths) > MAX_TAG_FILTER_PATHS:
            report["warnings"].append(
                f"Tag matching returned {len(tag_paths)} learning paths; capped at "
                f"{MAX_TAG_FILTER_PATHS}. Add {cert_lower} to CERTIFICATION_PATH_UIDS "
                f"to pin the syllabus."
            )

    report["resolvedPaths"] = len(path_uids)
    report["resolvedStandaloneModules"] = len(module_uids)
    return path_uids, module_uids, report


# ---------------------------------------------------------------------------
# Coverage sweep: fallback chain for uncovered exam skills
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for fuzzy matching."""
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(text.split())


def _topic_similarity(topic: str, reference: str) -> float:
    """Return 0‒1 similarity score between two topic strings."""
    return SequenceMatcher(None, _normalize(topic), _normalize(reference)).ratio()


def _topic_covered(topic: str, reference_titles: list[str], threshold: float = 0.45) -> bool:
    """Check if a topic is covered by any of the reference module/unit titles."""
    norm_topic = _normalize(topic)
    for ref in reference_titles:
        if _topic_similarity(norm_topic, _normalize(ref)) >= threshold:
            return True
        # Also check substring containment (handles short topic labels)
        if norm_topic in _normalize(ref) or _normalize(ref) in norm_topic:
            return True
    return False


def search_learn_docs(query: str, top: int = 5) -> list[dict]:
    """
    Search Microsoft Learn docs API for pages matching *query*.

    Returns list of {title, url, description}.
    """
    try:
        resp = requests.get(
            LEARN_SEARCH_URL,
            params={
                "search": query,
                "locale": "en-us",
                "$top": top,
                "facet": "category",
                "$filter": "category eq 'Documentation' or category eq 'Training'",
            },
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "description": r.get("description", ""),
            }
            for r in results
        ]
    except Exception as e:
        print(f"  Learn search failed for '{query}': {e}")
        return []


def coverage_sweep(
    exam_skills: list[dict],
    discovered_modules: list[dict],
    catalog: dict,
) -> dict:
    """
    Check each exam skill topic against discovered content.

    Fallback chain for uncovered topics:
      1. Title match against discovered module/unit titles
      2. Catalog module description search
      3. Learn docs API search
      4. Mark as explicit gap

    Args:
        exam_skills: List of exam skill dicts from fetch_exam_skills_outline()
        discovered_modules: Flat list of {title, topics, sourceUrls} from learning paths
        catalog: Full catalog dict for module description search

    Returns:
        {
            "covered": [{skill, topic, matchedBy}],
            "supplemented": [{skill, topic, source, urls}],
            "gaps": [{skill, topic}],
            "supplementalUrls": [str],
        }
    """
    print("\n" + "=" * 60)
    print("COVERAGE SWEEP: checking exam skills against discovered content")
    print("=" * 60)

    # Build reference corpus from discovered learning path content
    reference_titles: list[str] = []
    for mod in discovered_modules:
        reference_titles.append(mod.get("title", ""))
        reference_titles.extend(mod.get("topics", []))

    # Build module description index from catalog for fallback
    catalog_modules = catalog.get("modules", [])
    catalog_module_descs = [
        {
            "title": m.get("title", ""),
            "summary": m.get("summary", ""),
            "url": m.get("url", ""),
            "uid": m.get("uid", ""),
        }
        for m in catalog_modules
    ]

    covered: list[dict] = []
    supplemented: list[dict] = []
    gaps: list[dict] = []
    supplemental_urls: set[str] = set()

    for skill in exam_skills:
        skill_name = skill.get("name", "Unknown")
        for topic in skill.get("topics", []):
            # --- Level 1: title match against discovered content ---
            if _topic_covered(topic, reference_titles):
                covered.append({"skill": skill_name, "topic": topic, "matchedBy": "learning-path"})
                continue

            # --- Level 2: catalog module description search ---
            norm_topic = _normalize(topic)
            found_in_catalog = False
            for cm in catalog_module_descs:
                combined = _normalize(cm["title"] + " " + cm["summary"])
                if _topic_similarity(norm_topic, combined) >= 0.40 or norm_topic in combined:
                    url = cm["url"]
                    if url and not url.startswith("http"):
                        url = f"https://learn.microsoft.com{url}"
                    supplemented.append({
                        "skill": skill_name,
                        "topic": topic,
                        "source": "catalog-module",
                        "urls": [url] if url else [],
                        "matchedModule": cm["title"],
                    })
                    if url:
                        supplemental_urls.add(url)
                    found_in_catalog = True
                    break

            if found_in_catalog:
                continue

            # --- Level 3: Learn search API ---
            search_results = search_learn_docs(topic, top=3)
            time.sleep(REQUEST_DELAY)  # Rate limit
            if search_results:
                urls = [r["url"] for r in search_results if r.get("url")]
                supplemented.append({
                    "skill": skill_name,
                    "topic": topic,
                    "source": "learn-search",
                    "urls": urls,
                    "matchedModule": search_results[0].get("title", ""),
                })
                supplemental_urls.update(urls)
                continue

            # --- Level 4: Explicit gap ---
            gaps.append({"skill": skill_name, "topic": topic})

    total_topics = sum(len(s.get("topics", [])) for s in exam_skills)
    print(f"  Total exam skill topics: {total_topics}")
    print(f"  Covered by learning paths: {len(covered)}")
    print(f"  Supplemented (catalog): {len([s for s in supplemented if s['source'] == 'catalog-module'])}")
    print(f"  Supplemented (search):  {len([s for s in supplemented if s['source'] == 'learn-search'])}")
    print(f"  Remaining gaps: {len(gaps)}")
    if gaps:
        print("  Gap details:")
        for g in gaps[:10]:  # Show first 10
            print(f"    - [{g['skill']}] {g['topic']}")
        if len(gaps) > 10:
            print(f"    ... and {len(gaps) - 10} more")

    return {
        "covered": covered,
        "supplemented": supplemented,
        "gaps": gaps,
        "supplementalUrls": list(supplemental_urls),
    }


def compute_confidence_score(
    coverage_result: dict,
    exam_skills: list[dict],
) -> dict:
    """
    Compute a confidence score showing how completely the exam content is covered.

    Score breakdown:
      - Topics covered by learning paths:        1.0  (full weight)
      - Topics supplemented from catalog:         0.8  (good but not pre-curated)
      - Topics supplemented from Learn search:    0.5  (relevant but unverified)
      - Explicit gaps (no content found):         0.0

    Returns:
        {
            "overallScore": float (0-100),
            "totalTopics": int,
            "breakdown": {
                "learningPath": {"count": int, "weight": 1.0},
                "catalogModule": {"count": int, "weight": 0.8},
                "learnSearch":   {"count": int, "weight": 0.5},
                "gap":           {"count": int, "weight": 0.0},
            },
            "perSkillScores": [{skill, score, coveredTopics, totalTopics}],
            "grade": str,  # "A" / "B" / "C" / "D" / "F"
        }
    """
    covered = coverage_result.get("covered", [])
    supplemented = coverage_result.get("supplemented", [])
    gaps = coverage_result.get("gaps", [])

    lp_count = len(covered)
    catalog_count = len([s for s in supplemented if s["source"] == "catalog-module"])
    search_count = len([s for s in supplemented if s["source"] == "learn-search"])
    gap_count = len(gaps)
    total = lp_count + catalog_count + search_count + gap_count

    if total == 0:
        # Nothing to measure against is "unverified", not "fails the exam". An F
        # here reads as bad content when the skills outline simply wasn't found.
        return {
            "overallScore": 0.0,
            "totalTopics": 0,
            "breakdown": {},
            "perSkillScores": [],
            "grade": "n/a",
        }

    # Weighted score
    weighted_sum = (lp_count * 1.0) + (catalog_count * 0.8) + (search_count * 0.5)
    overall_score = round((weighted_sum / total) * 100, 1)

    # Per-skill breakdown
    skill_topic_status: dict[str, dict] = {}
    for item in covered:
        sk = item["skill"]
        skill_topic_status.setdefault(sk, {"covered": 0, "total": 0})
        skill_topic_status[sk]["covered"] += 1
        skill_topic_status[sk]["total"] += 1
    for item in supplemented:
        sk = item["skill"]
        skill_topic_status.setdefault(sk, {"covered": 0, "total": 0})
        skill_topic_status[sk]["covered"] += 0.8 if item["source"] == "catalog-module" else 0.5
        skill_topic_status[sk]["total"] += 1
    for item in gaps:
        sk = item["skill"]
        skill_topic_status.setdefault(sk, {"covered": 0, "total": 0})
        skill_topic_status[sk]["total"] += 1

    per_skill = []
    for sk, counts in skill_topic_status.items():
        t = counts["total"]
        score = round((counts["covered"] / t) * 100, 1) if t > 0 else 0.0
        per_skill.append({
            "skill": sk,
            "score": score,
            "coveredTopics": round(counts["covered"], 1),
            "totalTopics": t,
        })

    # Letter grade
    if overall_score >= 90:
        grade = "A"
    elif overall_score >= 75:
        grade = "B"
    elif overall_score >= 60:
        grade = "C"
    elif overall_score >= 40:
        grade = "D"
    else:
        grade = "F"

    print(f"\n  Confidence Score: {overall_score}% (Grade: {grade})")
    print(f"    Learning path coverage: {lp_count}/{total}")
    print(f"    Catalog supplemented:   {catalog_count}/{total}")
    print(f"    Search supplemented:    {search_count}/{total}")
    print(f"    Gaps:                   {gap_count}/{total}")

    return {
        "overallScore": overall_score,
        "totalTopics": total,
        "breakdown": {
            "learningPath": {"count": lp_count, "weight": 1.0},
            "catalogModule": {"count": catalog_count, "weight": 0.8},
            "learnSearch": {"count": search_count, "weight": 0.5},
            "gap": {"count": gap_count, "weight": 0.0},
        },
        "perSkillScores": per_skill,
        "grade": grade,
    }


def fetch_page(url: str, retries: int = 3) -> str:
    """Fetch HTML content with retries."""
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            response.encoding = 'utf-8'
            return response.text
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(1)
                continue
            raise e


def extract_text_content(html: str) -> tuple[str, int]:
    """Extract main text content from a unit page."""
    soup = BeautifulSoup(html, "lxml")
    
    # Find main content area
    main = soup.find("main") or soup.find("article") or soup.find("div", class_="content")
    if not main:
        return "", 0
    
    # Remove navigation, headers, footers, scripts
    for tag in main.find_all(["nav", "header", "footer", "script", "style", "aside"]):
        tag.decompose()
    
    # Extract text from content elements
    text_blocks = []
    for elem in main.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "pre", "code"]):
        text = elem.get_text(strip=True)
        if text and len(text) > 10:
            # Skip navigation/boilerplate
            skip_phrases = [
                "skip to main", "previous", "next unit", "was this page helpful",
                "module assessment", "sign in", "browse all training", "feedback",
                "content language selector", "your privacy choices"
            ]
            if any(skip in text.lower() for skip in skip_phrases):
                continue
            text_blocks.append(text)
    
    full_text = "\n\n".join(text_blocks)
    word_count = len(full_text.split())
    return full_text, word_count


def fetch_module_hierarchy(module_uid: str) -> dict:
    """
    Fetch module hierarchy from the hierarchy API to get actual unit URLs.
    
    The hierarchy API returns accurate URLs for all units, unlike guessing from UIDs.
    """
    url = f"https://learn.microsoft.com/api/hierarchy/modules/{module_uid}?locale=en-us"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"    Warning: Could not fetch hierarchy for {module_uid}: {e}")
        return {}


def build_unit_url_from_hierarchy(hierarchy: dict, unit_uid: str) -> str:
    """
    Get the unit URL from the hierarchy API response.
    
    The hierarchy API returns the actual URL for each unit, which may have
    non-sequential numbering (e.g., 3b-optimize instead of 4-optimize).
    """
    for unit in hierarchy.get("units", []):
        if unit.get("uid") == unit_uid:
            url = unit.get("url", "")
            if url and not url.startswith("http"):
                return f"https://learn.microsoft.com{url}"
            return url
    return ""


def build_unit_url(module_url: str, unit_uid: str, unit_index: int = 1) -> str:
    """
    Build the URL for a unit page using the module's URL (fallback method).
    
    Module URL: https://learn.microsoft.com/en-us/training/modules/use-dataflow-gen-2-fabric/...
    Unit UID: learn.wwl.ingest-dataflows-gen2-fabric.introduction
    
    URL pattern: /training/modules/{module-slug}/{unit-index}-{unit-slug}/
    
    NOTE: This is a fallback - prefer fetch_module_hierarchy() for accurate URLs.
    """
    # Extract module slug from URL
    # URL format: https://learn.microsoft.com/en-us/training/modules/{module-slug}/...
    import re
    match = re.search(r'/training/modules/([^/?#]+)', module_url)
    if not match:
        return ""
    module_slug = match.group(1)
    
    # Extract unit slug from UID (last part after the last dot)
    unit_parts = unit_uid.split(".")
    unit_slug = unit_parts[-1] if unit_parts else unit_uid
    
    # Build URL with index prefix (e.g., 1-introduction, 2-explore)
    return f"https://learn.microsoft.com/en-us/training/modules/{module_slug}/{unit_index}-{unit_slug}/"


def study_guide_url_for(certification_id: str) -> str:
    return (
        "https://learn.microsoft.com/en-us/credentials/certifications/resources/"
        f"study-guides/{certification_id.lower()}"
    )


def fetch_exam_skills_outline(
    certification_id: str, study_guide_url: str = None
) -> list[dict]:
    """
    Fetch the exam skills outline from the official study guide.

    This provides the specific testable skills that may not be fully covered
    by learning path content alone.

    A caller-supplied URL is tried first but not trusted: the portal's examUrl
    is an exam or certification landing page, which carries no skills outline,
    and accepting the empty parse from one reports as zero exam coverage.

    Returns list of skill dicts with format:
    {
        "name": "Skill name",
        "weight": "30-35%",
        "topics": ["Specific skill 1", "Specific skill 2", ...],
        "sourceUrls": [],
        "isExamSkill": True  # Flag to distinguish from learning path content
    }
    """
    candidates = [u for u in (study_guide_url, study_guide_url_for(certification_id)) if u]
    for url in dict.fromkeys(candidates):
        print(f"\nFetching exam skills outline from: {url}")
        try:
            html = fetch_page(url)
        except Exception as e:
            print(f"  Warning: Could not fetch study guide: {e}")
            continue
        skills = _parse_exam_skills(html)
        print(
            f"  Found {len(skills)} objectives with "
            f"{sum(len(s['topics']) for s in skills)} specific skills"
        )
        if skills:
            return skills
    return []


def _parse_exam_skills(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")

    skills = []
    current_domain = None
    current_objective = None
    
    # Pattern for domain headings with percentages (handles various dash types)
    domain_pattern = re.compile(r'(.+?)\s*\((\d+).(\d+)%\)')
    
    for element in soup.find_all(['h3', 'h4', 'ul', 'li']):
        text = element.get_text(strip=True)
        
        # Check for domain heading (h3 with percentage)
        if element.name == 'h3':
            match = domain_pattern.search(text)
            if match:
                current_domain = {
                    "name": match.group(1).strip(),
                    "weight": f"{match.group(2)}-{match.group(3)}%",
                    "topics": [],
                    "sourceUrls": [],
                    "isExamSkill": True
                }
                current_objective = None
        
        # Check for objective heading (h4)
        elif element.name == 'h4' and current_domain:
            obj_text = text.strip()
            if obj_text and 'Note' not in obj_text and len(obj_text) > 10:
                # Save previous domain if it has topics
                if current_domain.get("topics"):
                    skills.append(current_domain.copy())
                
                # Start a new domain for this objective
                current_domain = {
                    "name": f"{current_domain['name']}: {obj_text}",
                    "weight": current_domain["weight"],
                    "topics": [],
                    "sourceUrls": [],
                    "isExamSkill": True
                }
        
        # Check for specific skills (li items)
        elif element.name == 'li' and current_domain:
            skill_text = text.strip()
            if skill_text and len(skill_text) > 5:
                # Filter out non-skill items
                skip_phrases = ['how to earn', 'certification renewal', 'exam scoring', 
                               'sandbox', 'last updated', 'high contrast', 'ai disclaimer',
                               'previous versions', 'contribute', 'privacy', 'terms of use',
                               'trademarks', '© microsoft']
                if not any(x in skill_text.lower() for x in skip_phrases):
                    current_domain["topics"].append(skill_text)

                    # Extract inline links as source URLs for this skill
                    for link in element.find_all("a", href=True):
                        href = link["href"]
                        if href.startswith("/"):
                            href = f"https://learn.microsoft.com{href}"
                        if "learn.microsoft.com" in href:
                            current_domain["sourceUrls"].append(href)
    
    # Don't forget last domain
    if current_domain and current_domain.get("topics"):
        skills.append(current_domain)
    
    # Filter out empty skills
    return [s for s in skills if s.get("topics")]


def exam_skills_from_catalog(cert_ref: Optional[CertificationRef]) -> list[dict]:
    """The official skills-measured list, straight off the catalog record.

    The study-guide page is scraped for its per-domain sub-bullets, but that
    parse depends on Microsoft's HTML; this does not.
    """
    if not cert_ref or not cert_ref.skills_measured:
        return []
    return [
        {
            "name": skill,
            "weight": None,
            "topics": [skill],
            "sourceUrls": [],
            "isExamSkill": True,
        }
        for skill in cert_ref.skills_measured
    ]


def merge_exam_skills(scraped: list[dict], catalog_skills: list[dict]) -> list[dict]:
    """Union the scraped study guide with the catalog list, keyed on domain name.

    The scrape wins on overlap because it carries weights and sub-topics.
    """
    merged = list(scraped)
    # Matched by containment, not similarity: the scrape names domains
    # "<domain>: <objective>", so a catalog skill already present appears
    # verbatim inside one. Similarity cannot separate these — unrelated siblings
    # ("computer vision" vs "text analysis", 0.74) outscore true matches (0.51),
    # which collapsed ai-103's five skills to two.
    scraped_names = [_normalize(s.get("name", "")) for s in scraped]
    seen = set(scraped_names)
    for skill in catalog_skills:
        key = _normalize(skill.get("name", ""))
        if not key or key in seen:
            continue
        if any(key in name for name in scraped_names):
            continue
        seen.add(key)
        merged.append(skill)
    return merged


def deep_discover(
    certification_id: str,
    max_paths: Optional[int] = None,
    max_modules_per_path: Optional[int] = None,
    max_units_per_module: Optional[int] = None,
    skip_content: bool = False,
    catalog: Optional[dict] = None,
    cert_ref: Optional["CertificationRef"] = None,
    progress: Optional[callable] = None,
) -> DeepDiscoveryResult:
    """
    Perform deep discovery using the Microsoft Learn Catalog API.
    
    Args:
        certification_id: Microsoft certification ID (e.g., 'dp-700')
        max_paths: Limit number of learning paths (for testing)
        max_modules_per_path: Limit modules per path (for testing)
        max_units_per_module: Limit units per module (for testing)
        skip_content: If True, don't fetch unit content (faster for structure only)
        catalog: Pre-fetched catalog dict (avoids redundant API call)
    
    Returns:
        DeepDiscoveryResult with all discovered content
    """
    print(f"Starting deep discovery for {certification_id}")
    print("=" * 60)
    
    # Fetch the catalog (or reuse pre-fetched)
    if catalog is None:
        catalog = fetch_catalog()
    
    # Build lookup tables
    paths_by_uid = {p["uid"]: p for p in catalog.get("learningPaths", [])}
    modules_by_uid = {m["uid"]: m for m in catalog.get("modules", [])}
    units_by_uid = {u["uid"]: u for u in catalog.get("units", [])}
    
    # Get learning path UIDs for this certification
    cert_lower = certification_id.lower()
    path_uids, standalone_module_uids, resolution_report = resolve_content_sources(
        cert_lower, catalog, cert_ref=cert_ref
    )
    print(
        f"Resolved {len(path_uids)} learning paths and "
        f"{len(standalone_module_uids)} standalone modules for {certification_id}"
    )
    for warning in resolution_report.get("warnings", []):
        print(f"  Warning: {warning}")

    if max_paths:
        path_uids = path_uids[:max_paths]

    # Modules the study guide lists directly, with no parent path, are wrapped in
    # a synthetic path so the rest of the walk is uniform.
    if standalone_module_uids:
        path_uids = list(path_uids) + [STANDALONE_MODULES_UID]
        paths_by_uid[STANDALONE_MODULES_UID] = {
            "uid": STANDALONE_MODULES_UID,
            "title": "Exam study guide modules",
            "url": "",
            "durationInMinutes": 0,
            "summary": "Modules listed directly on the exam study guide.",
            "modules": standalone_module_uids,
        }

    learning_paths = []
    total_modules = 0
    total_units = 0
    total_words = 0
    units_failed = 0

    # The catalog already knows how many units each module has, so the total is
    # exact before a single page is fetched. Without it the portal has nothing
    # to show but 0/1 for the several minutes this takes.
    planned_units = sum(
        len(modules_by_uid.get(module_uid, {}).get("units", []))
        for path_uid in path_uids
        for module_uid in paths_by_uid.get(path_uid, {}).get("modules", [])
    )
    if progress:
        progress(
            "discover", 0, planned_units,
            f"Reading {planned_units} units across {len(path_uids)} learning paths",
        )
    
    # Process each learning path
    for i, path_uid in enumerate(path_uids):
        path_data = paths_by_uid.get(path_uid)
        if not path_data:
            print(f"  Warning: Learning path {path_uid} not found in catalog")
            continue
        
        print(f"\n[{i+1}/{len(path_uids)}] {path_data.get('title', 'Unknown')}")
        
        path = LearningPath(
            uid=path_uid,
            title=path_data.get("title", "Unknown"),
            url=path_data.get("url", ""),
            duration_minutes=path_data.get("durationInMinutes") or 0,
            description=path_data.get("summary", ""),
            modules=[]
        )
        
        # Get modules for this path
        module_uids = path_data.get("modules", [])
        if max_modules_per_path:
            module_uids = module_uids[:max_modules_per_path]
        
        for j, module_uid in enumerate(module_uids):
            module_data = modules_by_uid.get(module_uid)
            if not module_data:
                print(f"    Warning: Module {module_uid} not found in catalog")
                continue
            
            print(f"    [{j+1}/{len(module_uids)}] {module_data.get('title', 'Unknown')}")
            
            module = Module(
                uid=module_uid,
                title=module_data.get("title", "Unknown"),
                url=module_data.get("url", ""),
                duration_minutes=module_data.get("durationInMinutes") or 0,
                description=module_data.get("summary", ""),
                units=[]
            )
            
            # Get units for this module
            unit_uids = module_data.get("units", [])
            if max_units_per_module:
                unit_uids = unit_uids[:max_units_per_module]
            
            # Fetch module hierarchy to get accurate unit URLs
            hierarchy = fetch_module_hierarchy(module_uid)
            time.sleep(REQUEST_DELAY)  # Be respectful
            
            for k, unit_uid in enumerate(unit_uids):
                unit_data = units_by_uid.get(unit_uid)
                if not unit_data:
                    # Try partial match (sometimes UIDs vary slightly)
                    continue
                
                # Get URL from hierarchy API (accurate) or fall back to guessing
                unit_url = build_unit_url_from_hierarchy(hierarchy, unit_uid)
                if not unit_url:
                    # Fallback to guessing (may not work for all units)
                    unit_url = build_unit_url(module.url, unit_uid, unit_index=k + 1)
                
                unit = Unit(
                    uid=unit_uid,
                    title=unit_data.get("title", "Unknown"),
                    url=unit_url,
                    duration_minutes=unit_data.get("duration_in_minutes") or 0,
                    content="",
                    word_count=0
                )
                
                # Fetch content if not skipping
                if not skip_content:
                    try:
                        time.sleep(REQUEST_DELAY)
                        html = fetch_page(unit_url)
                        content, word_count = extract_text_content(html)
                        unit.content = content
                        unit.word_count = word_count
                        total_words += word_count
                        print(f"        [{k+1}/{len(unit_uids)}] {unit.title} ({word_count} words)")
                    except Exception as e:
                        units_failed += 1
                        print(f"        [{k+1}/{len(unit_uids)}] {unit.title} (fetch error: {e})")
                
                module.units.append(unit)
                total_units += 1
                # Throttled: a Cosmos write per unit would be hundreds per run.
                if progress and (total_units % UNIT_PROGRESS_EVERY == 0
                                 or total_units == planned_units):
                    progress(
                        "discover", total_units, max(planned_units, total_units),
                        f"{module.title} ({total_words:,} words so far)",
                    )
            
            path.modules.append(module)
            total_modules += 1
        
        learning_paths.append(path)
    
    # Calculate estimated episodes (assuming ~1400 words per episode)
    words_per_episode = 1400
    estimated_episodes = max(1, total_words // words_per_episode) if total_words > 0 else total_units

    resolution_report["unitsDiscovered"] = total_units
    resolution_report["unitsFailed"] = units_failed
    resolution_report["totalWords"] = total_words

    result = DeepDiscoveryResult(
        certification_id=certification_id,
        certification_url=f"https://learn.microsoft.com/en-us/credentials/certifications/exams/{certification_id}/",
        learning_paths=learning_paths,
        total_modules=total_modules,
        total_units=total_units,
        total_words=total_words,
        estimated_episodes=estimated_episodes,
        units_failed=units_failed,
        resolution=resolution_report,
    )

    print("\n" + "=" * 60)
    print("Discovery complete!")
    print(f"  Learning Paths: {len(learning_paths)}")
    print(f"  Total Modules: {total_modules}")
    print(f"  Total Units: {total_units}")
    print(f"  Failed unit fetches: {units_failed}")
    print(f"  Total Words: {total_words:,}")
    print(f"  Estimated Episodes: {estimated_episodes}")

    return result


def discover_test_content() -> DeepDiscoveryResult:
    """
    Discover minimal test content for development/testing.
    
    Uses a single unit from the catalog to generate one episode.
    Cost: ~$0.15 instead of $15+
    """
    print("Running TEST MODE discovery (single unit)")
    print("=" * 60)
    
    # Fetch catalog to get a real unit
    catalog = fetch_catalog()
    
    # Build lookup
    modules_by_uid = {m["uid"]: m for m in catalog.get("modules", [])}
    
    # Find the Azure Fundamentals introduction module (known to exist and be short)
    test_module_uid = "learn.wwl.describe-cloud-compute"
    test_unit_uid = "learn.wwl.describe-cloud-compute.introduction-microsoft-azure-fundamentals"
    
    module_data = modules_by_uid.get(test_module_uid, {})
    
    # Use hierarchy API to get accurate URL
    hierarchy = fetch_module_hierarchy(test_module_uid)
    unit_url = build_unit_url_from_hierarchy(hierarchy, test_unit_uid)
    if not unit_url:
        # Fallback to guessing
        module_url = module_data.get("url", "")
        unit_url = build_unit_url(module_url, test_unit_uid, unit_index=1)
    print(f"Test unit URL: {unit_url}")
    
    # Fetch content
    content = ""
    word_count = 0
    try:
        html = fetch_page(unit_url)
        content, word_count = extract_text_content(html)
        print(f"Fetched {word_count} words")
    except Exception as e:
        print(f"Could not fetch content: {e}")
    
    unit = Unit(
        uid=test_unit_uid,
        title="Introduction to Microsoft Azure Fundamentals",
        url=unit_url,
        duration_minutes=2,
        content=content,
        word_count=word_count
    )
    
    module = Module(
        uid=test_module_uid,
        title="Describe cloud computing",
        url=module_data.get("url", ""),
        duration_minutes=module_data.get("durationInMinutes") or 30,
        description="Test content for development",
        units=[unit]
    )
    
    path = LearningPath(
        uid="test-path",
        title="Test Path - Azure Fundamentals",
        url="",
        duration_minutes=30,
        description="Test content for development",
        modules=[module]
    )
    
    result = DeepDiscoveryResult(
        certification_id="test",
        certification_url="",
        learning_paths=[path],
        total_modules=1,
        total_units=1,
        total_words=word_count,
        estimated_episodes=1
    )
    
    print(f"\nTest discovery complete!")
    print(f"  Words: {word_count}")
    print(f"  Episodes: 1")
    
    return result


def result_to_dict(
    result: DeepDiscoveryResult,
    exam_skills: list[dict] = None,
    coverage_result: dict = None,
    confidence: dict = None,
) -> dict:
    """
    Convert result to JSON-serializable dict.
    
    Output includes both detailed structure AND workflow-compatible format
    (skillsOutline, sourceUrls) for use with generate-content.yml workflow.
    
    Skill dicts use "name"/"weight", matching the exam skills outline format
    that generate_episodes.py consumes.
    
    IMPORTANT: Modules are deduplicated by UID since the same module can appear
    in multiple learning paths. This prevents processing duplicates and wasting
    AI/compute resources.
    
    Args:
        result: DeepDiscoveryResult from learning paths discovery
        exam_skills: Optional list of exam skills from study guide (comprehensive mode)
        coverage_result: Optional coverage sweep results from coverage_sweep()
        confidence: Optional confidence score from compute_confidence_score()
    """
    # Build skills outline from learning paths (workflow-compatible format)
    # Each UNIQUE module becomes a "skill" and each unit becomes a "topic"
    # Deduplicate by module UID since same module appears in multiple paths
    skills_outline = []
    source_urls = set()
    seen_module_uids = set()
    duplicate_count = 0
    
    for path in result.learning_paths:
        for module in path.modules:
            # Skip duplicate modules (same module in multiple learning paths)
            if module.uid in seen_module_uids:
                duplicate_count += 1
                continue
            seen_module_uids.add(module.uid)
            
            skill = {
                "name": module.title,
                "weight": None,  # Deep discovery doesn't have weights
                "learningPath": path.title,
                "topics": [],
                "sourceUrls": [],  # Per-skill source URLs
                "isExamSkill": False  # Flag: this is learning path content
            }
            
            for unit in module.units:
                skill["topics"].append(unit.title)  # Just the topic name string
                if unit.url:
                    source_urls.add(unit.url)
                    skill["sourceUrls"].append(unit.url)
            
            skills_outline.append(skill)
    
    if duplicate_count > 0:
        print(f"  Deduplicated: removed {duplicate_count} duplicate module(s)")
    
    # Add exam skills to the outline (comprehensive mode)
    # These come AFTER learning paths to provide: foundations → specific skills
    if exam_skills:
        print(f"  Adding {len(exam_skills)} exam skill objectives to outline")
        for skill in exam_skills:
            # Prefix exam skills to distinguish in episode titles
            skills_outline.append({
                "name": f"[Exam Skill] {skill['name']}",
                "weight": skill.get("weight"),
                "topics": skill.get("topics", []),
                "sourceUrls": skill.get("sourceUrls", []),
                "isExamSkill": True
            })

    # Merge supplemental URLs from coverage sweep into the global source list
    if coverage_result:
        for url in coverage_result.get("supplementalUrls", []):
            source_urls.add(url)

    output = {
        # Workflow-compatible format (required by generate-content.yml)
        "skillsOutline": skills_outline,
        "sourceUrls": list(source_urls),
        
        # Detailed discovery result
        "certificationId": result.certification_id,
        "certificationUrl": result.certification_url,
        "learningPaths": [
            {
                "uid": path.uid,
                "title": path.title,
                "url": path.url,
                "durationMinutes": path.duration_minutes,
                "description": path.description,
                "modules": [
                    {
                        "uid": mod.uid,
                        "title": mod.title,
                        "url": mod.url,
                        "durationMinutes": mod.duration_minutes,
                        "description": mod.description,
                        "units": [
                            {
                                "uid": unit.uid,
                                "title": unit.title,
                                "url": unit.url,
                                "durationMinutes": unit.duration_minutes,
                                "content": unit.content,
                                "wordCount": unit.word_count
                            }
                            for unit in mod.units
                        ]
                    }
                    for mod in path.modules
                ]
            }
            for path in result.learning_paths
        ],
        "totalModules": result.total_modules,
        "totalUnits": result.total_units,
        "totalWords": result.total_words,
        "unitsFailed": result.units_failed,
        "resolution": result.resolution,
        "estimatedEpisodes": result.estimated_episodes
    }

    # Attach coverage and confidence (comprehensive mode)
    if confidence:
        output["confidence"] = confidence
    if coverage_result:
        output["coverageReport"] = {
            "coveredCount": len(coverage_result.get("covered", [])),
            "supplementedCount": len(coverage_result.get("supplemented", [])),
            "gapCount": len(coverage_result.get("gaps", [])),
            "gaps": coverage_result.get("gaps", []),
            "supplementalUrls": coverage_result.get("supplementalUrls", []),
        }

    return output


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Deep discover Microsoft Learn content using Catalog API"
    )
    parser.add_argument(
        "--certification-id",
        help="Microsoft certification ID (e.g., dp-700). Use 'test' for test mode.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run in test mode (single unit, minimal cost)"
    )
    parser.add_argument(
        "--max-paths",
        type=int,
        help="Limit number of learning paths"
    )
    parser.add_argument(
        "--max-modules",
        type=int,
        help="Limit modules per learning path"
    )
    parser.add_argument(
        "--max-units",
        type=int,
        help="Limit units per module"
    )
    parser.add_argument(
        "--skip-content",
        action="store_true",
        help="Skip fetching unit content (structure only)"
    )
    parser.add_argument(
        "--comprehensive",
        action="store_true",
        help="Include exam skills outline in addition to learning paths (recommended)"
    )
    parser.add_argument(
        "--output-file",
        default="deep_discovery_results.json",
        help="Output JSON file path"
    )
    
    args = parser.parse_args()
    
    # Determine mode
    exam_skills = None
    coverage_result = None
    confidence = None
    catalog = None

    if args.test or args.certification_id == "test":
        result = discover_test_content()
    elif args.certification_id:
        # Pre-fetch catalog once so it can be reused by coverage sweep
        catalog = fetch_catalog()

        result = deep_discover(
            certification_id=args.certification_id,
            max_paths=args.max_paths,
            max_modules_per_path=args.max_modules,
            max_units_per_module=args.max_units,
            skip_content=args.skip_content,
            catalog=catalog,
        )
        
        # Fetch exam skills for comprehensive mode
        if args.comprehensive:
            print("\n" + "=" * 60)
            print("COMPREHENSIVE MODE: Adding exam skills outline")
            exam_skills = fetch_exam_skills_outline(args.certification_id)

            if exam_skills:
                # Build flat list of discovered modules for coverage comparison
                discovered_modules = []
                for path in result.learning_paths:
                    for mod in path.modules:
                        discovered_modules.append({
                            "title": mod.title,
                            "topics": [u.title for u in mod.units],
                            "sourceUrls": [u.url for u in mod.units if u.url],
                        })

                # Run coverage sweep with fallback chain
                coverage_result = coverage_sweep(exam_skills, discovered_modules, catalog)

                # Compute confidence score
                confidence = compute_confidence_score(coverage_result, exam_skills)
    else:
        parser.error("Either --certification-id or --test is required")
    
    # Save results
    output = result_to_dict(
        result,
        exam_skills=exam_skills,
        coverage_result=coverage_result,
        confidence=confidence,
    )
    
    # Print summary
    total_skills = len(output.get("skillsOutline", []))
    total_topics = sum(len(s.get("topics", [])) for s in output.get("skillsOutline", []))
    learning_path_skills = len([s for s in output.get("skillsOutline", []) if not s.get("isExamSkill")])
    exam_skill_count = len([s for s in output.get("skillsOutline", []) if s.get("isExamSkill")])
    
    print("\n" + "=" * 60)
    print("DISCOVERY SUMMARY")
    print("=" * 60)
    print(f"  Learning Path modules: {learning_path_skills}")
    print(f"  Exam skill objectives: {exam_skill_count}")
    print(f"  Total skill domains: {total_skills}")
    print(f"  Total topics: {total_topics}")
    if confidence:
        print(f"  Confidence Score: {confidence['overallScore']}% (Grade: {confidence['grade']})")
        print(f"  Coverage gaps: {confidence['breakdown'].get('gap', {}).get('count', 0)}")
    
    with open(args.output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\nResults saved to {args.output_file}")


if __name__ == "__main__":
    main()