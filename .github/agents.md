# GitHub Copilot Agents

This file defines specialized agents for the Azure AI Certification Audio Learning Platform.

## Recent Implementation Notes (Post v1 Plan)

- **Discovery Strategy (Combined)**: Content generation always uses the combined strategy (learning paths **plus** exam skills outline) for full coverage. See [docs/CONTENT_DISCOVERY.md](../docs/CONTENT_DISCOVERY.md) for details.
- **Hierarchy API for URLs**: Unit URLs are fetched from `/api/hierarchy/modules/{uid}` because the catalog API doesn't provide actual URLs, and URL patterns can be non-sequential (e.g., `3b-optimize` instead of `4-optimize`).
- **Voice Selection**: Generate Content workflow allows choosing voices for instructional, podcast host, and podcast expert formats from 11 Azure Neural voices.
- **Episode Resumption**: Episodes that already exist in Cosmos DB are skipped by default. Use `forceRegenerate=true` to regenerate all episodes (e.g., after changing voices).
- **Longer Episodes**: Target episode length is 2,500-3,500 words (~20-25 minutes) for comprehensive coverage.
- **No Markdown in Narration**: Prompts explicitly prohibit markdown to prevent TTS from saying "hashtag" for headers.
- **Keyless Storage (policy-friendly)**: The platform runs with `allowSharedKeyAccess=false` on storage accounts and uses **Managed Identity / Entra ID** + **data-plane RBAC** instead of account keys.
- **Functions hosting**: Azure Functions is deployed on **Basic (B1)** plan (~$13/mo) to support Managed Identity authentication when shared key access is disabled on storage accounts.
- **Cosmos SQL RBAC scope**: Cosmos DB SQL role assignment scope must be the fully-qualified DB scope `${cosmosDb.id}/dbs/${cosmosDbDatabaseName}`.
- **Cosmos RBAC for GitHub OIDC**: The deploy-infra workflow extracts the service principal `oid` from the ARM access token and passes it as `automationPrincipalId` to Bicep, which grants Cosmos SQL Data Contributor at the database scope. The generate-content workflow also idempotently ensures this RBAC exists before running pipeline tools.
- **Search RBAC for GitHub OIDC**: Azure AI Search data-plane operations (create/update indexes, upload documents) require RBAC. Infra grants the automation principal **Search Index Data Contributor** on the Search service, and the generate-content workflow also idempotently ensures this role assignment to prevent `Forbidden` failures during indexing.
- **OIDC Token Refresh**: Long-running generate-content jobs use a background process to periodically re-authenticate via OIDC (every 4 minutes) to prevent token expiry during multi-hour content generation.
- **SWA deploy token**: Static Web Apps deploy token is retrieved at runtime in CI (no long-lived repo secret).
- **Deployment sprawl control**: CI supports an optional pinned suffix secret `AZURE_UNIQUE_SUFFIX` to avoid creating a full new resource set every run.
- **RG cleanup helper**: [scripts/cleanup-rg.sh](../scripts/cleanup-rg.sh) can delete old tagged deployment sets while keeping the active suffix.
- **Dynamic certification list**: Frontend dropdown is populated from the API (`GET /api/certifications`) with a safe fallback that includes `dp-700`.
- **Auto-resolved endpoints**: Generate Content workflow no longer requires endpoint secrets; it resolves them at runtime via [scripts/get-endpoints.sh](../scripts/get-endpoints.sh), which picks the newest (or pinned) deployment suffix.
- **Workflow triggers**: Deploy Infrastructure triggers on `infra/**`, `src/web/**`, `src/functions/**`, or workflow file changes. Content generation is manual via `workflow_dispatch`.
- **Local Development**: Use `./scripts/run-local.sh` to run content generation from the dev container. Uses `az login` credentials and handles ephemeral Search service lifecycle.

## Agents

### content-pipeline

**Scope**: `src/pipeline/**`

**Description**: Handles exam content discovery, RAG-based script generation, and audio synthesis.

**Responsibilities**:
- Discover and scrape Microsoft Learn content from exam skills outline pages
- Index content into Azure AI Search for RAG retrieval
- Generate episode scripts using PromptFlow with GPT-4o
- Convert scripts to SSML with proper prosody for learning retention
- Synthesize audio using Azure AI Speech neural voices
- Track content hashes for delta detection during refresh cycles
- Generate amendment episodes that reference prior content

**Key Files**:
- `src/pipeline/flow.dag.yaml` - PromptFlow orchestration
- `src/pipeline/tools/discover_exam_content.py` - Exam page scraping (skills mode)
- `src/pipeline/tools/deep_discover.py` - Deep discovery via Catalog API
- `src/pipeline/tools/check_content_delta.py` - Content change detection
- `src/pipeline/tools/generate_episodes.py` - Episode generation with retry/skip logic
- `src/pipeline/tools/synthesize_audio.py` - Azure AI Speech synthesis
- `src/pipeline/tools/upload_to_blob.py` - Blob storage upload
- `src/pipeline/prompts/*` - LLM prompt templates

**Context**:
- Uses Azure AI Document Intelligence for content extraction
- Uses Azure AI Search for RAG indexing and retrieval
- Uses Azure OpenAI GPT-4o for script generation
- Uses Azure AI Speech with configurable neural voices (default: `en-US-AndrewNeural`)
- Target episode length: ~20-25 minutes (~2,500-3,500 words)
- SSML includes 500ms pauses after key concepts, -8% rate for comprehension
- Retry with exponential backoff for OpenAI rate limits (429 errors)

**Auth & Access (no keys)**:
- GitHub Actions uses OIDC via `azure/login@v2`; Python tools use `DefaultAzureCredential()`.
- Cosmos access uses Entra ID auth to `CosmosClient(endpoint, DefaultAzureCredential())`.
- Blob access uses `BlobServiceClient(account_url=..., DefaultAzureCredential())`.

---

### frontend

**Scope**: `src/web/**`

**Description**: Audio player web interface with progress tracking.

**Responsibilities**:
- Display episode list grouped by skill domain (exam outline sections)
- Show course-level metrics: total content hours, completed hours, percentage
- Show domain-level metrics: episode count, progress, duration per section
- Collapsible domain sections for navigation
- HTML5 audio player with playback speed control
- Track listening progress (completion, position)
- Sync progress to Cosmos DB (authenticated) or localStorage (anonymous)
- Responsive design for desktop and mobile
- Handle Azure AD B2C authentication when enabled
- Populate certification dropdown dynamically from the backend

**Key Files**:
- `src/web/index.html` - Main application shell
- `src/web/js/app.js` - Application logic (includes player, progress, and auth)
- `src/web/css/styles.css` - Styling
- `src/web/staticwebapp.config.json` - Static Web Apps routing

**Context**:
- Deployed to Azure Static Web Apps
- Calls Azure Functions API for episode data and audio streaming
- Audio served via Functions proxy (no public Blob access)
- B2C authentication optional via feature flag

**Implementation details**:
- Certifications are fetched from `GET /api/certifications` (Cosmos DISTINCT over `episodes`).
- If no content exists yet, the UI still offers a fallback list (includes `dp-700`) and shows “No episodes found”.

---

### infra

**Scope**: `infra/**`, `.github/workflows/**`

**Description**: Infrastructure as Code and CI/CD pipelines.

**Responsibilities**:
- Bicep modules for all Azure resources
- Parameterized deployment for any Microsoft certification
- Conditional B2C deployment via feature flag
- GitHub Actions for infrastructure deployment
- GitHub Actions for content generation pipeline
- GitHub Actions for scheduled content refresh

**Key Files**:
- `infra/main.bicep` - Orchestrator module
- `infra/main.bicepparam` - Parameter file
- `infra/modules/ai-services.bicep` - OpenAI, Speech, Doc Intel, AI Search
- `infra/modules/data.bicep` - Cosmos DB, Storage Account
- `infra/modules/web.bicep` - Static Web Apps, Functions
- `infra/modules/identity.bicep` - B2C (conditional)
- `.github/workflows/deploy-infra.yml` - Infrastructure deployment
- `.github/workflows/generate-content.yml` - Content generation
- `.github/workflows/refresh-content.yml` - Scheduled refresh

**Context**:
- All resources prefer Managed Identity for authentication
- Storage accounts have public access disabled
- Parameters: `certificationId`, `audioFormat`, `enableB2C`, `location`

**Keyless storage + RBAC**:
- Functions runtime storage (`AzureWebJobsStorage`) is configured with `AzureWebJobsStorage__credential=managedidentity` and service URIs, and the Functions identity is granted:
	- Storage Blob Data Contributor
	- Storage Queue Data Contributor
	- Storage Table Data Contributor
- Functions read episode media/scripts from the *content* storage account via Storage Blob Data Reader.

**CI/CD notes**:
- Deploy workflow supports `AZURE_UNIQUE_SUFFIX` (optional secret) to keep one stable environment.
- Static Web Apps deploy token is fetched via `az staticwebapp secrets list` during the workflow.
- Functions deployment packages dependencies into `.python_packages/lib/site-packages` and deploys via zip deploy.

---

### refresh

**Scope**: `src/pipeline/tools/check_content_delta.py`, `.github/workflows/refresh-content.yml`

**Description**: Content update detection and amendment episode generation.

**Responsibilities**:
- Compare current Microsoft Learn content against stored hashes
- Identify which source URLs have changed
- Determine which episodes are affected by changes
- Generate amendment episodes that reference prior content
- Update Cosmos DB with new content hashes and episode references

**Key Files**:
- `src/pipeline/tools/check_content_delta.py` - Delta detection logic
- `.github/workflows/refresh-content.yml` - Scheduled refresh workflow

**Context**:
- Runs on schedule (weekly) or manual trigger
- Amendment episodes get new sequential numbers
- Amendment metadata includes `amendmentOf` field linking to original
- Scripts reference "what we discussed in Episode X" when content changes
