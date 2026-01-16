"""
Retrieve relevant content from Azure AI Search for RAG-based script generation.
"""

import hashlib
import os
from dataclasses import dataclass

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from openai import AzureOpenAI
from promptflow.core import tool


@dataclass
class RetrievedContent:
    """Content retrieved from AI Search."""

    content: str
    source_urls: list[str]
    content_hash: str


def get_embedding(text: str, openai_client: AzureOpenAI) -> list[float]:
    """Generate embedding for text using Azure OpenAI."""
    response = openai_client.embeddings.create(
        model="text-embedding-3-large",
        input=text,
    )
    return response.data[0].embedding


@tool
def retrieve_content(
    certification_id: str,
    skill_domain: str,
    skill_topics: list[str],
) -> dict:
    """
    Retrieve relevant content from Azure AI Search.

    Args:
        certification_id: Microsoft certification ID
        skill_domain: The skill domain to retrieve content for
        skill_topics: List of topics within this domain

    Returns:
        Dict with content, source_urls, and content_hash
    """
    # Get configuration from environment
    search_endpoint = os.environ.get("SEARCH_ENDPOINT")
    openai_endpoint = os.environ.get("OPENAI_ENDPOINT")

    if not search_endpoint or not openai_endpoint:
        raise ValueError("SEARCH_ENDPOINT and OPENAI_ENDPOINT environment variables required")

    credential = DefaultAzureCredential()

    # Initialize clients
    search_client = SearchClient(
        endpoint=search_endpoint,
        index_name=f"{certification_id}-content",
        credential=credential,
    )

    openai_client = AzureOpenAI(
        azure_endpoint=openai_endpoint,
        azure_ad_token_provider=lambda: credential.get_token(
            "https://cognitiveservices.azure.com/.default"
        ).token,
        api_version="2024-02-01",
    )

    # Build search query from domain and topics
    query_text = f"{skill_domain}\n" + "\n".join(skill_topics)

    # Generate embedding for hybrid search
    query_embedding = get_embedding(query_text, openai_client)

    # Perform hybrid search (keyword + vector)
    vector_query = VectorizedQuery(
        vector=query_embedding,
        k_nearest_neighbors=10,
        fields="contentVector",
    )

    results = search_client.search(
        search_text=query_text,
        vector_queries=[vector_query],
        select=["content", "sourceUrl", "title", "chunkId"],
        top=15,
    )

    # Aggregate results
    content_parts = []
    source_urls = set()

    for result in results:
        content_parts.append(f"## {result.get('title', 'Content')}\n\n{result['content']}")
        if result.get("sourceUrl"):
            source_urls.add(result["sourceUrl"])

    # Combine all retrieved content
    combined_content = "\n\n---\n\n".join(content_parts)

    # Compute hash of combined content for delta tracking
    content_hash = hashlib.sha256(combined_content.encode()).hexdigest()

    return {
        "content": combined_content,
        "source_urls": list(source_urls),
        "content_hash": content_hash,
    }
