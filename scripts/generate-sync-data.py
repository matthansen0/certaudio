#!/usr/bin/env python3
"""
Generate word-boundary sync data for existing episodes using Azure Speech-to-Text.

This is a one-off backfill script for episodes that were synthesized before the
read-along feature was added to the pipeline. New episodes capture word boundaries
at synthesis time at no extra cost; this script uses STT (~$1/audio-hour) to
retroactively generate the same data.

Usage:
    python scripts/generate-sync-data.py --cert ai-102 --format instructional
    python scripts/generate-sync-data.py --cert ai-102 --format instructional --episodes 1,2,3
    python scripts/generate-sync-data.py --cert ai-102 --format instructional --dry-run

Environment variables (from .env.local or exported):
    STORAGE_ACCOUNT_NAME   - Azure Storage account
    SPEECH_ENDPOINT        - Azure Speech endpoint (or SPEECH_KEY + SPEECH_REGION)
    SPEECH_KEY             - Speech API key (optional, uses DefaultAzureCredential otherwise)
    SPEECH_REGION          - Speech region
"""

import argparse
import json
import os
import sys
import tempfile
import time

try:
    import azure.cognitiveservices.speech as speechsdk
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient, ContentSettings
except ImportError:
    print("Missing dependencies. Install with:")
    print("  pip install azure-cognitiveservices-speech azure-storage-blob azure-identity")
    sys.exit(1)


def get_blob_service() -> BlobServiceClient:
    """Get blob service client using available credentials."""
    account = os.environ.get("STORAGE_ACCOUNT_NAME")
    if not account:
        raise ValueError("STORAGE_ACCOUNT_NAME environment variable required")

    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if conn_str:
        return BlobServiceClient.from_connection_string(conn_str)

    account_key = os.environ.get("STORAGE_ACCOUNT_KEY")
    if account_key:
        return BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=account_key,
        )

    credential = DefaultAzureCredential()
    return BlobServiceClient(
        account_url=f"https://{account}.blob.core.windows.net",
        credential=credential,
    )


def get_speech_config() -> speechsdk.SpeechConfig:
    """Create Speech SDK config for recognition."""
    speech_key = os.environ.get("SPEECH_KEY")
    speech_region = os.environ.get("SPEECH_REGION", "centralus")

    if speech_key:
        return speechsdk.SpeechConfig(subscription=speech_key, region=speech_region)

    # Use Entra token for Speech-to-Text
    speech_endpoint = os.environ.get("SPEECH_ENDPOINT")
    if not speech_endpoint:
        raise ValueError("SPEECH_KEY or SPEECH_ENDPOINT must be set")

    credential = DefaultAzureCredential()
    token = credential.get_token("https://cognitiveservices.azure.com/.default")

    subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID")
    resource_group = os.environ.get("AZURE_RESOURCE_GROUP")
    host_no_scheme = speech_endpoint.rstrip("/").split("//", 1)[-1]
    resource_name = host_no_scheme.split(".", 1)[0]

    resource_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{resource_name}"
    )
    auth_token = f"aad#{resource_id}#{token.token}"

    return speechsdk.SpeechConfig(auth_token=auth_token, region=speech_region)


def recognize_with_word_timestamps(audio_path: str) -> list[dict]:
    """Run STT on an audio file and return word boundary data."""
    config = get_speech_config()
    config.request_word_level_timestamps()
    config.output_format = speechsdk.OutputFormat.Detailed

    audio_config = speechsdk.audio.AudioConfig(filename=audio_path)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=config, audio_config=audio_config
    )

    word_boundaries = []
    done = False
    error_msg = None

    def on_recognized(evt):
        result = evt.result
        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            # Parse detailed results for word-level timestamps
            detailed = json.loads(result.json)
            best = detailed.get("NBest", [{}])[0]
            for word_info in best.get("Words", []):
                word_boundaries.append({
                    "text": word_info["Word"],
                    "offset": word_info["Offset"] / 10_000,  # 100ns ticks → ms
                    "duration": word_info["Duration"] / 10_000,
                    "type": "Word",
                })

    def on_canceled(evt):
        nonlocal done, error_msg
        if evt.cancellation_details.reason == speechsdk.CancellationReason.Error:
            error_msg = evt.cancellation_details.error_details
        done = True

    def on_session_stopped(evt):
        nonlocal done
        done = True

    recognizer.recognized.connect(on_recognized)
    recognizer.canceled.connect(on_canceled)
    recognizer.session_stopped.connect(on_session_stopped)

    recognizer.start_continuous_recognition()
    while not done:
        time.sleep(0.5)
    recognizer.stop_continuous_recognition()

    if error_msg:
        raise RuntimeError(f"Speech recognition failed: {error_msg}")

    return word_boundaries


def list_episodes(blob_service: BlobServiceClient, cert_id: str, audio_format: str) -> list[str]:
    """List episode IDs that have audio blobs."""
    container = blob_service.get_container_client("audio")
    prefix = f"{cert_id}/{audio_format}/episodes/"
    episode_ids = []
    for blob in container.list_blobs(name_starts_with=prefix):
        name = blob.name.split("/")[-1]
        if name.endswith(".mp3"):
            episode_ids.append(name.replace(".mp3", ""))
    return sorted(episode_ids)


def sync_exists(blob_service: BlobServiceClient, cert_id: str, audio_format: str, episode_id: str) -> bool:
    """Check if sync data already exists for an episode."""
    container = blob_service.get_container_client("scripts")
    blob_name = f"{cert_id}/{audio_format}/sync/{episode_id}.sync.json"
    blob_client = container.get_blob_client(blob_name)
    return blob_client.exists()


def main():
    parser = argparse.ArgumentParser(description="Generate word-boundary sync data for existing episodes")
    parser.add_argument("--cert", required=True, help="Certification ID (e.g., ai-102)")
    parser.add_argument("--format", default="instructional", choices=["instructional", "podcast"])
    parser.add_argument("--episodes", help="Comma-separated episode numbers (default: all)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing sync data")
    parser.add_argument("--dry-run", action="store_true", help="List episodes without processing")
    args = parser.parse_args()

    # Load .env.local if present
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env.local")
    if os.path.exists(env_file):
        print(f"Loading environment from {env_file}")
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())

    blob_service = get_blob_service()

    # Find episodes
    all_episodes = list_episodes(blob_service, args.cert, args.format)
    if args.episodes:
        selected = [f"{int(e):03d}" for e in args.episodes.split(",")]
        all_episodes = [e for e in all_episodes if e in selected]

    if not all_episodes:
        print("No episodes found.")
        return

    # Filter out episodes that already have sync data
    if not args.force:
        to_process = [
            e for e in all_episodes
            if not sync_exists(blob_service, args.cert, args.format, e)
        ]
        skipped = len(all_episodes) - len(to_process)
        if skipped > 0:
            print(f"Skipping {skipped} episodes with existing sync data (use --force to overwrite)")
    else:
        to_process = all_episodes

    # Estimate cost
    audio_container = blob_service.get_container_client("audio")
    total_bytes = 0
    for ep_id in to_process:
        blob_name = f"{args.cert}/{args.format}/episodes/{ep_id}.mp3"
        props = audio_container.get_blob_client(blob_name).get_blob_properties()
        total_bytes += props.size

    total_seconds = (total_bytes * 8) / 192_000  # 192kbps MP3
    total_hours = total_seconds / 3600
    estimated_cost = total_hours * 1.0  # $1/hr for STT standard

    print(f"\nEpisodes to process: {len(to_process)}")
    print(f"Estimated audio: {total_hours:.2f} hours")
    print(f"Estimated STT cost: ${estimated_cost:.2f}")

    if args.dry_run:
        for ep_id in to_process:
            print(f"  - {ep_id}")
        return

    # Process each episode
    scripts_container = blob_service.get_container_client("scripts")
    processed = 0
    errors = 0

    for ep_id in to_process:
        print(f"\n[{processed + 1}/{len(to_process)}] Processing episode {ep_id}...")

        # Download audio to temp file
        audio_blob = f"{args.cert}/{args.format}/episodes/{ep_id}.mp3"
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = tmp.name
                blob_client = audio_container.get_blob_client(audio_blob)
                download = blob_client.download_blob()
                tmp.write(download.readall())

            # Run STT
            print(f"  Running speech recognition...")
            word_boundaries = recognize_with_word_timestamps(tmp_path)
            print(f"  Captured {len(word_boundaries)} word boundaries")

            # Upload sync JSON
            sync_blob = f"{args.cert}/{args.format}/sync/{ep_id}.sync.json"
            sync_json = json.dumps(word_boundaries, separators=(",", ":"))
            scripts_container.upload_blob(
                name=sync_blob,
                data=sync_json,
                overwrite=True,
                content_settings=ContentSettings(content_type="application/json"),
            )
            print(f"  Uploaded: {sync_blob}")
            processed += 1

        except Exception as e:
            print(f"  ERROR: {e}")
            errors += 1

        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    print(f"\nDone. Processed: {processed}, Errors: {errors}")


if __name__ == "__main__":
    main()
