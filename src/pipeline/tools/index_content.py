"""
Index content into Azure AI Search for RAG retrieval.
"""

import argparse
import hashlib
import json
import os
from typing import Optional

import requests
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchProfile,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
)
from bs4 import BeautifulSoup
from openai import AzureOpenAI


def create_search_index(index_client: SearchIndexClient, index_name: str) -> None:
    """Create the search index with vector search capabilities."""
    
    fields = [
        SearchField(name="id", type=SearchFieldDataType.String, key=True),
        SearchField(name="certificationId", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="sourceUrl", type=SearchFieldDataType.String),
        SearchField(name="title", type=SearchFieldDataType.String, searchable=True),
        SearchField(name="content", type=SearchFieldDataType.String, searchable=True),
        SearchField(name="chunkId", type=SearchFieldDataType.Int32),
        SearchField(name="contentHash", type=SearchFieldDataType.String),
        SearchField(
            name="contentVector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=3072,  # text-embedding-3-large
            vector_search_profile_name="default-profile",
        ),
    ]
    
    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(name="default-algorithm"),
        ],
        profiles=[
            VectorSearchProfile(
                name="default-profile",
                algorithm_configuration_name="default-algorithm",
            ),
        ],
    )
    
    semantic_config = SemanticConfiguration(
        name="default-semantic",
        prioritized_fields=SemanticPrioritizedFields(
            title_field=SemanticField(field_name="title"),
            content_fields=[SemanticField(field_name="content")],
        ),
    )
    
    index = SearchIndex(
        name=index_name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=SemanticSearch(configurations=[semantic_config]),
    )
    
    index_client.create_or_update_index(index)
    print(f"Created/updated index: {index_name}")


def fetch_and_chunk_content(url: str, chunk_size: int = 1000) -> list[dict]:
    """Fetch a URL and split into chunks."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return []
    
    soup = BeautifulSoup(response.text, "lxml")
    
    # Remove non-content elements
    for element in soup.find_all(["nav", "footer", "aside", "script", "style"]):
        element.decompose()
    
    # Get title
    title = soup.find("h1")
    title_text = title.get_text(strip=True) if title else "Untitled"
    
    # Get main content
    main = soup.find("main") or soup.find("article") or soup.find("div", class_="content")
    if not main:
        main = soup.body
    
    if not main:
        return []
    
    # Extract text and split into chunks
    text = main.get_text(separator="\n", strip=True)
    
    # Simple chunking by paragraphs/sentences
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    
    chunks = []
    current_chunk = []
    current_length = 0
    
    for para in paragraphs:
        if current_length + len(para) > chunk_size and current_chunk:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = []
            current_length = 0
        
        current_chunk.append(para)
        current_length += len(para)
    
    if current_chunk:
        chunks.append("\n\n".join(current_chunk))
    
    return [
        {
            "title": title_text,
            "content": chunk,
            "chunkId": i,
        }
        for i, chunk in enumerate(chunks)
    ]


def get_embedding(text: str, openai_client: AzureOpenAI) -> list[float]:
    """Generate embedding for text."""
    response = openai_client.embeddings.create(
        model="text-embedding-3-large",
        input=text[:8000],  # Truncate to model limit
    )
    return response.data[0].embedding


def index_content(
    certification_id: str,
    source_urls: list[str],
    search_endpoint: str,
    openai_endpoint: str,
    update_mode: bool = False,
) -> None:
    """Index content from source URLs into Azure AI Search."""
    
    credential = DefaultAzureCredential()
    
    # Initialize clients
    index_name = f"{certification_id}-content"
    
    index_client = SearchIndexClient(
        endpoint=search_endpoint,
        credential=credential,
    )
    
    search_client = SearchClient(
        endpoint=search_endpoint,
        index_name=index_name,
        credential=credential,
    )
    
    openai_client = AzureOpenAI(
        azure_endpoint=openai_endpoint,
        azure_ad_token_provider=lambda: credential.get_token(
            "https://cognitiveservices.azure.com/.default"
        ).token,
        api_version="2024-02-01",
    )
    
    # Create index if needed
    if not update_mode:
        create_search_index(index_client, index_name)
    
    # Process each URL
    documents = []
    
    for url in source_urls:
        print(f"Processing: {url}")
        chunks = fetch_and_chunk_content(url)
        
        for chunk in chunks:
            # Generate embedding
            embedding = get_embedding(chunk["content"], openai_client)
            
            # Create document ID
            url_hash = hashlib.sha256(url.encode()).hexdigest()[:8]
            doc_id = f"{certification_id}-{url_hash}-{chunk['chunkId']}"
            
            doc = {
                "id": doc_id,
                "certificationId": certification_id,
                "sourceUrl": url,
                "title": chunk["title"],
                "content": chunk["content"],
                "chunkId": chunk["chunkId"],
                "contentHash": hashlib.sha256(chunk["content"].encode()).hexdigest()[:16],
                "contentVector": embedding,
            }
            
            documents.append(doc)
            
            # Batch upload every 100 documents
            if len(documents) >= 100:
                search_client.upload_documents(documents)
                print(f"Uploaded {len(documents)} documents")
                documents = []
    
    # Upload remaining documents
    if documents:
        search_client.upload_documents(documents)
        print(f"Uploaded {len(documents)} documents")
    
    print(f"Indexing complete for {certification_id}")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Index content into Azure AI Search")
    parser.add_argument(
        "--certification-id",
        required=True,
        help="Microsoft certification ID",
    )
    parser.add_argument(
        "--source-urls",
        required=True,
        help="JSON array of source URLs",
    )
    parser.add_argument(
        "--update-mode",
        type=lambda x: x.lower() == "true",
        default=False,
        help="Update existing index instead of recreating",
    )
    
    args = parser.parse_args()
    
    # Parse source URLs
    source_urls = json.loads(args.source_urls)
    
    # Get endpoints from environment
    search_endpoint = os.environ.get("SEARCH_ENDPOINT")
    openai_endpoint = os.environ.get("OPENAI_ENDPOINT")
    
    if not search_endpoint or not openai_endpoint:
        raise ValueError("SEARCH_ENDPOINT and OPENAI_ENDPOINT required")
    
    index_content(
        certification_id=args.certification_id,
        source_urls=source_urls,
        search_endpoint=search_endpoint,
        openai_endpoint=openai_endpoint,
        update_mode=args.update_mode,
    )


if __name__ == "__main__":
    main()
