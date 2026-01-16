# Azure AI-Powered Certification Audio Learning Platform

> **Plan Version**: 1.0  
> **Created**: January 16, 2026  
> **Status**: Approved, ready for implementation

## Overview

A fully automated Azure-native system that auto-discovers Microsoft Learn content from any Microsoft certification exam page, generates ~10-minute audio episodes (instructional with `en-US-GuyNeural` by default, or two-voice podcast-style), serves them through an optional B2C-authenticated web player with progress tracking, and creates sequentially-numbered amendment episodes when exam content changes.

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Audio format | Selectable per-course (not per-episode) | Consistency across learning experience |
| Episode length | ~10 minutes target, split if longer | Research-backed optimal retention; never truncate content |
| Instructional voice | `en-US-GuyNeural` with `newscast-casual` style | Authoritative yet approachable per learning science |
| Podcast voices | `en-US-GuyNeural` (host) + `en-US-TonyNeural` (expert) | Contrasting pitch for natural dialogue |
| Content updates | Amendment episodes with new sequential numbers | Links to original via metadata |
| Authentication | Optional Azure AD B2C (feature flag) | Same subscription; disabled by default for flexibility |
| Content source | Auto-discover from exam page | Stays current with Microsoft Learn changes |
| Storage access | Managed Identity, no public access | Enterprise-compatible; no SAS tokens in URLs |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           GitHub Actions CI/CD                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  deploy-infra.yml → Bicep → Azure Resources                                 │
│  generate-content.yml → PromptFlow → Episodes                               │
│  refresh-content.yml → Delta Check → Amendment Episodes                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        ▼                             ▼                             ▼
┌───────────────┐           ┌─────────────────┐           ┌─────────────────┐
│  AI Services  │           │      Data       │           │       Web       │
├───────────────┤           ├─────────────────┤           ├─────────────────┤
│ • OpenAI      │           │ • Cosmos DB     │           │ • Static Web    │
│ • AI Search   │◄─────────►│   (episodes,    │◄─────────►│   Apps          │
│ • Doc Intel   │           │    sources,     │           │ • Functions API │
│ • Speech      │           │    progress)    │           │ • (B2C optional)│
└───────────────┘           │ • Blob Storage  │           └─────────────────┘
                            │   (audio files) │
                            └─────────────────┘
```

## Azure Services Required

| Service | Purpose |
|---------|---------|
| Azure OpenAI Service | GPT-4o for script generation, QC, SSML conversion |
| Azure AI Search | RAG indexing of Microsoft Learn content |
| Azure AI Document Intelligence | Extract content from Learn pages |
| Azure AI Speech | Neural TTS with SSML prosody control |
| Azure Cosmos DB | Episode metadata, source tracking, user progress |
| Azure Blob Storage | Audio files, scripts, SSML (private access) |
| Azure Static Web Apps | Frontend hosting with built-in auth |
| Azure Functions | Backend API for audio proxy, progress sync |
| Azure AD B2C (optional) | User authentication for progress sync |

## File Structure

```
/
├── .github/
│   ├── agents.md                    # Agent definitions for Copilot
│   └── workflows/
│       ├── deploy-infra.yml         # Bicep deployment
│       ├── generate-content.yml     # Content generation pipeline
│       └── refresh-content.yml      # Scheduled update checks
├── infra/
│   ├── main.bicep                   # Orchestrator
│   ├── main.bicepparam              # Parameters file
│   └── modules/
│       ├── ai-services.bicep        # OpenAI, Speech, Doc Intel, AI Search
│       ├── data.bicep               # Cosmos DB, Storage
│       ├── web.bicep                # Static Web Apps, Functions
│       └── identity.bicep           # B2C (conditional)
├── src/
│   ├── functions/                   # Azure Functions backend
│   │   ├── host.json
│   │   ├── get-episodes/            # List episodes for a certification
│   │   ├── get-audio/               # Proxy audio from Blob via MI
│   │   ├── update-progress/         # Save user progress
│   │   └── trigger-refresh/         # Manual refresh trigger
│   ├── web/                         # Static Web Apps frontend
│   │   ├── index.html
│   │   ├── css/
│   │   ├── js/
│   │   └── staticwebapp.config.json
│   └── pipeline/                    # PromptFlow + ingestion
│       ├── flow.dag.yaml
│       ├── requirements.txt
│       ├── prompts/
│       │   ├── system_course_plan.txt
│       │   ├── system_narration_instructional.txt
│       │   ├── system_narration_podcast.txt
│       │   ├── system_amendment.txt
│       │   ├── system_ssml.txt
│       │   └── ...
│       └── tools/
│           ├── discover_exam_content.py
│           ├── check_content_delta.py
│           ├── synthesize_audio.py
│           └── upload_to_blob.py
├── PLAN.md                          # This document
└── README.md                        # User-facing documentation
```

## Blob Storage Structure

```
/{certificationId}/{format}/
├── episodes/
│   ├── 001.mp3
│   ├── 002.mp3
│   └── ...
├── scripts/
│   ├── 001.md
│   ├── 002.md
│   └── ...
└── metadata/
    └── index.json
```

## Cosmos DB Schema

### Container: `episodes`
```json
{
  "id": "ai-102-instructional-001",
  "certificationId": "ai-102",
  "format": "instructional",
  "sequenceNumber": 1,
  "title": "Introduction to Azure AI Services",
  "skillDomain": "Plan and manage an Azure AI solution",
  "sourceUrls": ["https://learn.microsoft.com/..."],
  "contentHash": "sha256:abc123...",
  "amendmentOf": null,
  "durationSeconds": 612,
  "createdAt": "2026-01-16T10:00:00Z"
}
```

### Container: `sources`
```json
{
  "id": "sha256:abc123...",
  "url": "https://learn.microsoft.com/en-us/azure/ai-services/...",
  "contentHash": "sha256:abc123...",
  "lastChecked": "2026-01-16T10:00:00Z",
  "episodeRefs": ["ai-102-instructional-001", "ai-102-instructional-002"]
}
```

### Container: `userProgress`
```json
{
  "id": "user123-ai-102",
  "userId": "user123",
  "certificationId": "ai-102",
  "format": "instructional",
  "progress": {
    "001": { "completed": true, "position": 612 },
    "002": { "completed": false, "position": 245 }
  },
  "lastUpdated": "2026-01-16T15:30:00Z"
}
```

## SSML Configuration

### Instructional Format
```xml
<speak version="1.0" xmlns="http://www.w3.org/2001/Synthesis" 
       xmlns:mstts="http://www.w3.org/2001/mstts" xml:lang="en-US">
  <voice name="en-US-GuyNeural">
    <mstts:express-as style="newscast-casual" styledegree="0.75">
      <prosody rate="-8%" pitch="-2%">
        {content with <break time="500ms"/> after key concepts}
      </prosody>
    </mstts:express-as>
  </voice>
</speak>
```

### Podcast Format
```xml
<speak version="1.0" xmlns="http://www.w3.org/2001/Synthesis" 
       xmlns:mstts="http://www.w3.org/2001/mstts" xml:lang="en-US">
  <!-- Host -->
  <voice name="en-US-GuyNeural">
    <mstts:express-as style="friendly">
      <prosody rate="default">{host dialogue}</prosody>
    </mstts:express-as>
  </voice>
  <break time="300ms"/>
  <!-- Expert -->
  <voice name="en-US-TonyNeural">
    <prosody rate="-5%">{expert dialogue}</prosody>
  </voice>
</speak>
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `certificationId` | string | `ai-102` | Microsoft certification ID |
| `audioFormat` | string | `instructional` | `instructional` or `podcast` |
| `enableB2C` | bool | `false` | Enable Azure AD B2C authentication |
| `location` | string | `eastus2` | Azure region for resources |
| `examPageUrl` | string | auto | Override exam page URL if needed |

## Implementation Order

1. **Infrastructure** (`infra/`) - Bicep modules for all Azure resources
2. **Content Pipeline** (`src/pipeline/`) - Ingestion, PromptFlow, audio synthesis
3. **Backend API** (`src/functions/`) - Episode list, audio proxy, progress
4. **Frontend** (`src/web/`) - Audio player with progress tracking
5. **CI/CD** (`.github/workflows/`) - Automated deployment and refresh
6. **Agents** (`.github/agents.md`) - Copilot agent definitions

## Agents

| Agent | Scope | Responsibilities |
|-------|-------|------------------|
| `content-pipeline` | `src/pipeline/**` | Exam discovery, RAG, script generation, audio synthesis |
| `frontend` | `src/web/**` | Audio player UI, progress tracking, styling |
| `infra` | `infra/**`, `.github/workflows/**` | Bicep modules, CI/CD pipelines |
| `refresh` | `src/pipeline/tools/check_content_delta.py`, `src/functions/trigger-refresh/` | Delta detection, amendment episode logic |
