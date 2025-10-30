# 🎯 AI-102 Audio Training Course Pipeline (Azure AI Studio)

A complete guide to building an end-to-end **AI-102 audio training course generator** entirely inside **Azure AI Studio (Prompt Flow)** and **Azure AI Speech**.

---

## 🧩 Overview

This pipeline automatically:
1. Generates a 35-episode AI-102 course outline
2. Writes full narration for each episode
3. Performs quality check & auto-revision
4. Converts narration to SSML
5. Synthesizes audio via Azure AI Speech
6. Uploads `.md`, `.ssml`, and `.mp3` to Azure Blob Storage

---

## 🧱 Prerequisites

### 1️⃣ Azure Resources
- Azure AI Studio (aka Azure AI Foundry)
- Azure OpenAI (GPT-4o or GPT-4 Turbo)
- Azure AI Speech
- Azure Storage Account
- *(Optional)* Azure Key Vault

### 2️⃣ Permissions
- Cognitive Services Contributor or Owner on all resources
- If using Managed Identity → grant `Storage Blob Data Contributor`

### 3️⃣ Environment Variables
If not using Managed Identity:

```bash
SPEECH_KEY=xxxxxxxxxxxx
SPEECH_REGION=eastus
STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=...
```

---

## 📁 Folder Structure

```
ai102-promptflow/
├─ flow.dag.yaml
├─ requirements.txt
├─ README.md
├─ .env.example
├─ prompts/
│  ├─ system_course_plan.txt
│  ├─ user_course_plan.txt
│  ├─ system_narration.txt
│  ├─ user_narration.txt
│  ├─ system_qc.txt
│  ├─ user_qc.txt
│  ├─ system_autorevise.txt
│  ├─ user_autorevise.txt
│  ├─ system_ssml.txt
│  └─ user_ssml.txt
└─ tools/
   ├─ synthesize_audio.py
   └─ upload_to_blob.py
```

Zip this folder → **Import to Azure AI Studio → Prompt Flow → Import from local files.**

---

## ⚙️ Environment File (`.env.example`)

```bash
SPEECH_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxx
SPEECH_REGION=eastus
STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net

# Optional - Managed Identity Mode
AZURE_USE_MSI=false
BLOB_ACCOUNT_URL=https://<account>.blob.core.windows.net
```

---

## 🧠 Core Components

### 1️⃣ `flow.dag.yaml`
Defines the logic: Course Plan → ForEach Episode → Narration → QC → Revise → SSML → Audio → Upload

Produces three artifacts per episode: `.md`, `.ssml`, `.mp3`.

Outputs `produced_metadata` (JSON list of all URLs).

👉 Use the full YAML version provided in the reference.

---

### 2️⃣ `tools/synthesize_audio.py`
Converts SSML → Audio using Azure AI Speech.

```python
import os, re, uuid

def slugify(text):
    return re.sub(r'[^a-z0-9-]+','', re.sub(r'\s+','-', text.lower())).strip('-')[:80]

def entry(ssml_text, voice_name, audio_format, episode_title):
    import azure.cognitiveservices.speech as speechsdk
    speech_key = os.getenv("SPEECH_KEY")
    region = os.getenv("SPEECH_REGION", "eastus")
    speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=region)
    speech_config.speech_synthesis_voice_name = voice_name
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3
    )
    out_name = f"{slugify(episode_title)}-{uuid.uuid4().hex[:8]}.mp3"
    audio_cfg = speechsdk.audio.AudioOutputConfig(filename=out_name)
    synth = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_cfg)
    res = synth.speak_ssml_async(ssml_text).get()
    if res.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise RuntimeError("Synthesis failed")
    return os.path.abspath(out_name)
```

---

### 3️⃣ `tools/upload_to_blob.py`
Uploads any local file (`.md`, `.ssml`, `.mp3`) to Azure Blob Storage.

```python
import os

def entry(file_path, container_name, blob_name):
    from azure.storage.blob import BlobServiceClient
    if os.getenv("AZURE_USE_MSI","false").lower() in ("1","true","yes"):
        from azure.identity import DefaultAzureCredential
        url = os.getenv("BLOB_ACCOUNT_URL")
        cred = DefaultAzureCredential()
        bsc = BlobServiceClient(account_url=url, credential=cred)
    else:
        bsc = BlobServiceClient.from_connection_string(os.getenv("STORAGE_CONNECTION_STRING"))
    container = bsc.get_container_client(container_name)
    try: container.create_container()
    except Exception: pass
    with open(file_path,"rb") as f:
        container.upload_blob(name=blob_name, data=f, overwrite=True, content_type=_mime(file_path))
    return container.get_blob_client(blob_name).url

def _mime(p):
    e = os.path.splitext(p)[1].lower()
    return {
        ".mp3":"audio/mpeg",
        ".wav":"audio/wav",
        ".md":"text/markdown",
        ".ssml":"application/ssml+xml"
    }.get(e,"application/octet-stream")
```

---

### 4️⃣ Prompt Files (Examples)

#### `prompts/system_course_plan.txt`
```
You are a senior Microsoft Azure instructor.
Produce an up-to-date, structured AI-102 course plan for an audio format.
```

#### `prompts/user_course_plan.txt`
```
Create a 35-episode plan for “AI-102 Azure AI Engineer Associate”.
Return JSON with: number, slug, title, outcomes[], services[], links[], exam_tips[].
```

#### `prompts/system_narration.txt`
```
You are an Azure instructor recording a 10-minute AI-102 lesson.
Explain clearly, emphasize exam tips, avoid marketing language.
```

#### `prompts/user_narration.txt`
```
Episode JSON:
{{episode}}
Write a 1,200–1,500-word narration with intro, concepts, examples, pitfalls, exam tips, and recap.
```

#### `prompts/system_ssml.txt`
```
You are an SSML transformer for Azure AI Speech (en-US-GuyNeural).
Use <p>, <break>, and <prosody rate="medium">. Return only valid SSML.
```

#### `prompts/user_ssml.txt`
```
<NARRATION>
{{final_narration.final_text}}
</NARRATION>
```

---

## 📦 Requirements

```
azure-cognitiveservices-speech==1.37.0
azure-storage-blob==12.22.0
azure-identity==1.17.1
```

---

## 🚀 Run the Flow

1. Import folder → Azure AI Studio → *Prompt Flow → Import from local files*
2. Ensure connections:
   - `azure_openai_chat` → GPT-4o / Turbo deployment
   - Speech & Storage → via Managed Identity or env vars
3. Set inputs:

```
episodes_count = 35
voice_name = en-US-GuyNeural
audio_format = mp3
storage_container = ai102-audio
```

4. Run the flow.
5. Review outputs:
   - `course_outline` → JSON episode plan
   - `produced_metadata` → blob URLs for each episode

Example output:

```json
[
  {
    "number": 1,
    "title": "Introduction & Exam Blueprint",
    "slug": "introduction-and-exam-blueprint",
    "urls": {
      "audio": "https://.../ai102/1-introduction-and-exam-blueprint.mp3",
      "narration_md": "https://.../ai102/1-introduction-and-exam-blueprint/narration.md",
      "ssml": "https://.../ai102/1-introduction-and-exam-blueprint/narration.ssml"
    }
  }
]
```

---

## ✅ Best Practices

| Stage | Tool | Output |
|-------|------|---------|
| 1. Episode Outline | Azure OpenAI | JSON plan |
| 2. Narration | Azure OpenAI | `.md` text |
| 3. Quality Check + Auto-Revise | Azure OpenAI | final narration |
| 4. SSML Conversion | Azure OpenAI | `.ssml` |
| 5. Audio Synthesis | Azure AI Speech | `.mp3` |
| 6. Upload | Azure Blob Storage | URLs for all artifacts |

---

### 💡 Tips
- Model: GPT-4o preferred for long context.
- Temperature: 0.4 (narration), 0.3 (SSML).
- Parallelism: Prompt Flow supports concurrent episode generation.
- Storage: enable lifecycle rules for cleanup.
- Expansion: add quiz or transcript nodes later.

---

## 🧾 Summary

This flow lets you build a **complete Azure-based audio training course generator** — from outline to final narrated audio — with zero local dependencies. Once imported and configured, running it will produce 35 professional-grade AI-102 study episodes automatically.
