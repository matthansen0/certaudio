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
        # Use managed identity with endpoint
        # Speech SDK requires endpoint-based auth for AAD tokens
        print("Using managed identity with endpoint authentication")
        credential = DefaultAzureCredential()
        
        # Get token for Cognitive Services
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        
        # For custom domain endpoints:
        # 1. Create SpeechConfig with just the host
        # 2. Set authorization_token separately (cannot pass both to constructor)
        speech_host = speech_endpoint.rstrip('/')
        
        # Create config with endpoint only
        config = speechsdk.SpeechConfig(host=speech_host)
        
        # Set authorization token separately using aad# format for custom domains
        config.authorization_token = f"aad#{speech_host}#{token.token}"
    else:
        # Fallback to region-based with token
        print("Using managed identity with region-based authentication")
        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        
        config = speechsdk.SpeechConfig(
            auth_token=token.token,
            region=speech_region,
        )

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
