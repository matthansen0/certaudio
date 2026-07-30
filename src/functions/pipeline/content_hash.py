"""Canonical page hashing, shared by the indexer and the delta check.

Both sides must extract text identically. If the hash stored at index time is
not computed the same way as the one compared at refresh time, every page reads
as changed and a "selective" refresh silently becomes a full-cost regeneration
of the whole certification. test_delta.py pins the two together.
"""

import hashlib

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# Stripped before hashing: these change on every deploy of Microsoft's site
# without the article itself changing.
NON_CONTENT_TAGS = ["nav", "footer", "aside", "script", "style"]


def extract_main_text(html: str) -> str:
    """Return the article text of a Learn page, normalised for comparison."""
    soup = BeautifulSoup(html, "lxml")

    for element in soup.find_all(NON_CONTENT_TAGS):
        element.decompose()

    main_content = (
        soup.find("main")
        or soup.find("article")
        or soup.find("div", class_="content")
    )
    text = (main_content or soup).get_text(separator=" ", strip=True)
    return " ".join(text.split())


def compute_content_hash(html: str) -> str:
    """Hash of a page's article text. Stable across navigation/chrome changes."""
    return hashlib.sha256(extract_main_text(html).encode()).hexdigest()


def fetch_page_content(url: str) -> str:
    """Fetch HTML content from a URL."""
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text
