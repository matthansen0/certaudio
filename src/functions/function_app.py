"""
Azure Functions for Certification Audio Platform API.
"""

import json
import logging
import os
from typing import Optional

import azure.functions as func
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

# Initialize function app
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# Logging
logger = logging.getLogger(__name__)


def _format_cert_name(cert_id: str) -> str:
    # Basic display name helper. Prefer a curated map when present.
    curated = {
        "ai-102": "AI-102: Azure AI Engineer",
        "az-204": "AZ-204: Azure Developer",
        "az-104": "AZ-104: Azure Administrator",
        "az-900": "AZ-900: Azure Fundamentals",
    }
    if not cert_id:
        return ""
    return curated.get(cert_id.lower(), cert_id.upper())


def get_cosmos_client():
    """Get Cosmos DB client using managed identity."""
    endpoint = os.environ.get("COSMOS_DB_ENDPOINT")
    credential = DefaultAzureCredential()
    return CosmosClient(endpoint, credential)


def get_blob_service():
    """Get Blob Service client using managed identity."""
    account = os.environ.get("STORAGE_ACCOUNT_NAME")
    credential = DefaultAzureCredential()
    return BlobServiceClient(
        account_url=f"https://{account}.blob.core.windows.net",
        credential=credential,
    )


# =============================================================================
# GET /api/certifications
# =============================================================================
@app.route(route="certifications", methods=["GET"])
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
@app.route(route="episodes/{certificationId}/{format}", methods=["GET"])
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

        # Group by skill domain
        domains = {}
        for ep in episodes:
            domain = ep.get("skillDomain", "Other")
            if domain not in domains:
                domains[domain] = []
            domains[domain].append({
                "id": ep["id"],
                "sequenceNumber": ep["sequenceNumber"],
                "title": ep["title"],
                "durationSeconds": ep.get("durationSeconds", 0),
                "isAmendment": ep.get("isAmendment", False),
                "amendmentOf": ep.get("amendmentOf"),
                "skillTopics": ep.get("skillTopics", []),
            })

        return func.HttpResponse(
            json.dumps({
                "certificationId": cert_id,
                "format": audio_format,
                "totalEpisodes": len(episodes),
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
@app.route(route="audio/{certificationId}/{format}/{episodeNumber}", methods=["GET"])
def get_audio(req: func.HttpRequest) -> func.HttpResponse:
    """
    Stream audio file for an episode.

    Returns:
        Audio file (MP3) with proper headers
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
        container_name = f"{cert_id}-{audio_format}"
        blob_name = f"episodes/{episode_num}.mp3"

        blob_client = blob_service.get_blob_client(
            container=container_name, blob=blob_name
        )

        # Download blob
        download = blob_client.download_blob()
        audio_data = download.readall()

        # Support range requests for seeking
        range_header = req.headers.get("Range")
        if range_header:
            # Parse range header
            range_match = range_header.replace("bytes=", "").split("-")
            start = int(range_match[0]) if range_match[0] else 0
            end = int(range_match[1]) if len(range_match) > 1 and range_match[1] else len(audio_data) - 1

            # Return partial content
            return func.HttpResponse(
                audio_data[start : end + 1],
                status_code=206,
                mimetype="audio/mpeg",
                headers={
                    "Content-Range": f"bytes {start}-{end}/{len(audio_data)}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(end - start + 1),
                },
            )

        return func.HttpResponse(
            audio_data,
            mimetype="audio/mpeg",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(len(audio_data)),
            },
        )

    except Exception as e:
        logger.error(f"Error streaming audio: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


# =============================================================================
# GET /api/progress/{userId}/{certificationId}
# =============================================================================
@app.route(route="progress/{userId}/{certificationId}", methods=["GET"])
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
@app.route(route="progress/{userId}/{certificationId}", methods=["POST"])
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
@app.route(route="script/{certificationId}/{format}/{episodeNumber}", methods=["GET"])
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
        container_name = f"{cert_id}-{audio_format}-scripts"
        blob_name = f"scripts/{episode_num}.md"

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
