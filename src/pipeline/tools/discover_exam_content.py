"""
Discover and extract content from Microsoft Learn exam pages.
Auto-discovers the skills outline and linked documentation for any Microsoft certification.
"""

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from bs4 import BeautifulSoup

# Microsoft Learn exam page patterns
EXAM_PAGE_PATTERNS = {
    "ai-102": "https://learn.microsoft.com/en-us/credentials/certifications/exams/ai-102/",
    "az-204": "https://learn.microsoft.com/en-us/credentials/certifications/exams/az-204/",
    "az-104": "https://learn.microsoft.com/en-us/credentials/certifications/exams/az-104/",
    "az-900": "https://learn.microsoft.com/en-us/credentials/certifications/exams/az-900/",
    "az-400": "https://learn.microsoft.com/en-us/credentials/certifications/exams/az-400/",
    "az-305": "https://learn.microsoft.com/en-us/credentials/certifications/exams/az-305/",
    "az-500": "https://learn.microsoft.com/en-us/credentials/certifications/exams/az-500/",
    "az-700": "https://learn.microsoft.com/en-us/credentials/certifications/exams/az-700/",
    "dp-900": "https://learn.microsoft.com/en-us/credentials/certifications/exams/dp-900/",
    "dp-100": "https://learn.microsoft.com/en-us/credentials/certifications/exams/dp-100/",
    "dp-203": "https://learn.microsoft.com/en-us/credentials/certifications/exams/dp-203/",
    "dp-300": "https://learn.microsoft.com/en-us/credentials/certifications/exams/dp-300/",
}


@dataclass
class SkillDomain:
    """Represents a skill domain from the exam outline."""

    name: str
    weight: str  # e.g., "25-30%"
    topics: list[str]
    source_urls: list[str]


@dataclass
class DiscoveryResult:
    """Results from exam content discovery."""

    certification_id: str
    exam_page_url: str
    skill_domains: list[SkillDomain]
    all_source_urls: list[str]
    total_topics: int


def get_exam_page_url(certification_id: str, override_url: Optional[str] = None) -> str:
    """Get the exam page URL for a certification ID."""
    if override_url:
        return override_url

    cert_lower = certification_id.lower()
    if cert_lower in EXAM_PAGE_PATTERNS:
        return EXAM_PAGE_PATTERNS[cert_lower]

    # Try to construct URL for unknown certifications
    return f"https://learn.microsoft.com/en-us/credentials/certifications/exams/{cert_lower}/"


def fetch_page_content(url: str) -> str:
    """Fetch HTML content from a URL."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    # Force UTF-8 decoding; Microsoft Learn pages are UTF-8 but sometimes lack charset header
    response.encoding = 'utf-8'
    return response.text


def fetch_page_content_with_effective_url(url: str) -> tuple[str, str]:
    """Fetch HTML content and return the effective URL after redirects."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
    response.raise_for_status()
    # Force UTF-8 decoding; Microsoft Learn pages are UTF-8 but sometimes lack charset header
    response.encoding = 'utf-8'
    return response.text, response.url


def extract_skills_outline(html: str, base_url: str) -> list[SkillDomain]:
    """Extract the skills measured outline from exam page HTML."""
    soup = BeautifulSoup(html, "lxml")
    skill_domains = []

    # Find the skills measured section
    # Microsoft Learn uses various heading structures, so we search flexibly
    skills_section = None

    # Try to find by heading text
    for heading in soup.find_all(["h2", "h3"]):
        if "skills" in heading.get_text().lower() and "measured" in heading.get_text().lower():
            skills_section = heading
            break

    if not skills_section:
        # Fallback: look for the exam objectives container
        skills_section = soup.find("div", class_=re.compile(r"exam-?skills|objectives", re.I))

    if not skills_section:
        print("Warning: Could not locate skills measured section")
        return skill_domains

    # Parse skill domains - they're typically in expandable sections or lists
    # Look for domain headings (usually have percentage weights)
    # Match various dash characters: hyphen (-), en-dash (–), em-dash (—), and Unicode variants
    domain_pattern = re.compile(r"(.+?)\s*\((\d+[\-\u2010-\u2015]\d+%)\)")

    current_domain = None
    current_topics = []
    current_urls = []

    # Walk through siblings and children to find domains and topics
    for element in skills_section.find_all_next(["h3", "h4", "li", "a", "p"]):
        text = element.get_text(strip=True)

        # Check if this is a domain heading
        match = domain_pattern.search(text)
        if match and element.name in ["h3", "h4", "p"]:
            # Save previous domain if exists
            if current_domain:
                skill_domains.append(
                    SkillDomain(
                        name=current_domain["name"],
                        weight=current_domain["weight"],
                        topics=current_topics,
                        source_urls=list(set(current_urls)),
                    )
                )

            current_domain = {"name": match.group(1).strip(), "weight": match.group(2)}
            current_topics = []
            current_urls = []

        # Check if this is a topic
        elif element.name == "li" and current_domain:
            topic_text = element.get_text(strip=True)
            if topic_text and len(topic_text) > 5:  # Filter out empty or trivial items
                current_topics.append(topic_text)

                # Extract any links within this topic
                for link in element.find_all("a", href=True):
                    href = link["href"]
                    if "learn.microsoft.com" in href or href.startswith("/"):
                        full_url = urljoin(base_url, href)
                        current_urls.append(full_url)

        # Check for linked documentation
        elif element.name == "a" and current_domain:
            href = element.get("href", "")
            if "learn.microsoft.com" in href or (
                href.startswith("/") and "azure" in href.lower()
            ):
                full_url = urljoin(base_url, href)
                current_urls.append(full_url)

        # Stop if we've gone past the skills section
        if element.name in ["h2"] and "skills" not in element.get_text().lower():
            break

    # Don't forget the last domain
    if current_domain:
        skill_domains.append(
            SkillDomain(
                name=current_domain["name"],
                weight=current_domain["weight"],
                topics=current_topics,
                source_urls=list(set(current_urls)),
            )
        )

    # Deduplicate domains by normalized name (handles dash variants and whitespace)
    # Keep only the version with the most topics for each domain
    def normalize_name(name: str) -> str:
        # Normalize dashes and whitespace
        import unicodedata
        normalized = unicodedata.normalize("NFKC", name.lower().strip())
        # Replace various dashes with standard hyphen
        for dash in ["\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015"]:
            normalized = normalized.replace(dash, "-")
        return normalized

    best_domains: dict[str, SkillDomain] = {}
    for domain in skill_domains:
        key = normalize_name(domain.name)
        if key not in best_domains or len(domain.topics) > len(best_domains[key].topics):
            best_domains[key] = domain

    # Filter out domains with no real topics (likely parsing artifacts)
    # A real topic should not match the domain pattern (contain percentage weight)
    domain_pattern = re.compile(r"\(\d+[\-\u2010-\u2015]\d+%\)")
    deduplicated = []
    for domain in best_domains.values():
        real_topics = [t for t in domain.topics if not domain_pattern.search(t)]
        if real_topics:
            deduplicated.append(
                SkillDomain(
                    name=domain.name,
                    weight=domain.weight,
                    topics=real_topics,
                    source_urls=domain.source_urls,
                )
            )

    return deduplicated


def discover_linked_content(skill_domains: list[SkillDomain]) -> list[str]:
    """Discover additional linked content from the skill domains."""
    all_urls = set()

    for domain in skill_domains:
        all_urls.update(domain.source_urls)

        # For each source URL, fetch and find additional relevant links
        for url in domain.source_urls[:5]:  # Limit to avoid too many requests
            try:
                html = fetch_page_content(url)
                soup = BeautifulSoup(html, "lxml")

                # Find links to related documentation
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    if (
                        "learn.microsoft.com" in href
                        and "/azure/" in href
                        and href not in all_urls
                    ):
                        all_urls.add(href)

            except Exception as e:
                print(f"Warning: Could not fetch {url}: {e}")
                continue

    return list(all_urls)


def save_discovery_to_cosmos(
    result: DiscoveryResult, cosmos_endpoint: str, database_name: str = "certaudio"
) -> None:
    """Save discovery results to Cosmos DB for future reference."""
    credential = DefaultAzureCredential()
    client = CosmosClient(cosmos_endpoint, credential)
    database = client.get_database_client(database_name)
    container = database.get_container_client("sources")

    # Save each source URL with its metadata
    for domain in result.skill_domains:
        for url in domain.source_urls:
            content_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
            doc = {
                "id": f"{result.certification_id}-{content_hash}",
                "certificationId": result.certification_id,
                "url": url,
                "skillDomain": domain.name,
                "contentHash": "",  # Will be populated when content is indexed
                "lastChecked": None,
                "episodeRefs": [],
            }
            container.upsert_item(doc)


def discover_exam_content(
    certification_id: str,
    exam_page_url: Optional[str] = None,
    cosmos_endpoint: Optional[str] = None,
) -> DiscoveryResult:
    """
    Main function to discover all content for a certification exam.

    Args:
        certification_id: Microsoft certification ID (e.g., 'ai-102')
        exam_page_url: Override URL for the exam page
        cosmos_endpoint: Cosmos DB endpoint for persisting results

    Returns:
        DiscoveryResult with all discovered content
    """
    # Get the exam page URL (or override)
    url = get_exam_page_url(certification_id, exam_page_url)
    print(f"Discovering content from: {url}")

    # Fetch and parse the exam page (use effective URL after redirects for correct link resolution)
    html, effective_url = fetch_page_content_with_effective_url(url)
    skill_domains = extract_skills_outline(html, effective_url)

    # Some newer certifications (e.g., dp-700) redirect exam URLs to certification pages
    # that do not contain a "Skills measured" outline. In that case, fall back to the
    # official study guide page for the certification.
    if not skill_domains and not exam_page_url:
        study_guide_url = (
            f"https://learn.microsoft.com/en-us/credentials/certifications/resources/study-guides/"
            f"{certification_id.lower()}"
        )
        print(
            "Warning: No skills outline found on exam page; "
            f"trying study guide: {study_guide_url}"
        )
        html, effective_url = fetch_page_content_with_effective_url(study_guide_url)
        skill_domains = extract_skills_outline(html, effective_url)

    # Track the final source page used for discovery
    url = effective_url

    print(f"Found {len(skill_domains)} skill domains")
    for domain in skill_domains:
        print(f"  - {domain.name} ({domain.weight}): {len(domain.topics)} topics")

    # Discover additional linked content
    all_urls = discover_linked_content(skill_domains)
    print(f"Discovered {len(all_urls)} total source URLs")

    # Calculate total topics
    total_topics = sum(len(d.topics) for d in skill_domains)

    result = DiscoveryResult(
        certification_id=certification_id,
        exam_page_url=url,
        skill_domains=skill_domains,
        all_source_urls=all_urls,
        total_topics=total_topics,
    )

    # Save to Cosmos DB if endpoint provided
    if cosmos_endpoint:
        save_discovery_to_cosmos(result, cosmos_endpoint)
        print("Saved discovery results to Cosmos DB")

    return result


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Discover Microsoft exam content")
    parser.add_argument(
        "--certification-id",
        required=True,
        help="Microsoft certification ID (e.g., ai-102)",
    )
    parser.add_argument(
        "--exam-page-url",
        default="",
        help="Override exam page URL",
    )
    parser.add_argument(
        "--output-file",
        default="discovery_results.json",
        help="Output JSON file path",
    )

    args = parser.parse_args()

    # Get endpoints from environment
    cosmos_endpoint = os.environ.get("COSMOS_DB_ENDPOINT")

    # Run discovery
    result = discover_exam_content(
        certification_id=args.certification_id,
        exam_page_url=args.exam_page_url or None,
        cosmos_endpoint=cosmos_endpoint,
    )

    # Output results
    output = {
        "certificationId": result.certification_id,
        "examPageUrl": result.exam_page_url,
        "skillsOutline": [
            {
                "name": d.name,
                "weight": d.weight,
                "topics": d.topics,
                "sourceUrls": d.source_urls,
            }
            for d in result.skill_domains
        ],
        "sourceUrls": result.all_source_urls,
        "totalTopics": result.total_topics,
    }

    with open(args.output_file, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Results saved to {args.output_file}")


if __name__ == "__main__":
    main()
