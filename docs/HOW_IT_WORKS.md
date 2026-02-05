# How It Works: A Deep Dive

> This document explains not just **what** the automation does, but **why** each decision was made—so you can understand, customize, or even rebuild it yourself.

## Table of Contents

1. [The Big Picture](#the-big-picture)
2. [Infrastructure Layer](#infrastructure-layer)
   - [AI Services](#ai-services)
   - [Data Layer](#data-layer)
   - [Web Layer](#web-layer)
3. [Content Pipeline](#content-pipeline)
   - [Discovery](#discovery)
   - [RAG Indexing](#rag-indexing)
   - [Episode Generation](#episode-generation)
4. [Authentication & Security](#authentication--security)
5. [Cost Optimization](#cost-optimization)
6. [Workflow Orchestration](#workflow-orchestration)
7. [Customization Guide](#customization-guide)

---

## The Big Picture

Here's the thing: this project started from a simple question—*"What if I could turn Microsoft Learn content into podcast episodes for my commute?"*

The answer turned into a fully automated pipeline that:

```
Microsoft Learn → Discovery → RAG Index → AI Narration → Text-to-Speech → Web Player
```

**The core insight**: Microsoft Learn already has great content, but it's designed for reading. We're transforming it into audio, using AI to make it conversational, and serving it through a simple web player.

### Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           GitHub Actions CI/CD                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  deploy-infra.yml → Bicep → Azure Resources (one-time setup)               │
│  generate-content.yml → Python → Episodes (per certification)               │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        ▼                             ▼                             ▼
┌───────────────┐           ┌─────────────────┐           ┌─────────────────┐
│  AI Services  │           │      Data       │           │       Web       │
├───────────────┤           ├─────────────────┤           ├─────────────────┤
│ • OpenAI      │           │ • Cosmos DB     │           │ • Static Web    │
│ • AI Search*  │◄─────────►│   (metadata)    │◄─────────►│   Apps          │
│ • AI Foundry* │           │ • Blob Storage  │           │ • Functions API │
│ • Speech      │           │   (audio files) │           └─────────────────┘
└───────────────┘           └─────────────────┘
                              * AI Search deployed only during generation
                              * AI Foundry deployed only with Study Partner
```

---

## Infrastructure Layer

All infrastructure is defined in [infra/](../infra/) using Azure Bicep. Here's why we chose each service.

### AI Services

**File**: [`infra/modules/ai-services.bicep`](../infra/modules/ai-services.bicep)

#### Azure OpenAI (`Microsoft.CognitiveServices/accounts`, kind: `OpenAI`)

**What We Deployed**:
- S0 tier OpenAI service
- GPT-4o deployment (`GlobalStandard`, 30K TPM capacity)
- text-embedding-3-large deployment (`Standard`, 10K TPM)

**Why This Configuration**:
```bicep
// Using separate location due to model availability constraints
resource openAi 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  location: openAiLocation  // Not the same as other resources!
  ...
}
```

Here's the thing about Azure OpenAI: GPT-4o isn't available everywhere. We use `eastus2` because:
- GPT-4o GlobalStandard is available there
- It has good capacity (less rate limiting)
- It's close enough to `centralus` (where other resources live) that latency is fine

**Key Settings You Should Know**:

| Parameter | Value | Why |
|-----------|-------|-----|
| `openAiLocation` | `eastus2` | GPT-4o availability |
| `capacity: 30` | 30K tokens/min | Balances throughput vs. quota |
| `sku: GlobalStandard` | Pay-per-token | Cheaper than provisioned for bursty workloads |

**Tradeoffs**:
- ❌ Could use `gpt-4o-mini` for cheaper narration (faster, ~5x cheaper, but lower quality)
- ❌ Could use provisioned throughput for guaranteed capacity (expensive for occasional use)
- ✅ GlobalStandard is perfect for "generate once, serve forever" workflows

**🎓 Learn More**:
- [Azure OpenAI Model Availability](https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models#model-summary-table-and-region-availability)
- [Quotas and limits](https://learn.microsoft.com/en-us/azure/ai-services/openai/quotas-limits)

---

#### Azure AI Foundry (Optional - Study Partner)

**File**: [`infra/modules/ai-foundry.bicep`](../infra/modules/ai-foundry.bicep)

**What We Deployed** (when `enableStudyPartner=true`):
- AIServices account with `allowProjectManagement: true`
- AI Foundry Project (`study-partner`) with System-assigned Managed Identity
- GPT-4o model deployment (`GlobalStandard`, 30K TPM)
- Project connections to CosmosDB, Storage, and AI Search

**Why AI Foundry (not direct OpenAI calls)**:

The Study Partner feature uses AI Foundry's **Agent Service** rather than direct OpenAI API calls. Here's why:

1. **Built-in tool orchestration** - Agents can use tools (like Azure AI Search) natively
2. **Conversation threads** - SDK manages conversation state automatically
3. **Grounding** - Native integration with Azure AI Search for RAG
4. **Enterprise features** - Content filtering, logging, responsible AI controls

**Architecture**:
```
User Query
    │
    ▼
┌───────────────────────────────────────────────────┐
│              Azure Functions                       │
│  POST /api/chat                                   │
│    └─► AIProjectClient (azure-ai-projects SDK)   │
│         └─► agents.create_and_run_agent()        │
└───────────────────────────────────────────────────┘
                    │
                    ▼
┌───────────────────────────────────────────────────┐
│           AI Foundry Project                       │
│  ┌─────────────────────────────────────────────┐  │
│  │              GPT-4o Agent                   │  │
│  │  Instructions: Certification exam prep      │  │
│  │  Model: gpt-4o                              │  │
│  │  Tools: [AzureAISearchTool]                 │  │
│  └─────────────────────────────────────────────┘  │
│                    │                              │
│                    ▼                              │
│  ┌─────────────────────────────────────────────┐  │
│  │         Azure AI Search Tool                │  │
│  │  Index: certification-content               │  │
│  │  Connection: via project connection         │  │
│  └─────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────┘
```

**Key Settings You Should Know**:

| Parameter | Value | Why |
|-----------|-------|-----|
| `foundryLocation` | `swedencentral` | Full AI Foundry feature support |
| `kind: AIServices` | Multi-service | Enables project management |
| `allowProjectManagement` | `true` | Required for Foundry projects |
| `model: gpt-4o` | GlobalStandard | Matches core OpenAI deployment |

**Project Connections**:
The AI Foundry project has connections to existing resources:

| Connection | Resource | Purpose |
|------------|----------|---------|
| CosmosDB | `certaudio-dev-cosmos-*` | Thread storage (agent conversations) |
| Storage | `certaudiodevst*` | File storage (agent attachments) |
| AI Search | `certaudio-dev-search-*` | Vector store for RAG retrieval |

**Tradeoffs**:
- ❌ Adds ~$80/month (primarily AI Search Basic tier)
- ❌ Requires supported region for Foundry (swedencentral)
- ✅ Native tool integration is cleaner than manual RAG orchestration
- ✅ Managed conversation threads simplify state management
- ✅ Enterprise-grade with built-in responsible AI controls

**Implementation Notes**:
- The capability hosts (for agent capabilities) are disabled in Bicep due to Azure preview API issues
- The `azure-ai-projects` SDK can create agents without explicit capability hosts
- Functions app has fallback logic: tries Foundry Agent first, falls back to OpenAI+RAG if unavailable

**🎓 Learn More**:
- [Azure AI Foundry Overview](https://learn.microsoft.com/en-us/azure/ai-studio/what-is-ai-studio)
- [Azure AI Projects SDK](https://learn.microsoft.com/en-us/python/api/overview/azure/ai-projects-readme)
- [Standard Agent Setup](https://learn.microsoft.com/en-us/azure/ai-services/agents/quickstart)

---

#### Azure AI Speech (`Microsoft.CognitiveServices/accounts`, kind: `SpeechServices`)

**What We Deployed**:
- S0 tier Speech service (pay-per-character)
- Neural TTS voices (not the old standard voices)

**Why This Configuration**:
We use neural voices because they sound *dramatically* better than standard voices. The difference between `en-US-Guy` (standard) and `en-US-GuyNeural` is night and day.

**Key Settings You Should Know**:

The voice selection happens at generation time, not deployment time:

```yaml
# In .github/workflows/generate-content.yml
instructionalVoice:
  default: 'en-US-AndrewNeural'  # Male, warm and professional
```

Popular voice choices:
| Voice | Style | Good For |
|-------|-------|----------|
| `en-US-AndrewNeural` | Warm, professional | Instructional (our default) |
| `en-US-GuyNeural` | Newscast, authoritative | Technical content |
| `en-US-AvaNeural` | Engaging, friendly | Conversational |

**Tradeoffs**:
- ❌ HD voices (`-HD` suffix) sound even better but cost 2x more
- ❌ Custom Neural Voice (train your own) is cool but requires audio samples
- ✅ Standard neural voices are the sweet spot for quality vs. cost

**🎓 Learn More**:
- [Voice Gallery (listen to samples!)](https://speech.microsoft.com/portal/voicegallery)
- [SSML Prosody for natural speech](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-voice)

---

### Data Layer

**File**: [`infra/modules/data.bicep`](../infra/modules/data.bicep)

#### Azure Cosmos DB (`Microsoft.DocumentDB/databaseAccounts`)

**What We Deployed**:
- Serverless Cosmos DB account (pay-per-RU, no provisioned capacity)
- Three containers: `episodes`, `sources`, `userProgress`
- Partition key: `/certificationId` (episodes, sources) and `/userId` (progress)

**Why Serverless**:
```bicep
capabilities: [
  {
    name: 'EnableServerless'
  }
]
```

Episode metadata is accessed infrequently—users load the episode list once, then stream audio. Serverless is perfect for this "bursty reads, rare writes" pattern. You'd pay ~$25/month minimum for provisioned throughput vs. ~$2/month for serverless with typical usage.

**Why These Partition Keys**:
- `/certificationId` for episodes: All episodes for one cert are in one partition → fast "get all DP-700 episodes" queries
- `/userId` for progress: All progress for one user is in one partition → fast "get my progress" queries

**Tradeoffs**:
- ❌ Provisioned throughput would give consistent performance (overkill for this use case)
- ❌ Could use a single container with composite key (more complex queries)
- ✅ Serverless with simple partitioning is the 80/20 solution

**🎓 Learn More**:
- [Serverless vs Provisioned](https://learn.microsoft.com/en-us/azure/cosmos-db/throughput-serverless)
- [Partition key best practices](https://learn.microsoft.com/en-us/azure/cosmos-db/partitioning-overview#choose-partitionkey)

---

#### Azure Storage Account (`Microsoft.Storage/storageAccounts`)

**What We Deployed**:
- Standard LRS storage (cheapest tier)
- Private access only (`allowBlobPublicAccess: false`)
- No shared key access (`allowSharedKeyAccess: false`)

**Why Private + No Shared Key**:
```bicep
properties: {
  allowBlobPublicAccess: false
  allowSharedKeyAccess: false  // Forces Managed Identity or Azure AD
}
```

This is about security posture. Many enterprise Azure tenants enforce this via policy anyway. By setting it explicitly:
1. Audio files aren't publicly accessible (good—they're behind our API)
2. No connection strings floating around (great—only managed identities can access)

**Blob Organization**:
```
audio/
  └── {certificationId}/
      └── {format}/
          └── episodes/
              ├── 001.mp3
              ├── 002.mp3
              └── ...

scripts/
  └── {certificationId}/
      └── {format}/
          ├── 001.md
          ├── 002.md
          └── ...
```

**Tradeoffs**:
- ❌ Public blob access would be simpler (just give users the URL)—but less secure
- ❌ SAS tokens would work—but they expire and leak
- ✅ Proxying through Functions with Managed Identity is the enterprise-grade approach

**🎓 Learn More**:
- [Storage account security](https://learn.microsoft.com/en-us/azure/storage/common/storage-account-overview#security)
- [Managed Identity for storage](https://learn.microsoft.com/en-us/azure/storage/blobs/authorize-access-azure-active-directory)

---

### Web Layer

**File**: [`infra/modules/web.bicep`](../infra/modules/web.bicep)

#### Azure Static Web Apps

**What We Deployed**:
- Standard tier SWA (supports linked backends)
- Linked to Azure Functions for API

**Why Static Web Apps (not App Service)**:
1. **Free/cheap hosting** for static files (HTML/CSS/JS)
2. **Built-in auth** (Azure AD, GitHub, etc.) if you want it later
3. **Linked backends** let you connect Functions as `/api/*`
4. **Global CDN** included

**Why Standard Tier**:
```bicep
sku: {
  name: 'Standard'  // Not Free!
}
```

We need Standard because:
- Free tier doesn't support linked backends (you'd need SWA's built-in Functions)
- Standard gives you custom domains, more bandwidth, etc.

Cost: ~$9/month. Worth it for the linked backend feature alone.

**🎓 Learn More**:
- [SWA Overview](https://learn.microsoft.com/en-us/azure/static-web-apps/overview)
- [Linked backends](https://learn.microsoft.com/en-us/azure/static-web-apps/functions-bring-your-own)

---

#### Azure Functions

**What We Deployed**:
- Python 3.11 on Linux
- Basic (B1) App Service Plan
- System-assigned Managed Identity

**Why Basic (B1) (not Consumption)**:
```bicep
resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  sku: {
    name: 'B1'
    tier: 'Basic'
  }
}
```

Here's where it gets interesting. Linux Consumption Functions with `allowSharedKeyAccess: false` on storage is... problematic. The deployment mechanism needs storage access, and key-less auth on Consumption has edge cases.

Basic (B1):
- ✅ Works cleanly with Managed Identity storage
- ✅ Always running (no cold starts)
- ✅ Reasonable cost (~$13/month)
- ❌ Doesn't scale to zero (fixed cost)

**If you want to save money**: You could switch to Consumption and enable `allowSharedKeyAccess: true` on the Functions storage account. It's less secure but could save ~$13/month.

**The API Endpoints**:
```
GET /api/healthz                          # Health check
GET /api/certifications                   # List available certs
GET /api/episodes/{certId}/{format}       # Get episode list
GET /api/audio/{certId}/{format}/{num}    # Stream audio (proxied from blob)
GET /api/script/{certId}/{format}/{num}   # Get transcript
POST /api/progress/{userId}/{certId}      # Save progress
GET /api/progress/{userId}/{certId}       # Get progress
```

**🎓 Learn More**:
- [Functions hosting options](https://learn.microsoft.com/en-us/azure/azure-functions/functions-scale)
- [Managed Identity with Functions](https://learn.microsoft.com/en-us/azure/azure-functions/functions-identity-access-azure-sql-with-managed-identity)

---

## Content Pipeline

This is where the magic happens. The pipeline transforms Microsoft Learn content into audio episodes.

### Discovery

**File**: [`src/pipeline/tools/deep_discover.py`](../src/pipeline/tools/deep_discover.py)

**What Happens**:
1. Fetch Microsoft Learn Catalog API
2. Find learning paths for the certification
3. Get all modules and units from each path
4. Optionally: Scrape the exam study guide for specific skills

**The Two Content Sources**:

| Source | What It Provides | How We Get It |
|--------|------------------|---------------|
| Learn Catalog API | Educational content (modules, units) | REST API call |
| Exam Study Guide | Testable skills (what's on the exam) | HTML scraping |

**Why Both**:
Learning paths teach concepts. Exam skills define what you'll be tested on. They're complementary, not identical. Many exam skills have NO dedicated learning path content.

**Discovery Strategy (Combined)**:

The platform now always uses the **combined** strategy: learning paths **plus** the exam study guide skills outline. This provides the most complete coverage and avoids surprising gaps.

(You can still run the underlying tools directly for "learning-paths-only" or "skills-only" exploration, but it’s no longer exposed as a workflow/local-run option.)

**Key Code**:
```python
# Getting learning paths from the Catalog API
CATALOG_URL = "https://learn.microsoft.com/api/catalog/"

# Known cert-to-path mappings (more reliable than searching)
CERTIFICATION_PATH_UIDS = {
    "dp-700": [
        "learn.wwl.ingest-data-with-microsoft-fabric",
        "learn.wwl.implement-lakehouse-microsoft-fabric",
        # ...
    ],
}
```

**🎓 Learn More**:
- [Microsoft Learn Catalog API](https://learn.microsoft.com/en-us/training/support/catalog-api)
- [Exam study guides](https://learn.microsoft.com/en-us/credentials/certifications/resources/study-guides/dp-700)

---

### RAG Indexing

**File**: [`src/pipeline/tools/index_content.py`](../src/pipeline/tools/index_content.py)

**What Happens**:
1. Take discovered content (unit text)
2. Chunk it into ~1000-token pieces
3. Generate embeddings for each chunk
4. Upload to Azure AI Search

**Why RAG (Retrieval-Augmented Generation)**:
We don't just dump all the Learn content into the prompt. That would:
- Exceed token limits
- Cost a fortune
- Include irrelevant content

Instead, we index everything, then retrieve only the relevant chunks for each episode topic.

**The Search Index**:
```python
# Index schema (simplified)
fields = [
    SimpleField(name="id", type=SearchFieldDataType.String, key=True),
    SearchableField(name="content", type=SearchFieldDataType.String),
    SearchableField(name="title", type=SearchFieldDataType.String),
    SearchField(
        name="contentVector",
        type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
        vector_search_dimensions=3072,  # text-embedding-3-large
        vector_search_profile_name="default"
    ),
]
```

**Hybrid Search**:
We use both keyword AND vector search:
```python
# In generate_episodes.py
vector_query = VectorizedQuery(
    vector=query_embedding,
    k_nearest_neighbors=10,
)
results = search_client.search(
    search_text=query_text,    # Keyword search
    vector_queries=[vector_query],  # Vector search
    top=5,
)
```

Hybrid search gives you the best of both worlds: exact keyword matches AND semantic similarity.

**🎓 Learn More**:
- [Azure AI Search vector search](https://learn.microsoft.com/en-us/azure/search/vector-search-overview)
- [Hybrid search](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview)

---

### Episode Generation

**File**: [`src/pipeline/tools/generate_episodes.py`](../src/pipeline/tools/generate_episodes.py)

**What Happens (per episode)**:
1. **Retrieve**: Query AI Search for relevant content
2. **Generate**: GPT-4o creates narration script
3. **Convert**: Transform to SSML (Speech Synthesis Markup)
4. **Synthesize**: Azure Speech creates MP3
5. **Upload**: Store in Blob Storage
6. **Save**: Write metadata to Cosmos DB

**The Narration Prompt** (simplified):
```jinja2
{# From src/pipeline/prompts/narration.jinja2 #}
You are creating an educational audio episode about {{ skill_domain }}.

Topics to cover:
{% for topic in skill_topics %}
- {{ topic }}
{% endfor %}

Reference content:
{{ retrieved_content }}

Create a {{ audio_format }} narration that:
- Is approximately 8-10 minutes when spoken
- Explains concepts clearly for someone studying for the exam
- Uses natural, conversational language
```

**SSML Enhancement**:
We don't just send plain text to Speech. We convert to SSML for natural prosody:
```xml
<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis">
  <voice name="en-US-AndrewNeural">
    <prosody rate="0.95" pitch="+0Hz">
      Let's talk about data ingestion in Microsoft Fabric.
      <break time="500ms"/>
      There are several approaches you should know...
    </prosody>
  </voice>
</speak>
```

**Batch Processing**:
Episodes are generated in parallel batches:
```yaml
# Workflow creates a matrix of batch indices
batch_size: 10  # Episodes per batch
# 100 episodes → 10 parallel jobs
```

**🎓 Learn More**:
- [SSML reference](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup)
- [Azure OpenAI prompt engineering](https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/prompt-engineering)

---

## Authentication & Security

### RBAC (Role-Based Access Control)

We use Azure AD and Managed Identity everywhere. No keys, no connection strings.

**The Principal Types**:
1. **Automation Principal** (GitHub Actions) - runs the generation pipeline
2. **Functions Managed Identity** - reads blobs, queries Cosmos
3. **Users** (optional) - access the web player

**Key Role Assignments**:

| Principal | Resource | Role | Why |
|-----------|----------|------|-----|
| Automation | OpenAI | Cognitive Services OpenAI User | Call GPT-4o API |
| Automation | Speech | Cognitive Services Speech User | Synthesize audio |
| Automation | Storage | Storage Blob Data Contributor | Upload episodes |
| Automation | Cosmos | Cosmos DB SQL Data Contributor | Write metadata |
| Automation | Search | Search Index Data Contributor | Create index, upload docs |
| Functions MI | Storage | Storage Blob Data Reader | Stream audio to users |
| Functions MI | Cosmos | Cosmos DB SQL Data Contributor | Read/write episodes & progress |

**Cosmos DB RBAC Gotcha**:
Cosmos has its OWN RBAC system (not the standard Azure RBAC):
```bicep
// This is NOT Microsoft.Authorization/roleAssignments!
resource cosmosDbSqlDataContributorRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  properties: {
    roleDefinitionId: '00000000-0000-0000-0000-000000000002'  // Built-in Data Contributor
    scope: '${cosmosDb.id}/dbs/${cosmosDbDatabaseName}'  // Database-level scope
  }
}
```

**🎓 Learn More**:
- [Azure RBAC overview](https://learn.microsoft.com/en-us/azure/role-based-access-control/overview)
- [Cosmos DB RBAC](https://learn.microsoft.com/en-us/azure/cosmos-db/how-to-setup-rbac)

---

### GitHub OIDC (Federated Identity)

**What It Is**:
Instead of storing Azure credentials in GitHub Secrets, we use OpenID Connect. GitHub proves its identity to Azure AD, which grants temporary tokens.

**Why This Matters**:
- ✅ No long-lived secrets to rotate
- ✅ No credentials that can leak
- ✅ Azure sees exactly which GitHub workflow is calling

**How It's Set Up**:
```yaml
# In workflow
permissions:
  id-token: write  # Allows requesting OIDC token

# Login step
- uses: azure/login@v2
  with:
    client-id: ${{ secrets.AZURE_CLIENT_ID }}
    tenant-id: ${{ secrets.AZURE_TENANT_ID }}
    subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
```

The `AZURE_CLIENT_ID` is an app registration with federated credentials pointing to your GitHub repo.

**🎓 Learn More**:
- [GitHub OIDC with Azure](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure)
- [Configuring federated credentials](https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-azure)

---

## Cost Optimization

### The Big Win: Ephemeral AI Search

**The Problem**: Azure AI Search Basic tier costs ~$75/month. We only need it during content generation (a few hours per certification).

**The Solution**: Deploy Search at the start of generation, delete it at the end.

```yaml
# In generate-content.yml

jobs:
  deploy-search:
    # Creates certaudio-search-ephemeral
    
  # ... generation jobs ...
  
  cleanup-search:
    if: always()  # Runs even if generation fails
    # Deletes certaudio-search-ephemeral
```

**Savings**: ~$75/month → ~$0.50/generation (assuming ~2-4 hours runtime)

**File**: [`infra/modules/search-ephemeral.bicep`](../infra/modules/search-ephemeral.bicep)

---

### Monthly Cost Breakdown (Typical Usage)

| Service | Configuration | Monthly Cost |
|---------|--------------|--------------|
| Cosmos DB | Serverless | ~$2-5 |
| Storage | LRS, ~5GB | ~$0.10 |
| Static Web Apps | Standard | ~$9 |
| Functions | B1 Basic | ~$13* |
| OpenAI | Pay-per-token | ~$0 (no usage) |
| Speech | Pay-per-char | ~$0 (no usage) |
| **AI Search** | **Ephemeral** | **~$0** |
| **Total** | | **~$25-30/month** |

*Functions cost can drop to ~$0 with Consumption plan (see tradeoffs above)

**Per-Generation Cost**:
| Service | Cost per DP-700 generation |
|---------|---------------------------|
| AI Search (2-4 hours) | ~$0.50 |
| OpenAI GPT-4o | ~$15-25 |
| OpenAI Embeddings | ~$0.25 |
| Speech TTS | ~$15-18 |
| **Total** | **~$30-45** |

---

## Workflow Orchestration

### deploy-infra.yml

**Triggers**: Push to `main` affecting `infra/`, `src/web/`, `src/functions/`

**What It Does**:
1. Validates Bicep syntax
2. Deploys/updates all Azure resources
3. Deploys Static Web App content
4. Deploys Functions code

**Key Insight**: This is idempotent. Run it as many times as you want; it only changes what's different.

---

### generate-content.yml

**Triggers**: Manual dispatch only (you pick the certification)

**What It Does**:
1. Deploy ephemeral AI Search
2. Discover content (Learn API + exam skills)
3. Index content for RAG
4. Generate episodes in parallel batches
5. Delete ephemeral AI Search

**The Matrix Strategy**:
```yaml
generate-episodes:
  strategy:
    matrix:
      batchIndex: ${{ fromJson(needs.discover-content.outputs.batchIndices) }}
    max-parallel: 1  # Sequential batches (TTS parallelized within each)
```

Each batch processes ~10 episodes with parallel TTS synthesis (10 concurrent by default).

---

### Alternative: Run Locally from Dev Container

If you're hitting **GitHub Actions limits** (6-hour timeout, free tier minutes), you can run generation directly from your dev container:

```bash
# Run full generation locally (combined discovery strategy)
./scripts/run-local.sh dp-700 instructional
```

**Why Local?**
| Aspect | GitHub Actions | Local |
|--------|---------------|-------|
| **Timeout** | 6 hours max | None |
| **Minutes** | 2000/month free | Unlimited |
| **Auth** | OIDC (5-min tokens) | Azure CLI (persistent) |
| **Complexity** | Batched workflow | Single script |

All the "compute" happens on Azure's side (OpenAI, Speech). Your machine just sends HTTP requests and waits.

**Usage**:
```bash
# Make sure you're logged in to Azure
az login

# Run generation
./scripts/run-local.sh dp-700                           # Defaults: instructional
./scripts/run-local.sh az-104 podcast                   # Podcast format

# Force regenerate existing episodes
FORCE_REGENERATE=true ./scripts/run-local.sh dp-700
```

**What the script does**:
1. Resolves all service endpoints from Azure (OpenAI, Speech, Cosmos, Storage)
2. Creates an ephemeral AI Search service for indexing
3. Runs the full pipeline: discover → index → generate
4. Cleans up the Search service when done (or on error)

The local runner uses `az login` credentials via `DefaultAzureCredential`, which persists for hours/days instead of the 5-minute OIDC tokens used in workflows.

---

## Customization Guide

### "I want to use a different voice"

In the workflow dispatch, pick your voice. Or change the default:

```yaml
# .github/workflows/generate-content.yml
instructionalVoice:
  default: 'en-US-AndrewNeural'  # Change this
```

[Listen to voice samples](https://speech.microsoft.com/portal/voicegallery)

---

### "I want shorter/longer episodes"

In the workflow, change topics per episode:

```yaml
env:
  TOPICS_PER_EPISODE: 5  # Fewer = shorter episodes
```

Or edit the narration prompt in [`src/pipeline/prompts/narration.jinja2`](../src/pipeline/prompts/narration.jinja2).

---

### "I want to add a new certification"

1. Find the learning path UIDs from [Microsoft Learn Catalog](https://learn.microsoft.com/api/catalog/)
2. Add them to [`src/pipeline/tools/deep_discover.py`](../src/pipeline/tools/deep_discover.py):

```python
CERTIFICATION_PATH_UIDS = {
    # ...existing...
    "your-cert": [
        "learn.wwl.path-uid-1",
        "learn.wwl.path-uid-2",
    ],
}
```

3. Add to the workflow dropdown:
```yaml
certificationId:
  options:
    - your-cert  # Add this
```

---

### "I want to save money on Functions"

Switch to Consumption plan. In [`infra/modules/web.bicep`](../infra/modules/web.bicep):

```bicep
resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  sku: {
    name: 'Y1'        // Changed from B1
    tier: 'Dynamic'   // Changed from Basic
  }
}
```

**Warning**: You'll also need to enable `allowSharedKeyAccess: true` on the Functions storage account.

---

### "I want podcast-style two-voice episodes"

Select `podcast` format in the workflow. It uses two voices with dialogue:

```yaml
audioFormat: 'podcast'
podcastHostVoice: 'en-US-GuyNeural'
podcastExpertVoice: 'en-US-TonyNeural'
```

The narration prompt changes to generate a conversation between host and expert.

---

## Troubleshooting

### "Workflow failed during discovery"

1. Check the study guide URL exists: `https://aka.ms/{CERT-ID}-StudyGuide`
2. If it's a new cert, it might not be in the Catalog API yet
3. Try `skills` mode instead of `comprehensive`

### "Rate limits during generation"

The code has retry logic, but if you're generating multiple certs:
- Reduce `max-parallel` in the matrix
- Increase GPT-4o capacity in Azure Portal

### "Audio sounds robotic"

Make sure you're using Neural voices (names end in `Neural`). The SSML conversion adds prosody, but base voice quality matters.

### "Episodes are too short/long"

Adjust the narration prompt's length guidance, or change `TOPICS_PER_EPISODE`.

---

## What's Next?

Once you understand this system, you could:

1. **Add more content sources** - YouTube transcripts, blog posts, etc.
2. **Implement spaced repetition** - Quiz episodes that revisit old content
3. **Add multi-language support** - Azure Speech supports 100+ languages
4. **Create personalized playlists** - Based on weak areas from practice exams
5. **Add B2C authentication** - The infrastructure is ready for it

The foundation is here. Make it yours. 🎧
