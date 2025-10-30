
import os, re, uuid

def _slugify(text: str) -> str:
    return re.sub(r'[^a-z0-9-]+','', re.sub(r'\s+','-', (text or 'episode').lower())).strip('-')[:80]

def entry(ssml_text: str, voice_name: str, audio_format: str, episode_title: str) -> str:
    speech_key = os.getenv("SPEECH_KEY")
    speech_region = os.getenv("SPEECH_REGION", "eastus")
    if not speech_key:
        raise RuntimeError("SPEECH_KEY not set. Configure in Prompt flow Environment or use a connection binding.")

    import azure.cognitiveservices.speech as speechsdk
    speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=speech_region)
    if voice_name:
        speech_config.speech_synthesis_voice_name = voice_name

    if (audio_format or 'mp3').lower() == "wav":
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm
        )
        ext = "wav"
    else:
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3
        )
        ext = "mp3"

    out_name = f"{_slugify(episode_title)}-{uuid.uuid4().hex[:8]}.{ext}"
    audio_config = speechsdk.audio.AudioOutputConfig(filename=out_name)
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
    result = synthesizer.speak_ssml_async(ssml_text).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        details = getattr(result, 'cancellation_details', None)
        raise RuntimeError(f"Synthesis failed: {getattr(details, 'reason', 'unknown')}")
    return os.path.abspath(out_name)
