"""
Deep discovery: Use Microsoft Learn Catalog API for comprehensive content discovery.

This module uses the official Microsoft Learn Catalog API to discover all learning paths,
modules, and units for a certification, then fetches the actual content from each unit.

Content hierarchy:
- Certification → Learning Paths → Modules → Units → Content
"""

import argparse
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Constants
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
REQUEST_DELAY = 0.3  # Be respectful to Microsoft's servers
CATALOG_URL = "https://learn.microsoft.com/api/catalog/"

# Known certification to learning path mappings (for certs where we know the exact paths)
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


def deep_discover(
    certification_id: str,
    max_paths: Optional[int] = None,
    max_modules_per_path: Optional[int] = None,
    max_units_per_module: Optional[int] = None,
    skip_content: bool = False
) -> DeepDiscoveryResult:
    """
    Perform deep discovery using the Microsoft Learn Catalog API.
    
    Args:
        certification_id: Microsoft certification ID (e.g., 'dp-700')
        max_paths: Limit number of learning paths (for testing)
        max_modules_per_path: Limit modules per path (for testing)
        max_units_per_module: Limit units per module (for testing)
        skip_content: If True, don't fetch unit content (faster for structure only)
    
    Returns:
        DeepDiscoveryResult with all discovered content
    """
    print(f"Starting deep discovery for {certification_id}")
    print("=" * 60)
    
    # Fetch the catalog
    catalog = fetch_catalog()
    
    # Build lookup tables
    paths_by_uid = {p["uid"]: p for p in catalog.get("learningPaths", [])}
    modules_by_uid = {m["uid"]: m for m in catalog.get("modules", [])}
    units_by_uid = {u["uid"]: u for u in catalog.get("units", [])}
    
    # Get learning path UIDs for this certification
    cert_lower = certification_id.lower()
    if cert_lower in CERTIFICATION_PATH_UIDS:
        path_uids = CERTIFICATION_PATH_UIDS[cert_lower]
        print(f"Using {len(path_uids)} configured learning paths for {certification_id}")
    else:
        # Try to find paths by searching (less reliable)
        path_uids = []
        for p in catalog.get("learningPaths", []):
            # Check if path is associated with this cert
            products = p.get("products", [])
            if any(cert_lower in str(prod).lower() for prod in products):
                path_uids.append(p["uid"])
        print(f"Found {len(path_uids)} learning paths by product search")
    
    if max_paths:
        path_uids = path_uids[:max_paths]
    
    learning_paths = []
    total_modules = 0
    total_units = 0
    total_words = 0
    
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
                        print(f"        [{k+1}/{len(unit_uids)}] {unit.title} (fetch error: {e})")
                
                module.units.append(unit)
                total_units += 1
            
            path.modules.append(module)
            total_modules += 1
        
        learning_paths.append(path)
    
    # Calculate estimated episodes (assuming ~1400 words per episode)
    words_per_episode = 1400
    estimated_episodes = max(1, total_words // words_per_episode) if total_words > 0 else total_units
    
    result = DeepDiscoveryResult(
        certification_id=certification_id,
        certification_url=f"https://learn.microsoft.com/en-us/credentials/certifications/exams/{certification_id}/",
        learning_paths=learning_paths,
        total_modules=total_modules,
        total_units=total_units,
        total_words=total_words,
        estimated_episodes=estimated_episodes
    )
    
    print("\n" + "=" * 60)
    print("Discovery complete!")
    print(f"  Learning Paths: {len(learning_paths)}")
    print(f"  Total Modules: {total_modules}")
    print(f"  Total Units: {total_units}")
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


def result_to_dict(result: DeepDiscoveryResult) -> dict:
    """
    Convert result to JSON-serializable dict.
    
    Output includes both detailed structure AND workflow-compatible format
    (skillsOutline, sourceUrls) for use with generate-content.yml workflow.
    
    Field names match discover_exam_content.py format:
    - skill["name"] (not "skillName")
    - skill["weight"] (not "weightPercentage")
    
    IMPORTANT: Modules are deduplicated by UID since the same module can appear
    in multiple learning paths. This prevents processing duplicates and wasting
    AI/compute resources.
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
                "name": module.title,  # Match discover_exam_content format
                "weight": None,  # Deep discovery doesn't have weights
                "learningPath": path.title,
                "topics": [],
                "sourceUrls": []  # Per-skill source URLs
            }
            
            for unit in module.units:
                skill["topics"].append(unit.title)  # Just the topic name string
                if unit.url:
                    source_urls.add(unit.url)
                    skill["sourceUrls"].append(unit.url)
            
            skills_outline.append(skill)
    
    if duplicate_count > 0:
        print(f"  Deduplicated: removed {duplicate_count} duplicate module(s)")
    
    return {
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
        "estimatedEpisodes": result.estimated_episodes
    }


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
        "--output-file",
        default="deep_discovery_results.json",
        help="Output JSON file path"
    )
    
    args = parser.parse_args()
    
    # Determine mode
    if args.test or args.certification_id == "test":
        result = discover_test_content()
    elif args.certification_id:
        result = deep_discover(
            certification_id=args.certification_id,
            max_paths=args.max_paths,
            max_modules_per_path=args.max_modules,
            max_units_per_module=args.max_units,
            skip_content=args.skip_content
        )
    else:
        parser.error("Either --certification-id or --test is required")
    
    # Save results
    output = result_to_dict(result)
    with open(args.output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\nResults saved to {args.output_file}")


if __name__ == "__main__":
    main()
