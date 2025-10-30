
# AI-102 Audio Course Prompt Flow

## Import
1. Zip this folder.
2. Azure AI Studio → Prompt flow → Import from local files → select the zip.
3. In Project Settings → Connections, ensure:
   - `azure_openai_chat` (Chat Completions on GPT-4o / GPT-4 Turbo)
4. Configure environment variables or Managed Identity for Speech & Blob (see `.env.example`).

## Configure Inputs
- `episodes_count`: 35 (default)
- `voice_name`: en-US-GuyNeural
- `audio_format`: mp3
- `storage_container`: ai102-audio

## Outputs
- `course_outline`: JSON course plan
- `produced_metadata`: JSON array of URLs (audio, narration.md, narration.ssml per episode)
