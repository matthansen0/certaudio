"""
Synthesize audio from SSML using Azure AI Speech.
"""

import os
import tempfile
import uuid
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk
from azure.identity import DefaultAzureCredential
from promptflow.core import tool


def get_speech_config() -> speechsdk.SpeechConfig:
    """Create Speech SDK config using managed identity or key."""
    speech_endpoint = os.environ.get("SPEECH_ENDPOINT")
    speech_key = os.environ.get("SPEECH_KEY")
    speech_region = os.environ.get("SPEECH_REGION")

    # Default fallback for region
    if not speech_region:
        speech_region = "centralus"

    print(f"Using Speech region: {speech_region}")
    print(f"Speech endpoint: {speech_endpoint}")

    if speech_key:
        # Use API key authentication
        print("Using API key authentication")
        config = speechsdk.SpeechConfig(subscription=speech_key, region=speech_region)
    elif speech_endpoint:
        # Use Microsoft Entra authentication for SpeechSynthesizer.
        # Per Microsoft docs, SpeechSynthesizer requires an auth token in the form:
        #   aad#<resourceId>#<entraAccessToken>
        # and SpeechConfig must be created with auth_token + region (not endpoint/host).
        print("Using managed identity with Entra authentication")

        subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID")
        resource_group = os.environ.get("AZURE_RESOURCE_GROUP")
        if not subscription_id or not resource_group:
            raise ValueError(
                "AZURE_SUBSCRIPTION_ID and AZURE_RESOURCE_GROUP are required for Entra Speech auth."
            )

        # Speech endpoint is typically like:
        #   https://<speech-resource-name>.cognitiveservices.azure.com/
        speech_host = speech_endpoint.rstrip("/")
        host_no_scheme = speech_host.split("//", 1)[-1]
        resource_name = host_no_scheme.split(".", 1)[0]
        if not resource_name:
            raise ValueError(f"Could not parse Speech resource name from SPEECH_ENDPOINT={speech_endpoint}")

        resource_id = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{resource_name}"
        )

        credential = DefaultAzureCredential()
        aad_token = credential.get_token("https://cognitiveservices.azure.com/.default")
        authorization_token = f"aad#{resource_id}#{aad_token.token}"

        config = speechsdk.SpeechConfig(auth_token=authorization_token, region=speech_region)
    else:
        raise ValueError("SPEECH_ENDPOINT or SPEECH_KEY must be set")

    # Configure audio output format
    config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio48Khz192KBitRateMonoMp3
    )

    return config


def synthesize_ssml(ssml_content: str, output_path: str) -> tuple[bool, float]:
    """
    Synthesize SSML to audio file.

    Args:
        ssml_content: SSML markup
        output_path: Path to save MP3 file

    Returns:
        Tuple of (success, duration_seconds)
    """
    config = get_speech_config()

    # Configure audio output to file
    audio_config = speechsdk.audio.AudioOutputConfig(filename=output_path)

    # Create synthesizer
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=config, audio_config=audio_config
    )

    # Synthesize
    result = synthesizer.speak_ssml_async(ssml_content).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        # Calculate duration from audio data
        # MP3 at 192kbps: duration = file_size_bytes * 8 / 192000
        file_size = os.path.getsize(output_path)
        duration_seconds = (file_size * 8) / 192000
        return True, duration_seconds

    elif result.reason == speechsdk.ResultReason.Canceled:
        cancellation = result.cancellation_details
        print(f"Speech synthesis canceled: {cancellation.reason}")
        if cancellation.reason == speechsdk.CancellationReason.Error:
            print(f"Error details: {cancellation.error_details}")
        return False, 0

    return False, 0


def synthesize_audio_segments(
    ssml_segments: list[str],
    output_path: str,
) -> tuple[bool, float]:
    """Synthesize multiple SSML segments and concatenate resulting MP3 files.

    This avoids the Speech service max media duration limit (~10 minutes) per request.
    """
    if not ssml_segments:
        return False, 0

    temp_paths: list[str] = []
    total_duration = 0.0

    base = Path(output_path)
    for idx, segment in enumerate(ssml_segments, start=1):
        part_path = str(base.with_name(f"{base.stem}.part{idx:02d}{base.suffix}"))
        ok, dur = synthesize_ssml(segment, part_path)
        if not ok:
            # Best-effort cleanup
            for p in temp_paths:
                try:
                    os.remove(p)
                except Exception:
                    pass
            return False, 0
        temp_paths.append(part_path)
        total_duration += dur

    # Concatenate MP3 parts. MP3 frames are typically concat-safe when encoded consistently.
    with open(output_path, "wb") as out_f:
        for p in temp_paths:
            with open(p, "rb") as in_f:
                out_f.write(in_f.read())

    for p in temp_paths:
        try:
            os.remove(p)
        except Exception:
            pass

    return True, total_duration


@tool
def synthesize_audio(
    ssml_content: str,
    episode_number: int,
    certification_id: str,
    audio_format: str,
) -> dict:
    """
    Synthesize audio from SSML content.

    Args:
        ssml_content: SSML markup for synthesis
        episode_number: Episode sequence number
        certification_id: Certification ID
        audio_format: 'instructional' or 'podcast'

    Returns:
        Dict with audio_path and duration_seconds
    """
    # Create temp file for audio output
    temp_dir = tempfile.mkdtemp()
    filename = f"{certification_id}_{audio_format}_{episode_number:03d}.mp3"
    output_path = os.path.join(temp_dir, filename)

    print(f"Synthesizing audio for episode {episode_number}...")

    # Synthesize
    success, duration = synthesize_ssml(ssml_content, output_path)

    if not success:
        raise RuntimeError(f"Audio synthesis failed for episode {episode_number}")

    print(f"Audio synthesized: {duration:.1f} seconds")

    return {
        "audio_path": output_path,
        "duration_seconds": duration,
        "filename": filename,
    }
