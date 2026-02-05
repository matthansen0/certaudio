"""
Azure Functions for Certification Audio Platform API.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import azure.functions as func
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions

# Initialize function app
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# Logging
logger = logging.getLogger(__name__)

# =============================================================================
# CACHED CLIENTS (avoid re-auth on every request)
# =============================================================================
_credential = None
_blob_service = None
_user_delegation_key = None
_delegation_key_expiry = None


def _get_credential():
    """Get cached DefaultAzureCredential."""
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


def _get_blob_service():
    """Get cached BlobServiceClient."""
    global _blob_service
    if _blob_service is None:
        account = os.environ.get("STORAGE_ACCOUNT_NAME")
        _blob_service = BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=_get_credential(),
        )
    return _blob_service


def _get_user_delegation_key():
    """Get cached user delegation key (refreshed every 50 minutes)."""
    global _user_delegation_key, _delegation_key_expiry
    now = datetime.now(timezone.utc)
    
    # Refresh if key is missing or will expire within 10 minutes
    if _user_delegation_key is None or _delegation_key_expiry is None or \
       now + timedelta(minutes=10) >= _delegation_key_expiry:
        start_time = now
        _delegation_key_expiry = now + timedelta(hours=1)
        _user_delegation_key = _get_blob_service().get_user_delegation_key(
            key_start_time=start_time,
            key_expiry_time=_delegation_key_expiry,
        )
        logger.info("Refreshed user delegation key")
    
    return _user_delegation_key, _delegation_key_expiry


# =============================================================================
# GET /api/healthz
# =============================================================================
@app.route(route="healthz", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def healthz(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"status": "ok"}),
        mimetype="application/json",
    )


def _normalize_episode_id(episode_num: str) -> str:
    """Normalize an episode identifier for blob paths.

    The content pipeline stores episode assets using zero-padded numbers
    (e.g., episodes/001.mp3, scripts/001.md). The web app routes use
    natural numbers (e.g., /api/audio/.../1).
    """
    if episode_num is None:
        return ""
    text = str(episode_num).strip()
    if text.isdigit():
        return f"{int(text):03d}"
    return text


def _format_cert_name(cert_id: str) -> str:
    # Basic display name helper. Prefer a curated map when present.
    curated = {
        # Agentic AI
        "ab-731": "AB-731: AI Transformation Leader",
        "ab-100": "AB-100: Agentic AI Business Solutions Architect",
        # Azure
        "az-900": "AZ-900: Azure Fundamentals",
        "az-104": "AZ-104: Azure Administrator",
        "az-204": "AZ-204: Azure Developer",
        "az-305": "AZ-305: Azure Solutions Architect",
        "az-400": "AZ-400: DevOps Engineer",
        "az-500": "AZ-500: Azure Security Engineer",
        "az-700": "AZ-700: Azure Network Engineer",
        # AI & Data
        "ai-900": "AI-900: Azure AI Fundamentals",
        "ai-102": "AI-102: Azure AI Engineer",
        "dp-900": "DP-900: Azure Data Fundamentals",
        "dp-100": "DP-100: Azure Data Scientist",
        "dp-203": "DP-203: Azure Data Engineer",
        "dp-300": "DP-300: Azure Database Administrator",
        "dp-600": "DP-600: Fabric Analytics Engineer",
        "dp-700": "DP-700: Fabric Data Engineer",
        # Security
        "sc-900": "SC-900: Security Fundamentals",
        "sc-100": "SC-100: Cybersecurity Architect",
        "sc-200": "SC-200: Security Operations Analyst",
        "sc-300": "SC-300: Identity Administrator",
        "sc-400": "SC-400: Information Protection Admin",
        # M365
        "ms-900": "MS-900: Microsoft 365 Fundamentals",
        "ms-102": "MS-102: Microsoft 365 Administrator",
        "pl-900": "PL-900: Power Platform Fundamentals",
        "pl-300": "PL-300: Power BI Data Analyst",
    }
    if not cert_id:
        return ""
    return curated.get(cert_id.lower(), cert_id.upper())


# Cached Cosmos client
_cosmos_client = None


def get_cosmos_client():
    """Get cached Cosmos DB client using managed identity."""
    global _cosmos_client
    if _cosmos_client is None:
        endpoint = os.environ.get("COSMOS_DB_ENDPOINT")
        _cosmos_client = CosmosClient(endpoint, _get_credential())
    return _cosmos_client


def get_blob_service():
    """Get cached Blob Service client using managed identity."""
    return _get_blob_service()


# =============================================================================
# GET /api/certifications
# =============================================================================
@app.route(
    route="certifications",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def list_certifications(req: func.HttpRequest) -> func.HttpResponse:
    """List certifications currently present in Cosmos episodes."""
    try:
        client = get_cosmos_client()
        database = client.get_database_client(
            os.environ.get("COSMOS_DB_DATABASE", "certaudio")
        )
        container = database.get_container_client("episodes")

        # DISTINCT across partitions: returns list of certificationIds.
        query = "SELECT DISTINCT VALUE c.certificationId FROM c"
        cert_ids = list(
            container.query_items(query=query, enable_cross_partition_query=True)
        )

        # Filter/normalize, then sort.
        cert_ids = sorted({c for c in cert_ids if isinstance(c, str) and c.strip()})
        result = [{"id": cid, "name": _format_cert_name(cid)} for cid in cert_ids]

        return func.HttpResponse(
            json.dumps({"certifications": result}),
            mimetype="application/json",
        )
    except Exception as e:
        logger.error(f"Error listing certifications: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


# =============================================================================
# GET /api/episodes/{certificationId}/{format}
# =============================================================================
@app.route(
    route="episodes/{certificationId}/{format}",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def get_episodes(req: func.HttpRequest) -> func.HttpResponse:
    """
    Get all episodes for a certification and format.

    Returns:
        JSON array of episode metadata
    """
    cert_id = req.route_params.get("certificationId")
    audio_format = req.route_params.get("format")

    if not cert_id or not audio_format:
        return func.HttpResponse(
            json.dumps({"error": "certificationId and format are required"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        client = get_cosmos_client()
        database = client.get_database_client(os.environ.get("COSMOS_DB_DATABASE", "certaudio"))
        container = database.get_container_client("episodes")

        # Query episodes
        query = """
            SELECT * FROM c 
            WHERE c.certificationId = @certId AND c.format = @format
            ORDER BY c.sequenceNumber
        """
        params = [
            {"name": "@certId", "value": cert_id},
            {"name": "@format", "value": audio_format},
        ]

        episodes = list(
            container.query_items(
                query=query, parameters=params, enable_cross_partition_query=True
            )
        )

        # Group by skill domain with metrics
        domains = {}
        total_duration_seconds = 0
        
        for ep in episodes:
            domain = ep.get("skillDomain", "Other")
            duration = ep.get("durationSeconds", 0)
            total_duration_seconds += duration
            
            if domain not in domains:
                domains[domain] = {
                    "episodes": [],
                    "totalDurationSeconds": 0,
                    "episodeCount": 0,
                }
            
            domains[domain]["episodes"].append({
                "id": ep["id"],
                "sequenceNumber": ep["sequenceNumber"],
                "title": ep["title"],
                "durationSeconds": duration,
                "skillDomain": domain,
                "isAmendment": ep.get("isAmendment", False),
                "amendmentOf": ep.get("amendmentOf"),
                "skillTopics": ep.get("skillTopics", []),
            })
            domains[domain]["totalDurationSeconds"] += duration
            domains[domain]["episodeCount"] += 1

        # Calculate hours
        total_hours = total_duration_seconds / 3600

        return func.HttpResponse(
            json.dumps({
                "certificationId": cert_id,
                "format": audio_format,
                "totalEpisodes": len(episodes),
                "totalDurationSeconds": total_duration_seconds,
                "totalHours": round(total_hours, 1),
                "domains": domains,
            }),
            mimetype="application/json",
        )

    except Exception as e:
        logger.error(f"Error getting episodes: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


# =============================================================================
# GET /api/audio/{certificationId}/{format}/{episodeNumber}
# =============================================================================
@app.route(
    route="audio/{certificationId}/{format}/{episodeNumber}",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def get_audio(req: func.HttpRequest) -> func.HttpResponse:
    """
    Redirect to a SAS URL for direct blob download.
    This avoids proxying large audio files through the Function.
    """
    cert_id = req.route_params.get("certificationId")
    audio_format = req.route_params.get("format")
    episode_num = req.route_params.get("episodeNumber")

    if not all([cert_id, audio_format, episode_num]):
        return func.HttpResponse(
            json.dumps({"error": "Missing required parameters"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        account_name = os.environ.get("STORAGE_ACCOUNT_NAME")
        container_name = "audio"
        episode_id = _normalize_episode_id(episode_num)
        blob_name = f"{cert_id}/{audio_format}/episodes/{episode_id}.mp3"

        # Use cached user delegation key for fast SAS generation
        user_delegation_key, expiry_time = _get_user_delegation_key()
        
        # Generate SAS URL
        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=container_name,
            blob_name=blob_name,
            user_delegation_key=user_delegation_key,
            permission=BlobSasPermissions(read=True),
            expiry=expiry_time,
        )
        
        sas_url = f"https://{account_name}.blob.core.windows.net/{container_name}/{blob_name}?{sas_token}"
        
        # Redirect client to download directly from blob storage
        return func.HttpResponse(
            status_code=302,
            headers={
                "Location": sas_url,
                "Cache-Control": "no-cache",
            },
        )

    except Exception as e:
        logger.error(f"Error generating audio URL: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


# =============================================================================
# GET /api/progress/{userId}/{certificationId}
# =============================================================================
@app.route(
    route="progress/{userId}/{certificationId}",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def get_progress(req: func.HttpRequest) -> func.HttpResponse:
    """
    Get user progress for a certification.

    Returns:
        JSON object with episode completion status
    """
    user_id = req.route_params.get("userId")
    cert_id = req.route_params.get("certificationId")

    if not user_id or not cert_id:
        return func.HttpResponse(
            json.dumps({"error": "userId and certificationId are required"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        client = get_cosmos_client()
        database = client.get_database_client(os.environ.get("COSMOS_DB_DATABASE", "certaudio"))
        container = database.get_container_client("userProgress")

        # Try to get existing progress
        doc_id = f"{user_id}-{cert_id}"
        try:
            progress = container.read_item(item=doc_id, partition_key=user_id)
        except:
            # No progress found, return empty
            progress = {
                "id": doc_id,
                "userId": user_id,
                "certificationId": cert_id,
                "progress": {},
            }

        return func.HttpResponse(
            json.dumps(progress),
            mimetype="application/json",
        )

    except Exception as e:
        logger.error(f"Error getting progress: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


# =============================================================================
# POST /api/progress/{userId}/{certificationId}
# =============================================================================
@app.route(
    route="progress/{userId}/{certificationId}",
    methods=["POST"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def update_progress(req: func.HttpRequest) -> func.HttpResponse:
    """
    Update user progress for a certification.

    Request body:
        {
            "episodeId": "001",
            "completed": true,
            "position": 612
        }
    """
    user_id = req.route_params.get("userId")
    cert_id = req.route_params.get("certificationId")

    if not user_id or not cert_id:
        return func.HttpResponse(
            json.dumps({"error": "userId and certificationId are required"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        body = req.get_json()
        episode_id = body.get("episodeId")
        completed = body.get("completed", False)
        position = body.get("position", 0)

        if not episode_id:
            return func.HttpResponse(
                json.dumps({"error": "episodeId is required"}),
                status_code=400,
                mimetype="application/json",
            )

        client = get_cosmos_client()
        database = client.get_database_client(os.environ.get("COSMOS_DB_DATABASE", "certaudio"))
        container = database.get_container_client("userProgress")

        # Get or create progress document
        doc_id = f"{user_id}-{cert_id}"
        try:
            progress_doc = container.read_item(item=doc_id, partition_key=user_id)
        except:
            progress_doc = {
                "id": doc_id,
                "userId": user_id,
                "certificationId": cert_id,
                "progress": {},
            }

        # Update episode progress
        progress_doc["progress"][episode_id] = {
            "completed": completed,
            "position": position,
        }

        # Save
        container.upsert_item(progress_doc)

        return func.HttpResponse(
            json.dumps({"success": True}),
            mimetype="application/json",
        )

    except Exception as e:
        logger.error(f"Error updating progress: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


# =============================================================================
# GET /api/script/{certificationId}/{format}/{episodeNumber}
# =============================================================================
@app.route(
    route="script/{certificationId}/{format}/{episodeNumber}",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def get_script(req: func.HttpRequest) -> func.HttpResponse:
    """
    Get episode script (transcript).

    Returns:
        Markdown script content
    """
    cert_id = req.route_params.get("certificationId")
    audio_format = req.route_params.get("format")
    episode_num = req.route_params.get("episodeNumber")

    if not all([cert_id, audio_format, episode_num]):
        return func.HttpResponse(
            json.dumps({"error": "Missing required parameters"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        blob_service = get_blob_service()
        # Fixed container name with path prefix for organization
        container_name = "scripts"
        episode_id = _normalize_episode_id(episode_num)
        blob_name = f"{cert_id}/{audio_format}/scripts/{episode_id}.md"

        blob_client = blob_service.get_blob_client(
            container=container_name, blob=blob_name
        )

        download = blob_client.download_blob()
        script_content = download.readall().decode("utf-8")

        return func.HttpResponse(
            script_content,
            mimetype="text/markdown",
        )

    except Exception as e:
        logger.error(f"Error getting script: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


# =============================================================================
# POST /api/chat - Study Partner AI Chat
# =============================================================================

# Rate limiting configuration
RATE_LIMIT_REQUESTS = 50  # Max requests per window
RATE_LIMIT_WINDOW_SECONDS = 3600  # 1 hour window

# In-memory rate limit tracking (note: resets on function restart, not distributed)
# Key: client_id, Value: list of request timestamps
_rate_limit_cache: dict[str, list[float]] = {}


def _get_client_id(req: func.HttpRequest) -> str:
    """Get a client identifier for rate limiting."""
    # Try X-Forwarded-For header (set by Azure Front Door / Static Web Apps)
    forwarded_for = req.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        # Take the first IP (client IP)
        return forwarded_for.split(",")[0].strip()
    
    # Fallback to a session ID from the request body or a default
    return "anonymous"


def _check_rate_limit(client_id: str) -> tuple[bool, int, int]:
    """
    Check if the client is within rate limits.
    
    Returns:
        (allowed, remaining_requests, reset_seconds)
    """
    import time
    
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    
    # Get or create request history for this client
    if client_id not in _rate_limit_cache:
        _rate_limit_cache[client_id] = []
    
    # Remove old requests outside the window
    _rate_limit_cache[client_id] = [
        ts for ts in _rate_limit_cache[client_id] if ts > window_start
    ]
    
    request_count = len(_rate_limit_cache[client_id])
    remaining = max(0, RATE_LIMIT_REQUESTS - request_count)
    
    # Calculate reset time (when oldest request expires)
    if _rate_limit_cache[client_id]:
        oldest = min(_rate_limit_cache[client_id])
        reset_seconds = int(oldest + RATE_LIMIT_WINDOW_SECONDS - now)
    else:
        reset_seconds = RATE_LIMIT_WINDOW_SECONDS
    
    if request_count >= RATE_LIMIT_REQUESTS:
        return False, 0, reset_seconds
    
    # Record this request
    _rate_limit_cache[client_id].append(now)
    
    return True, remaining - 1, reset_seconds


# Cached OpenAI client
_openai_client = None


def get_openai_client():
    """Get cached Azure OpenAI client using managed identity."""
    global _openai_client
    if _openai_client is None:
        from openai import AzureOpenAI
        from azure.identity import get_bearer_token_provider

        endpoint = os.environ.get("OPENAI_ENDPOINT")
        if not endpoint:
            return None
        token_provider = get_bearer_token_provider(
            _get_credential(), "https://cognitiveservices.azure.com/.default"
        )
        _openai_client = AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token_provider=token_provider,
            api_version="2024-06-01",
        )
    return _openai_client


# =============================================================================
# AI FOUNDRY AGENT CLIENT
# =============================================================================
_ai_project_client = None
_study_partner_agent = None


def get_ai_project_client():
    """Get cached AI Project client for AI Foundry."""
    global _ai_project_client
    if _ai_project_client is None:
        foundry_endpoint = os.environ.get("FOUNDRY_ENDPOINT", "").strip()
        if not foundry_endpoint:
            return None
        try:
            from azure.ai.projects import AIProjectClient
            _ai_project_client = AIProjectClient(
                endpoint=foundry_endpoint,
                credential=_get_credential(),
            )
            logger.info(f"AI Project client initialized: {foundry_endpoint}")
        except Exception as e:
            logger.error(f"Failed to initialize AI Project client: {e}")
            return None
    return _ai_project_client


def get_or_create_agent():
    """Get or create the Study Partner agent with AI Search tool."""
    global _study_partner_agent
    
    if _study_partner_agent is not None:
        return _study_partner_agent
    
    client = get_ai_project_client()
    if not client:
        return None
    
    try:
        from azure.ai.projects.models import (
            AzureAISearchTool,
            AzureAISearchToolResource,
        )
        
        # Get the search connection name from environment
        search_connection = os.environ.get("FOUNDRY_SEARCH_CONNECTION", "").strip()
        search_index = os.environ.get("FOUNDRY_SEARCH_INDEX", "certification-content").strip()
        
        # Configure AI Search tool
        ai_search_tool = AzureAISearchTool(
            index_connection_id=search_connection,
            index_name=search_index,
        ) if search_connection else None
        
        tools = [ai_search_tool] if ai_search_tool else []
        
        # Create or get the agent
        _study_partner_agent = client.agents.create_agent(
            model=os.environ.get("FOUNDRY_MODEL", "gpt-4o"),
            name="study-partner",
            instructions=AGENT_INSTRUCTIONS,
            tools=tools,
        )
        logger.info(f"Created Study Partner agent: {_study_partner_agent.id}")
        return _study_partner_agent
    except Exception as e:
        logger.error(f"Failed to create agent: {e}")
        return None


# Agent instructions (system prompt)
AGENT_INSTRUCTIONS = """You are a friendly and knowledgeable Microsoft certification exam study partner.

Your capabilities:
- **Practice Test Mode**: Give 5 multiple-choice questions (A-D), wait for answers, then provide a score and explanations
- **Quick Question**: Generate a single practice question with options, wait for the user's answer, then reveal the correct response
- **Explain Concepts**: Break down complex Azure/Microsoft topics into understandable explanations
- **Compare Topics**: Help users understand differences between similar services or concepts
- **Scenario Practice**: Create realistic exam-style scenario questions

Guidelines:
- Be encouraging and supportive
- Keep explanations concise but thorough
- Use real-world examples when helpful
- For practice tests: Present all 5 questions together, ask the user to reply with answers, then score and explain
- Format responses with markdown for readability
- When you have reference content from the search tool, cite your sources
- Focus on official exam objectives and Microsoft Learn content

You have access to a search tool that can find relevant Microsoft Learn documentation. Use it when users ask about specific topics."""


def chat_with_agent(cert_id: str, message: str, history: list) -> str:
    """Use AI Foundry Agent to respond to user message."""
    client = get_ai_project_client()
    agent = get_or_create_agent()
    
    if not client or not agent:
        raise ValueError("AI Foundry Agent not available")
    
    # Create a new thread for this conversation
    thread = client.agents.create_thread()
    
    try:
        # Add conversation history to thread
        for msg in history[-10:]:
            if msg.get("role") in ("user", "assistant") and msg.get("content"):
                client.agents.create_message(
                    thread_id=thread.id,
                    role=msg["role"],
                    content=msg["content"],
                )
        
        # Add context about the certification in the first user message
        cert_name = _format_cert_name(cert_id) if cert_id else "Microsoft certification"
        contextualized_message = f"[Context: User is studying for {cert_name}]\n\n{message}"
        
        # Add current message
        client.agents.create_message(
            thread_id=thread.id,
            role="user",
            content=contextualized_message,
        )
        
        # Run the agent
        run = client.agents.create_and_process_run(
            thread_id=thread.id,
            assistant_id=agent.id,
        )
        
        if run.status != "completed":
            logger.error(f"Agent run failed: {run.status} - {run.last_error}")
            raise ValueError(f"Agent run failed: {run.status}")
        
        # Get the response messages
        messages = client.agents.list_messages(thread_id=thread.id)
        
        # Find the assistant's response (most recent assistant message)
        for msg in messages.data:
            if msg.role == "assistant":
                # Extract text content
                for content in msg.content:
                    if hasattr(content, 'text'):
                        return content.text.value
        
        raise ValueError("No response from agent")
        
    finally:
        # Clean up thread
        try:
            client.agents.delete_thread(thread.id)
        except Exception:
            pass


# Cached Search client
_search_client = None


def get_search_client(index_name: str):
    """Get Azure AI Search client using managed identity."""
    global _search_client
    if _search_client is None:
        from azure.search.documents import SearchClient

        endpoint = os.environ.get("SEARCH_ENDPOINT")
        if not endpoint:
            return None
        _search_client = SearchClient(
            endpoint=endpoint,
            index_name=index_name,
            credential=_get_credential(),
        )
    return _search_client


def search_content(cert_id: str, query: str, top_k: int = 5) -> list[dict]:
    """Search the certification content index for relevant chunks."""
    index_name = f"{cert_id}-content"
    client = get_search_client(index_name)
    if not client:
        return []

    try:
        results = client.search(
            search_text=query,
            select=["content", "title", "url"],
            top=top_k,
        )
        return [
            {
                "content": r.get("content", ""),
                "title": r.get("title", ""),
                "url": r.get("url", ""),
            }
            for r in results
        ]
    except Exception as e:
        logger.warning(f"Search failed (index may not exist): {e}")
        return []


# Study Partner system prompt with RAG context
STUDY_PARTNER_SYSTEM_PROMPT = """You are a friendly and knowledgeable Microsoft certification exam study partner.
Your role is to help users prepare for their {certification} certification exam.

{context_section}

Your capabilities:
- **Practice Test Mode**: When asked for a test, give 5 multiple-choice questions (A-D), wait for answers, then provide a score and explanations
- **Quick Question**: Generate a single practice question with options, wait for the user's answer, then reveal the correct response with explanation
- **Explain Concepts**: Break down complex Azure/Microsoft topics into understandable explanations  
- **Compare Topics**: Help users understand differences between similar services or concepts (e.g., "What's the difference between X and Y?")
- **Scenario Practice**: Create realistic exam-style scenario questions
- **Clarify Confusion**: If someone says "I don't understand X", explain it step by step with examples

Guidelines:
- Be encouraging and supportive - exam prep can be stressful!
- Keep explanations concise but thorough
- Use real-world examples when helpful
- **For practice tests**: Present all 5 questions together, numbered 1-5 with options A-D. Ask the user to reply with their answers (e.g., "1:A, 2:B, 3:C, 4:D, 5:A"). Then score them and explain each answer.
- **For single questions**: Wait for the user's answer before revealing the correct response
- Format responses with markdown for readability (use **bold**, bullet points, code blocks where appropriate)
- If you're unsure about something, say so rather than guessing
- When you have reference content, base your answers on it but explain in your own words
- Focus on the official exam objectives and Microsoft Learn content

Remember: You're a study partner, not just an information source. Engage conversationally and adapt to the user's learning style."""


# =============================================================================
# GET /api/chat/config - Study Partner Configuration
# =============================================================================

@app.route(
    route="chat/config",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def chat_config(req: func.HttpRequest) -> func.HttpResponse:
    """
    Get Study Partner configuration (rate limits, etc.).
    
    Returns:
        {"rateLimitPerHour": 50}
    """
    return func.HttpResponse(
        json.dumps({
            "rateLimitPerHour": RATE_LIMIT_REQUESTS,
        }),
        mimetype="application/json",
    )


def check_honeypot(body: dict) -> bool:
    """
    Check if honeypot field was filled (indicating bot).
    
    Returns True if request is suspicious (should be blocked).
    """
    hp_value = body.get("hp", "")
    # If the honeypot field has any value, it's likely a bot
    return bool(hp_value)


@app.route(
    route="chat",
    methods=["POST"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def chat(req: func.HttpRequest) -> func.HttpResponse:
    """
    AI-powered study partner chat endpoint.
    
    Uses AI Foundry Agent when available, falls back to direct OpenAI + RAG.
    
    Request body:
        {
            "certificationId": "az-104",
            "message": "Quiz me on Azure AD",
            "history": [{"role": "user", "content": "..."}, ...]
        }
    
    Returns:
        {"response": "AI response text", "rateLimit": {"remaining": N, "resetMinutes": M}}
        or {"not_deployed": true, "message": "..."} if Study Partner is not enabled
        or {"rate_limited": true, ...} if rate limit exceeded
    """
    # Check if Study Partner is deployed
    # Either FOUNDRY_ENDPOINT (Agent) or SEARCH_ENDPOINT (RAG fallback) must be set
    foundry_endpoint = os.environ.get("FOUNDRY_ENDPOINT", "").strip()
    search_endpoint = os.environ.get("SEARCH_ENDPOINT", "").strip()
    
    if not foundry_endpoint and not search_endpoint:
        return func.HttpResponse(
            json.dumps({
                "not_deployed": True,
                "message": "Study Partner is not enabled. Deploy with enableStudyPartner=true to use this feature."
            }),
            mimetype="application/json",
        )

    # Check rate limit
    client_id = _get_client_id(req)
    allowed, remaining, reset_seconds = _check_rate_limit(client_id)
    
    if not allowed:
        reset_minutes = (reset_seconds + 59) // 60  # Round up
        return func.HttpResponse(
            json.dumps({
                "rate_limited": True,
                "message": f"Rate limit exceeded. Please wait {reset_minutes} minute{'s' if reset_minutes != 1 else ''} before trying again.",
                "rateLimit": {
                    "remaining": 0,
                    "resetMinutes": reset_minutes,
                    "limit": RATE_LIMIT_REQUESTS,
                }
            }),
            status_code=429,
            mimetype="application/json",
        )

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}),
            status_code=400,
            mimetype="application/json",
        )

    # Check honeypot (bot detection)
    if check_honeypot(body):
        logger.warning(f"Honeypot triggered from {client_id}")
        return func.HttpResponse(
            json.dumps({
                "error": "Request blocked.",
                "verification_failed": True,
            }),
            status_code=403,
            mimetype="application/json",
        )

    cert_id = body.get("certificationId", "").strip()
    message = body.get("message", "").strip()
    history = body.get("history", [])

    if not message:
        return func.HttpResponse(
            json.dumps({"error": "Message is required"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        # Try AI Foundry Agent first if configured
        if foundry_endpoint:
            try:
                assistant_response = chat_with_agent(cert_id, message, history)
                logger.info("Response generated via AI Foundry Agent")
            except Exception as agent_err:
                logger.warning(f"AI Foundry Agent failed, falling back to OpenAI: {agent_err}")
                assistant_response = chat_with_openai_rag(cert_id, message, history)
        else:
            # Fall back to direct OpenAI + RAG
            assistant_response = chat_with_openai_rag(cert_id, message, history)

        reset_minutes = (reset_seconds + 59) // 60
        return func.HttpResponse(
            json.dumps({
                "response": assistant_response,
                "rateLimit": {
                    "remaining": remaining,
                    "resetMinutes": reset_minutes,
                    "limit": RATE_LIMIT_REQUESTS,
                }
            }),
            mimetype="application/json",
        )

    except Exception as e:
        logger.error(f"Chat error: {e}")
        return func.HttpResponse(
            json.dumps({"error": "Failed to generate response. Please try again."}),
            status_code=500,
            mimetype="application/json",
        )


def chat_with_openai_rag(cert_id: str, message: str, history: list) -> str:
    """Fallback: Use direct OpenAI + AI Search RAG."""
    client = get_openai_client()
    if not client:
        raise ValueError("OpenAI service not configured")

    # Format certification name for the prompt
    cert_name = _format_cert_name(cert_id) if cert_id else "Microsoft certification"

    # RAG: Search for relevant content
    search_results = search_content(cert_id, message, top_k=5)
    
    # Build context section from search results
    if search_results:
        context_parts = ["Here is relevant content from Microsoft Learn that may help answer the question:\n"]
        for i, r in enumerate(search_results, 1):
            context_parts.append(f"**Source {i}**: {r['title']}")
            context_parts.append(r['content'][:1500])  # Limit per chunk
            context_parts.append("")
        context_section = "\n".join(context_parts)
    else:
        context_section = "(No specific reference content found - answer based on your training knowledge)"

    # Build messages array
    messages = [
        {
            "role": "system",
            "content": STUDY_PARTNER_SYSTEM_PROMPT.format(
                certification=cert_name,
                context_section=context_section,
            ),
        }
    ]
    
    # Add conversation history (limit to last 10 for token efficiency)
    for msg in history[-10:]:
        if msg.get("role") in ("user", "assistant") and msg.get("content"):
            messages.append({
                "role": msg["role"],
                "content": msg["content"],
            })
    
    # Add current message (if not already in history)
    if not history or history[-1].get("content") != message:
        messages.append({"role": "user", "content": message})

    # Call Azure OpenAI
    response = client.chat.completions.create(
        model="gpt-4o",  # Deployment name from ai-services.bicep
        messages=messages,
        max_tokens=1500,
        temperature=0.7,
    )

    return response.choices[0].message.content
