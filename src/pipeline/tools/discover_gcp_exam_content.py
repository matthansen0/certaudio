"""
Discover and extract content from Google Cloud certification exam pages.
Auto-discovers the skills outline and linked documentation for any GCP certification.

This is a proof-of-concept to demonstrate GCP certification content discovery
similar to the Microsoft Learn discovery system.
"""

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Headers for requests
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# GCP certification patterns
GCP_CERTIFICATION_URLS = {
    "cloud-digital-leader": "https://cloud.google.com/learn/certification/cloud-digital-leader",
    "cloud-engineer": "https://cloud.google.com/certification/cloud-engineer",
    "cloud-architect": "https://cloud.google.com/certification/cloud-architect",
    "data-engineer": "https://cloud.google.com/certification/data-engineer",
    "cloud-developer": "https://cloud.google.com/certification/cloud-developer",
    "cloud-devops-engineer": "https://cloud.google.com/certification/cloud-devops-engineer",
    "cloud-security-engineer": "https://cloud.google.com/certification/cloud-security-engineer",
    "cloud-network-engineer": "https://cloud.google.com/certification/cloud-network-engineer",
    "machine-learning-engineer": "https://cloud.google.com/certification/machine-learning-engineer",
}

# GCP exam guide PDFs (these are static hosted files)
GCP_EXAM_GUIDES = {
    "cloud-digital-leader": "https://services.google.com/fh/files/misc/cloud_digital_leader_exam_guide_english.pdf",
}

# GCP Cloud Skills Boost learning paths
GCP_LEARNING_PATHS = {
    "cloud-digital-leader": "https://www.cloudskillsboost.google/paths/9",
}

# Mapping of exam topics to GCP documentation areas
GCP_DOC_AREAS = {
    "compute": "https://docs.cloud.google.com/compute/docs",
    "storage": "https://docs.cloud.google.com/storage/docs",
    "bigquery": "https://docs.cloud.google.com/bigquery/docs",
    "kubernetes": "https://docs.cloud.google.com/kubernetes-engine/docs",
    "cloud-run": "https://docs.cloud.google.com/run/docs",
    "app-engine": "https://docs.cloud.google.com/appengine/docs",
    "cloud-functions": "https://docs.cloud.google.com/functions/docs",
    "iam": "https://docs.cloud.google.com/iam/docs",
    "vpc": "https://docs.cloud.google.com/vpc/docs",
    "pub-sub": "https://docs.cloud.google.com/pubsub/docs",
    "dataflow": "https://docs.cloud.google.com/dataflow/docs",
    "cloud-sql": "https://docs.cloud.google.com/sql/docs",
    "spanner": "https://docs.cloud.google.com/spanner/docs",
    "bigtable": "https://docs.cloud.google.com/bigtable/docs",
    "firestore": "https://docs.cloud.google.com/firestore/docs",
    "looker": "https://docs.cloud.google.com/looker/docs",
    "vertex-ai": "https://docs.cloud.google.com/vertex-ai/docs",
    "apigee": "https://docs.cloud.google.com/apigee/docs",
    "cloud-armor": "https://docs.cloud.google.com/armor/docs",
    "security-command-center": "https://docs.cloud.google.com/security-command-center/docs",
    "billing": "https://docs.cloud.google.com/billing/docs",
}


@dataclass
class ExamSection:
    """Represents an exam section from the GCP exam guide."""
    number: str
    name: str
    weight: str  # e.g., "~17%"
    subsections: list[dict] = field(default_factory=list)
    related_docs: list[str] = field(default_factory=list)


@dataclass
class GCPDiscoveryResult:
    """Results from GCP exam content discovery."""
    certification_id: str
    certification_name: str
    exam_page_url: str
    exam_guide_url: Optional[str]
    learning_path_url: Optional[str]
    sections: list[ExamSection]
    all_doc_urls: list[str]
    total_topics: int


# Cloud Digital Leader exam structure (parsed from PDF)
CDL_EXAM_STRUCTURE = [
    {
        "number": "1",
        "name": "Digital Transformation with Google Cloud",
        "weight": "~17%",
        "subsections": [
            {
                "id": "1.1",
                "title": "Why Cloud Technology is Transforming Business",
                "topics": [
                    "Define cloud computing",
                    "Describe how cloud technology shifts the way organizations create value",
                    "Explain the benefits of cloud technology for organizations",
                    "Explain the challenges that cloud technology addresses"
                ]
            },
            {
                "id": "1.2", 
                "title": "Fundamental Cloud Concepts",
                "topics": [
                    "Define IaaS, PaaS, and SaaS",
                    "Compare IaaS, PaaS, SaaS tradeoffs (TCO, flexibility, shared responsibilities)",
                    "Determine which computing model applies to various business scenarios",
                    "Describe the cloud shared responsibility model"
                ]
            }
        ],
        "related_docs": [
            "https://docs.cloud.google.com/docs/overview",
            "https://cloud.google.com/learn/what-is-cloud-computing",
            "https://cloud.google.com/learn/what-is-iaas",
            "https://cloud.google.com/learn/what-is-paas",
            "https://cloud.google.com/learn/what-is-saas",
        ]
    },
    {
        "number": "2",
        "name": "Exploring Data Transformation with Google Cloud",
        "weight": "~16%",
        "subsections": [
            {
                "id": "2.1",
                "title": "The Value of Data",
                "topics": [
                    "Explain how data generates business insights and drives decision-making",
                    "Differentiate between databases, data warehouses, and data lakes",
                    "Explain how organizations can create value from data",
                    "Describe how cloud unlocks value from structured and unstructured data",
                    "Discuss data value chain concepts",
                    "Explain data governance importance"
                ]
            },
            {
                "id": "2.2",
                "title": "Google Cloud Data Management Solutions",
                "topics": [
                    "Differentiate GCP data management options: Cloud Storage, Cloud Spanner, Cloud SQL, Cloud Bigtable, BigQuery, Firestore",
                    "Define relational, non-relational, object storage, SQL, NoSQL",
                    "Describe BigQuery as serverless data warehouse for multicloud",
                    "Differentiate Cloud Storage classes: Standard, Nearline, Coldline, Archive",
                    "Describe database migration and modernization"
                ]
            },
            {
                "id": "2.3",
                "title": "Making Data Useful and Accessible",
                "topics": [
                    "Describe Looker for business intelligence and self-serve analytics",
                    "Discuss BigQuery + Looker for real-time reports and dashboards",
                    "Describe streaming analytics value",
                    "Describe Pub/Sub and Dataflow for data pipelines"
                ]
            }
        ],
        "related_docs": [
            "https://docs.cloud.google.com/bigquery/docs",
            "https://docs.cloud.google.com/storage/docs",
            "https://docs.cloud.google.com/sql/docs",
            "https://docs.cloud.google.com/spanner/docs",
            "https://docs.cloud.google.com/bigtable/docs",
            "https://docs.cloud.google.com/firestore/docs",
            "https://docs.cloud.google.com/looker/docs",
            "https://docs.cloud.google.com/pubsub/docs",
            "https://docs.cloud.google.com/dataflow/docs",
        ]
    },
    {
        "number": "3",
        "name": "Innovating with Google Cloud Artificial Intelligence",
        "weight": "~16%",
        "subsections": [
            {
                "id": "3.1",
                "title": "AI and ML Fundamentals",
                "topics": [
                    "Define artificial intelligence (AI) and machine learning (ML)",
                    "Differentiate AI/ML from data analytics and BI",
                    "Discuss types of problems ML can solve",
                    "Explain ML business value (large datasets, scaling decisions, unstructured data)",
                    "Explain importance of high-quality data for ML",
                    "Discuss explainable and responsible AI"
                ]
            },
            {
                "id": "3.2",
                "title": "Google Cloud's AI and ML Solutions",
                "topics": [
                    "Explain tradeoffs when selecting AI/ML solutions: speed, effort, differentiation, expertise",
                    "Discuss pre-trained APIs, AutoML, custom models for different use cases"
                ]
            },
            {
                "id": "3.3",
                "title": "Building and Using Google Cloud AI/ML Solutions",
                "topics": [
                    "Discuss BigQuery ML for creating ML models using SQL",
                    "Select appropriate pre-trained APIs: Natural Language, Vision, Translation, Speech-to-Text, Text-to-Speech",
                    "Explain training custom ML models with AutoML",
                    "Discuss Vertex AI for custom model development",
                    "Recognize TensorFlow and Cloud TPU for ML training"
                ]
            }
        ],
        "related_docs": [
            "https://docs.cloud.google.com/vertex-ai/docs",
            "https://docs.cloud.google.com/ai-platform/docs",
            "https://docs.cloud.google.com/bigquery/docs/bqml-introduction",
            "https://docs.cloud.google.com/vision/docs",
            "https://docs.cloud.google.com/natural-language/docs",
            "https://docs.cloud.google.com/translate/docs",
            "https://docs.cloud.google.com/speech-to-text/docs",
            "https://docs.cloud.google.com/text-to-speech/docs",
            "https://cloud.google.com/learn/what-is-artificial-intelligence",
        ]
    },
    {
        "number": "4",
        "name": "Modernize Infrastructure and Applications with Google Cloud",
        "weight": "~17%",
        "subsections": [
            {
                "id": "4.1",
                "title": "Cloud Modernization and Migration",
                "topics": [
                    "Discuss benefits of infrastructure and application modernization",
                    "Define migration terms: workload, retire, retain, rehost, lift and shift, replatform, move and improve, refactor, reimagine"
                ]
            },
            {
                "id": "4.2",
                "title": "Computing in the Cloud",
                "topics": [
                    "Define compute terms: VMs, containerization, containers, microservices, serverless, preemptible VMs, Kubernetes, autoscaling, load balancing",
                    "Describe benefits of cloud compute workloads",
                    "Explain choices between compute options",
                    "Discuss Compute Engine for virtual machines",
                    "Discuss rehost migration for legacy applications"
                ]
            },
            {
                "id": "4.3",
                "title": "Serverless Computing",
                "topics": [
                    "Explain benefits of serverless computing",
                    "Discuss Cloud Run, App Engine, Cloud Functions for serverless"
                ]
            },
            {
                "id": "4.4",
                "title": "Containers in the Cloud",
                "topics": [
                    "Discuss advantages of modern cloud application development",
                    "Differentiate between VMs and containers",
                    "Discuss benefits of containers and microservices",
                    "Discuss GKE and Cloud Run for containers"
                ]
            },
            {
                "id": "4.5",
                "title": "The Value of APIs",
                "topics": [
                    "Define application programming interface (API)",
                    "Explain creating value through exposing and monetizing APIs",
                    "Discuss Apigee API Management"
                ]
            },
            {
                "id": "4.6",
                "title": "Hybrid and Multi-cloud",
                "topics": [
                    "Discuss reasons for hybrid or multi-cloud strategy",
                    "Describe GKE Enterprise for hybrid/multicloud management"
                ]
            }
        ],
        "related_docs": [
            "https://docs.cloud.google.com/compute/docs",
            "https://docs.cloud.google.com/kubernetes-engine/docs",
            "https://docs.cloud.google.com/run/docs",
            "https://docs.cloud.google.com/appengine/docs",
            "https://docs.cloud.google.com/functions/docs",
            "https://docs.cloud.google.com/apigee/docs",
            "https://docs.cloud.google.com/anthos/docs",
            "https://cloud.google.com/architecture/migrations",
        ]
    },
    {
        "number": "5",
        "name": "Trust and Security with Google Cloud",
        "weight": "~17%",
        "subsections": [
            {
                "id": "5.1",
                "title": "Trust and Security in the Cloud",
                "topics": [
                    "Describe top cybersecurity threats and business implications",
                    "Differentiate cloud security from on-premises security",
                    "Describe control, compliance, confidentiality, integrity, availability",
                    "Define key security terms and concepts"
                ]
            },
            {
                "id": "5.2",
                "title": "Google's Trusted Infrastructure",
                "topics": [
                    "Describe Google's defense-in-depth approach",
                    "Describe Google's custom data centers and security hardware",
                    "Describe role of encryption in protecting data",
                    "Differentiate authentication, authorization, and auditing",
                    "Describe two-step verification (2SV) and IAM",
                    "Describe network attack protection with Google Cloud Armor",
                    "Define Security Operations (SecOps) in the cloud"
                ]
            },
            {
                "id": "5.3",
                "title": "Google Cloud's Trust Principles and Compliance",
                "topics": [
                    "Discuss Google Cloud's trust principles",
                    "Describe transparency reports and third-party audits",
                    "Describe data sovereignty and data residency options",
                    "Describe compliance resource center and Reports Manager"
                ]
            }
        ],
        "related_docs": [
            "https://docs.cloud.google.com/iam/docs",
            "https://docs.cloud.google.com/armor/docs",
            "https://docs.cloud.google.com/security-command-center/docs",
            "https://cloud.google.com/security",
            "https://cloud.google.com/trust-center",
            "https://docs.cloud.google.com/security/encryption",
        ]
    },
    {
        "number": "6",
        "name": "Scaling with Google Cloud Operations",
        "weight": "~17%",
        "subsections": [
            {
                "id": "6.1",
                "title": "Financial Governance and Managing Cloud Costs",
                "topics": [
                    "Discuss cloud financial governance best practices",
                    "Define cloud cost-management terms",
                    "Discuss resource hierarchy for access control",
                    "Describe resource quota policies and budget threshold rules",
                    "Discuss Cloud Billing Reports for cost visualization"
                ]
            },
            {
                "id": "6.2",
                "title": "Operational Excellence and Reliability at Scale",
                "topics": [
                    "Describe benefits of modernizing operations with GCP",
                    "Define cloud operations terms",
                    "Describe resilient, fault-tolerant, scalable infrastructure",
                    "Define reliability, DevOps, and SRE terms",
                    "Describe Google Cloud Customer Care benefits",
                    "Describe support case lifecycle"
                ]
            },
            {
                "id": "6.3",
                "title": "Sustainability with Google Cloud",
                "topics": [
                    "Describe Google Cloud's sustainability commitment",
                    "Discuss products supporting sustainability goals"
                ]
            }
        ],
        "related_docs": [
            "https://docs.cloud.google.com/billing/docs",
            "https://docs.cloud.google.com/stackdriver/docs",
            "https://docs.cloud.google.com/monitoring/docs",
            "https://docs.cloud.google.com/logging/docs",
            "https://docs.cloud.google.com/resource-manager/docs",
            "https://cloud.google.com/sustainability",
        ]
    }
]


def fetch_page_content(url: str) -> str:
    """Fetch HTML content from a URL."""
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    response.encoding = 'utf-8'
    return response.text


def extract_gcp_doc_content(url: str) -> dict:
    """Extract content from a GCP documentation page."""
    try:
        html = fetch_page_content(url)
        soup = BeautifulSoup(html, "html.parser")
        
        # Remove script and style elements
        for element in soup(["script", "style", "nav", "header", "footer"]):
            element.decompose()
        
        # Find main content area
        main_content = soup.find("article") or soup.find("main") or soup.find("div", class_="devsite-article-body")
        
        if main_content:
            # Extract title
            title_elem = soup.find("h1")
            title = title_elem.get_text(strip=True) if title_elem else "Untitled"
            
            # Extract text content
            text = main_content.get_text(separator="\n", strip=True)
            
            # Extract headings for structure
            headings = []
            for h in main_content.find_all(["h2", "h3"]):
                headings.append({
                    "level": int(h.name[1]),
                    "text": h.get_text(strip=True)
                })
            
            # Count words
            words = len(text.split())
            
            return {
                "url": url,
                "title": title,
                "headings": headings,
                "word_count": words,
                "content_preview": text[:500] + "..." if len(text) > 500 else text,
                "success": True
            }
        else:
            # No main content found, try to get body text
            body = soup.find("body")
            if body:
                text = body.get_text(separator="\n", strip=True)[:2000]
                return {
                    "url": url,
                    "title": soup.find("title").get_text(strip=True) if soup.find("title") else "Untitled",
                    "headings": [],
                    "word_count": len(text.split()),
                    "content_preview": text[:500] + "..." if len(text) > 500 else text,
                    "success": True
                }
            return {
                "url": url,
                "title": "No content found",
                "error": "Could not find main content area",
                "success": False
            }
    except Exception as e:
        return {
            "url": url,
            "title": "Error",
            "error": str(e),
            "success": False
        }


def discover_gcp_certification(certification_id: str) -> GCPDiscoveryResult:
    """
    Discover content for a GCP certification.
    
    Args:
        certification_id: The certification identifier (e.g., "cloud-digital-leader")
    
    Returns:
        GCPDiscoveryResult with exam structure and documentation URLs
    """
    cert_lower = certification_id.lower()
    
    # Get certification URLs
    cert_url = GCP_CERTIFICATION_URLS.get(cert_lower, f"https://cloud.google.com/certification/{cert_lower}")
    exam_guide_url = GCP_EXAM_GUIDES.get(cert_lower)
    learning_path_url = GCP_LEARNING_PATHS.get(cert_lower)
    
    # For Cloud Digital Leader, use the pre-parsed structure
    if cert_lower == "cloud-digital-leader":
        sections = []
        all_doc_urls = []
        total_topics = 0
        
        for section_data in CDL_EXAM_STRUCTURE:
            section = ExamSection(
                number=section_data["number"],
                name=section_data["name"],
                weight=section_data["weight"],
                subsections=section_data["subsections"],
                related_docs=section_data["related_docs"]
            )
            sections.append(section)
            all_doc_urls.extend(section_data["related_docs"])
            
            for subsection in section_data["subsections"]:
                total_topics += len(subsection.get("topics", []))
        
        return GCPDiscoveryResult(
            certification_id=cert_lower,
            certification_name="Cloud Digital Leader",
            exam_page_url=cert_url,
            exam_guide_url=exam_guide_url,
            learning_path_url=learning_path_url,
            sections=sections,
            all_doc_urls=list(set(all_doc_urls)),
            total_topics=total_topics
        )
    
    # For other certifications, return placeholder structure
    return GCPDiscoveryResult(
        certification_id=cert_lower,
        certification_name=certification_id.replace("-", " ").title(),
        exam_page_url=cert_url,
        exam_guide_url=exam_guide_url,
        learning_path_url=learning_path_url,
        sections=[],
        all_doc_urls=[],
        total_topics=0
    )


def fetch_documentation_content(doc_urls: list[str], max_docs: int = 5) -> list[dict]:
    """
    Fetch content from multiple GCP documentation pages.
    
    Args:
        doc_urls: List of documentation URLs to fetch
        max_docs: Maximum number of documents to fetch (for testing)
    
    Returns:
        List of document content dictionaries
    """
    results = []
    for i, url in enumerate(doc_urls[:max_docs]):
        print(f"  Fetching ({i+1}/{min(len(doc_urls), max_docs)}): {url}")
        result = extract_gcp_doc_content(url)
        results.append(result)
    return results


def main():
    """Main entry point for GCP content discovery."""
    parser = argparse.ArgumentParser(description="Discover GCP certification content")
    parser.add_argument(
        "--certification", "-c",
        default="cloud-digital-leader",
        help="Certification ID (e.g., cloud-digital-leader)"
    )
    parser.add_argument(
        "--fetch-docs", "-f",
        action="store_true",
        help="Fetch actual documentation content (slower)"
    )
    parser.add_argument(
        "--max-docs", "-m",
        type=int,
        default=5,
        help="Maximum documents to fetch when using --fetch-docs"
    )
    parser.add_argument(
        "--output", "-o",
        help="Output JSON file path"
    )
    
    args = parser.parse_args()
    
    print(f"\n🔍 Discovering GCP {args.certification} certification content...\n")
    
    # Discover certification structure
    result = discover_gcp_certification(args.certification)
    
    print(f"✅ Certification: {result.certification_name}")
    print(f"   Exam Page: {result.exam_page_url}")
    print(f"   Exam Guide: {result.exam_guide_url or 'Not available'}")
    print(f"   Learning Path: {result.learning_path_url or 'Not available'}")
    print(f"   Total Sections: {len(result.sections)}")
    print(f"   Total Topics: {result.total_topics}")
    print(f"   Documentation URLs: {len(result.all_doc_urls)}")
    
    print("\n📚 Exam Sections:")
    for section in result.sections:
        print(f"\n   Section {section.number}: {section.name} ({section.weight})")
        for subsection in section.subsections:
            print(f"      {subsection['id']} {subsection['title']}")
            for topic in subsection.get('topics', [])[:3]:
                print(f"         • {topic[:60]}...")
            if len(subsection.get('topics', [])) > 3:
                print(f"         ... and {len(subsection['topics']) - 3} more topics")
    
    # Optionally fetch documentation content
    doc_content = []
    if args.fetch_docs:
        print(f"\n📖 Fetching documentation content (max {args.max_docs} docs)...")
        doc_content = fetch_documentation_content(result.all_doc_urls, args.max_docs)
        
        successful = sum(1 for d in doc_content if d['success'])
        print(f"\n   Fetched: {successful}/{len(doc_content)} documents successfully")
        
        for doc in doc_content:
            if doc['success']:
                print(f"\n   📄 {doc['title']}")
                print(f"      URL: {doc['url']}")
                print(f"      Words: {doc['word_count']}")
                print(f"      Headings: {len(doc['headings'])}")
    
    # Output results
    if args.output:
        output_data = {
            "certification_id": result.certification_id,
            "certification_name": result.certification_name,
            "exam_page_url": result.exam_page_url,
            "exam_guide_url": result.exam_guide_url,
            "learning_path_url": result.learning_path_url,
            "total_topics": result.total_topics,
            "sections": [
                {
                    "number": s.number,
                    "name": s.name,
                    "weight": s.weight,
                    "subsections": s.subsections,
                    "related_docs": s.related_docs
                }
                for s in result.sections
            ],
            "doc_content": doc_content if args.fetch_docs else []
        }
        
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\n💾 Results saved to: {args.output}")
    
    print("\n✅ Discovery complete!\n")
    return result


if __name__ == "__main__":
    main()
