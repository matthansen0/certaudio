"""
Upload audio, scripts, and SSML to Azure Blob Storage.
"""

import os
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from promptflow.core import tool


def get_blob_service_client() -> BlobServiceClient:
    """Get Blob Service client using managed identity."""
    storage_account = os.environ.get("STORAGE_ACCOUNT_NAME")
    if not storage_account:
        raise ValueError("STORAGE_ACCOUNT_NAME environment variable required")

    credential = DefaultAzureCredential()
    account_url = f"https://{storage_account}.blob.core.windows.net"

    return BlobServiceClient(account_url=account_url, credential=credential)


@tool
def upload_to_blob(
    audio_file_path: str,
    script_content: str,
    ssml_content: str,
    certification_id: str,
    audio_format: str,
    episode_number: int,
) -> dict:
    """
    Upload audio, script, and SSML to blob storage.

    Args:
        audio_file_path: Local path to MP3 file
        script_content: Narration script text
        ssml_content: SSML markup
        certification_id: Certification ID
        audio_format: 'instructional' or 'podcast'
        episode_number: Episode sequence number

    Returns:
        Dict with audio_url, script_url, ssml_url
    """
    blob_service = get_blob_service_client()

    # Fixed container names - use path prefixes for cert/format organization
    audio_container = blob_service.get_container_client("audio")
    scripts_container = blob_service.get_container_client("scripts")

    # Ensure containers exist (they should be created by infra, but just in case)
    try:
        audio_container.create_container()
    except Exception:
        pass  # Container already exists

    try:
        scripts_container.create_container()
    except Exception:
        pass  # Container already exists

    # File paths in blob storage - use path prefixes for organization
    episode_id = f"{episode_number:03d}"
    audio_blob_path = f"{certification_id}/{audio_format}/episodes/{episode_id}.mp3"
    script_blob_path = f"{certification_id}/{audio_format}/scripts/{episode_id}.md"
    ssml_blob_path = f"{certification_id}/{audio_format}/ssml/{episode_id}.ssml"

    # Upload audio file
    print(f"Uploading audio: {audio_blob_path}")
    with open(audio_file_path, "rb") as audio_file:
        audio_container.upload_blob(
            name=audio_blob_path,
            data=audio_file,
            overwrite=True,
            content_settings=ContentSettings(content_type="audio/mpeg"),
        )

    # Upload script
    print(f"Uploading script: {script_blob_path}")
    scripts_container.upload_blob(
        name=script_blob_path,
        data=script_content,
        overwrite=True,
        content_settings=ContentSettings(content_type="text/markdown"),
    )

    # Upload SSML
    print(f"Uploading SSML: {ssml_blob_path}")
    scripts_container.upload_blob(
        name=ssml_blob_path,
        data=ssml_content,
        overwrite=True,
        content_settings=ContentSettings(content_type="application/ssml+xml"),
    )

    # Construct URLs (these will be accessed via Functions API, not directly)
    storage_account = os.environ.get("STORAGE_ACCOUNT_NAME")
    base_url = f"https://{storage_account}.blob.core.windows.net"

    # Clean up local audio file
    try:
        os.remove(audio_file_path)
    except Exception:
        pass

    return {
        "audio_url": f"{base_url}/audio/{audio_blob_path}",
        "script_url": f"{base_url}/scripts/{script_blob_path}",
        "ssml_url": f"{base_url}/scripts/{ssml_blob_path}",
    }
