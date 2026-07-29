# GitHub Copilot Agents

This file defines specialized agents for the Azure AI Certification Audio Learning Platform.

## Recent Implementation Notes (Post v1 Plan)

- **Study Partner (AI Foundry Agent)**: Optional feature that deploys Azure AI Foundry with a GPT-4o agent for interactive exam prep chat. Enable with `azd env set ENABLE_STUDY_PARTNER true`. Adds ~$5-10/month; AI Search is *not* part of this toggle because generation needs it too. See [Study Partner section in README](../README.md#study-partner-optional).
- **Discovery Strategy (Combined)**: Content generation always uses the combined strategy (learning paths **plus** exam skills outline) for full coverage. See [docs/CONTENT_DISCOVERY.md](../docs/CONTENT_DISCOVERY.md) for details.
- **Dynamic Learning Path Resolution**: Learning paths are resolved dynamically via catalog role + product tag filtering (`CERTIFICATION_ROLE_PRODUCTS` mapping) instead of hardcoded UIDs. Hardcoded UIDs are kept as a fallback but stale entries are auto-detected and skipped. This prevents silent content loss when Microsoft renames or restructures learning paths.
- **Coverage Sweep**: In comprehensive mode, every exam skill topic is checked against discovered content. Uncovered topics go through a fallback chain: catalog module description matching → Learn search API → explicit gap reporting. Supplemental URLs from the sweep are merged into the indexing pipeline.
- **Confidence Score**: Discovery outputs a weighted confidence score (0–100%, Grade A–F) showing content coverage completeness. Weights: learning-path=1.0, catalog-supplement=0.8, search-supplement=0.5, gap=0.0. Surfaced in generation output via `--discovery-json` flag.
- **Hierarchy API for URLs**: Unit URLs are fetched from `/api/hierarchy/modules/{uid}` because the catalog API doesn't provide actual URLs, and URL patterns can be non-sequential (e.g., `3b-optimize` instead of `4-optimize`).
- **Voice Selection**: Voices for instructional, podcast host, and podcast expert formats are chosen when submitting a job in the admin portal, validated against `VOICE_NAME_RE` in `src/functions/admin.py`, and defaulted from app settings.
- **Dragon HD Voices**: Azure Speech Dragon HD voices (e.g., `en-US-Andrew:DragonHDLatestNeural`) are only available in **eastus**, **westeurope**, and **southeastasia** regions. The Speech service is deployed to eastus to enable HD voice support.
- **Dragon HD SSML Compatibility**: Dragon HD voices produce **audio clicking/popping artifacts** when SSML contains `<prosody rate>` wrappers with `<break>` tags inside. The `generate_episodes.py` tool detects Dragon HD voices (by `:DragonHDLatestNeural` suffix) and generates simplified SSML without rate adjustments or break elements. Standard Neural voices work fine with the full SSML features.
- **Episode Resumption**: Episodes that already exist in Cosmos DB are skipped by default. Use `forceRegenerate=true` to regenerate all episodes (e.g., after changing voices).
- **Longer Episodes**: Target episode length is 2,500-3,500 words (~20-25 minutes) for comprehensive coverage.
- **No Markdown in Narration**: Prompts explicitly prohibit markdown to prevent TTS from saying "hashtag" for headers.
- **Keyless Storage (policy-friendly)**: The platform runs with `allowSharedKeyAccess=false` on storage accounts and uses **Managed Identity / Entra ID** + **data-plane RBAC** instead of account keys.
- **Functions hosting**: Azure Functions is deployed on **Basic (B1)** plan (~$13/mo) with **Always On enabled** to prevent cold starts. Required for Managed Identity authentication when shared key access is disabled.
- **Functions performance**: Credentials (`DefaultAzureCredential`), blob clients, and user delegation keys are cached at module level to avoid re-auth on every request. First request after cold start fetches delegation key (~3s), subsequent requests are <1s.
- **Audio delivery**: Blob Storage is private (no public access, no shared keys), so SAS URLs are not usable. `get_audio` in `function_app.py` **proxies** the bytes through the Function using Managed Identity, honouring `Range` requests with `206`/`416` so seeking works. There is no `Storage Blob Delegator` role and no redirect.
- **CSP for audio**: `media-src 'self' blob:` — audio is same-origin because it is proxied. Do not re-add `*.blob.core.windows.net`.
- **SWA built-in auth (Microsoft only)**: User sign-in uses SWA's built-in Microsoft (AAD) provider. Zero app registrations, zero cost. The `x-ms-client-principal` header is decoded by `_get_swa_user()` in Functions. GitHub, Twitter, and Google providers are blocked via `staticwebapp.config.json` route rules. Progress is synced to Cosmos DB `userProgress` container keyed by stable SWA `userId`.
- **Cross-device progress sync**: On sign-in, the frontend fetches server progress, merges with localStorage (keeping `completed=True` and `max(position)`), and pushes the merged result back. Subsequent saves go to both localStorage and the authenticated `/api/me/progress/{certId}` endpoint.
- **Auth unit tests**: `src/functions/test_auth.py` covers `_get_swa_user` header parsing (valid, missing, malformed), `/api/me` identity endpoint, and progress GET/POST endpoints (401 for unauthenticated, single update, bulk merge, validation).
- **EasyAuth is the security control — do not disable it**: SWA-managed EasyAuth is what stops a caller reaching the Function hostname directly and forging `x-ms-client-principal`. An earlier version of the deployment disabled it to ease debugging. A direct call returning `401` is correct behaviour, not a bug. Test through the SWA hostname.
- **Linking a backend is not enough to protect it**: `az staticwebapp backends link` registers the `azureStaticWebApps` identity provider and sets `platform.enabled=true`, but leaves `globalValidation.requireAuthentication=false`. EasyAuth is then present but *not enforcing*, and the Function hostname still answers anonymous requests. `scripts/postprovision.sh` explicitly PUTs `requireAuthentication=true` and `unauthenticatedClientAction=Return401` after linking. **The auth module only picks this up on restart**, so the hook restarts the app and then asserts the direct hostname returns 401.
- **Disabling EasyAuth also wipes the provider registration**: it clears `identityProviders.azureStaticWebApps`, and setting `platform.enabled=true` afterwards is not enough — auth would be on with nothing that trusts SWA, locking out the site. Re-linking (unlink then link) is the only supported repair, which is why the hook checks the provider registration before enforcing.
- **Cosmos SQL RBAC scope**: Cosmos DB SQL role assignment scope must be the fully-qualified DB scope `${cosmosDb.id}/dbs/${cosmosDbDatabaseName}`.
- **RBAC is declarative**: every role the Function's managed identity needs is in `infra/modules/rbac.bicep`, deployed after the web module so it can consume `functionsAppPrincipalId`. Assignment names are `guid(scope, principalId, roleId)`, so redeploys are no-ops. Do not move role assignments into scripts.
- **principalType matters**: role assignments for the *deploying* principal must pass `principalType` from `AZURE_PRINCIPAL_TYPE` (default `User`). Hardcoding `ServicePrincipal` fails with `UnmatchedPrincipalType`, because `azd` deploys as the signed-in human.
- **Search RBAC**: Azure AI Search data-plane operations (create/update indexes, upload documents) require RBAC. The Function identity holds **Search Index Data Contributor** because it both writes the grounding index during generation and reads it for Study Partner RAG.
- **Shared search index**: One Basic Search service holds a single `certification-content` index for every certification, discriminated by a filterable `certificationId` field. There is no per-certification index and no ephemeral Search service — both were removed.
- **SWA deploy token**: Static Web Apps deploy token is retrieved at runtime in CI (no long-lived repo secret).
- **Deployment sprawl control**: CI supports an optional pinned suffix secret `AZURE_UNIQUE_SUFFIX` to avoid creating a full new resource set every run.
- **Dynamic certification list**: Frontend dropdown is populated from the API (`GET /api/certifications`) with a safe fallback that includes `dp-700`.
- **Content generation runs in the Function App, not CI**: Storage and Cosmos sit behind Private Link with `publicNetworkAccess=Disabled`, which Azure Policy enforces in many enterprise tenants, so a GitHub-hosted runner has no route to the data plane. Generation runs in-process in the already VNet-integrated Function App. An admin submits a job at `/admin.html` → `POST /api/portal/jobs` → message on the `content-jobs` queue → `run_content_job` queue trigger → `src/functions/pipeline/orchestrator.py`. Progress is written back to the job document and polled by the UI.
- **No workflows at all**: deployment is `azd up`, defined by `azure.yaml`. The `deploy-infra.yml`, `generate-content.yml`, `refresh-content.yml`, and `private-pipeline.yml` workflows, and the `run-local.sh`, `index-content.sh`, `get-endpoints.sh`, and WebJob helpers, were all deleted. Do not reintroduce them. This is a public template: a fork must deploy with no GitHub setup, no app registration, and no secrets.
- **azd file precedence**: `azd` prefers `infra/main.bicepparam` over `infra/main.parameters.json`, and resolves `module: main` to a compiled `infra/main.json` over `main.bicep`. Both were deleted and `main.json` is gitignored, because a stale copy silently deploys the wrong template. `infra/main.bicep` is **subscription-scoped** and creates the resource group itself.
- **Service tags**: `azd` maps a service in `azure.yaml` to a resource by an `azd-service-name` tag (`api` on the Function App, `web` on the Static Web App). A Static Web Apps service also needs a `package.json` even with no build step.
- **Admin bootstrap**: Bicep writes a rotating `ADMIN_BOOTSTRAP_TOKEN` app setting. The first admin claims it once at `/admin.html`; afterwards admins are managed in the portal. Compared with `hmac.compare_digest`, and the claim marker is written before the admin record so a partial failure spends the token rather than leaving it reusable.
- **One job at a time**: `host.json` sets `functionTimeout: -1` (allowed on a dedicated plan) and queue `batchSize: 1`, `newBatchThreshold: 0`, `maxDequeueCount: 2`. `POST /api/portal/jobs` also returns `409` if a job is already queued or running.
- **Input validation**: `certificationId` is interpolated into an AI Search OData filter, so it is constrained to `^[a-z0-9][a-z0-9-]{0,63}$` at the API boundary and quotes are additionally escaped at the filter. Voice names are constrained to the Azure short-name shape.
- **Local Development**: Content generation cannot run locally — the data plane is private and tenant policy silently reverts firewall changes. Run `python -m pytest -q` in `src/functions` for unit tests, and generate from the admin portal.
- **Private networking is mandatory**: Azure Policy in this tenant uses `Modify` effects to force `publicNetworkAccess=Disabled`, `allowSharedKeyAccess=false`, and `disableLocalAuth=true` on Storage and Cosmos. ARM returns HTTP 200 while silently rewriting the payload, so a template asking for public access appears to succeed and simply does not take effect. Do not try to "fix" connectivity by enabling public access — check the effective policy for the target subscription instead.

## Agents

### study-partner

**Scope**: `src/functions/function_app.py` (chat endpoint), `infra/modules/ai-foundry.bicep`

**Description**: AI-powered conversational study partner using Azure AI Foundry Agent Service.

**Responsibilities**:
- Provide interactive Q&A for certification exam preparation
- Use RAG (Retrieval-Augmented Generation) to ground answers in indexed exam content
- Create and manage AI agents with Azure AI Foundry SDK
- Handle conversation threads for contextual follow-up questions
- Gracefully fall back to direct OpenAI+RAG if Foundry unavailable

**Key Files**:
- `src/functions/function_app.py` - Chat API endpoint (`/api/chat`)
- `infra/modules/ai-foundry.bicep` - AI Foundry infrastructure
- `src/web/js/study.js` - Study Partner frontend UI

**Context**:
- Deployed conditionally via `enableStudyPartner=true` parameter
- Uses Azure AI Foundry's Standard Agent Setup (no BYO storage)
- Agent has an Azure AI Search tool configured for RAG retrieval
- GPT-4o model (GlobalStandard, 30K TPM) deployed to the Foundry account
- Connections to CosmosDB, Storage, and AI Search for agent capabilities

**Agent Configuration**:
- Model: `gpt-4o` (version 2024-11-20)
- Instructions: Certification exam prep specialist with concise, accurate answers
- Tool: Azure AI Search for retrieving relevant exam content
- Threads: Managed per-conversation for context retention

**Auth & Access**:
- Functions app uses Managed Identity to authenticate to AI Foundry
- AI Foundry Project has its own Managed Identity for resource access
- Function identity granted `Cognitive Services User` on Foundry account
- Project identity granted roles on CosmosDB, Storage, and AI Search

---

### content-pipeline

**Scope**: `src/functions/pipeline/**`, `src/functions/admin.py`

**Description**: Handles exam content discovery, RAG-based script generation, and audio synthesis. Runs in-process inside the Function App, not as a separate job.

**Responsibilities**:
- Discover and scrape Microsoft Learn content from exam skills outline pages
- Index content into the shared Azure AI Search index for RAG retrieval
- Generate episode scripts with GPT-4o
- Convert scripts to SSML with proper prosody for learning retention
- Synthesize audio using Azure AI Speech neural voices
- Track content hashes for delta detection during refresh cycles
- Generate amendment episodes that reference prior content
- Report progress back to the job document so the admin UI can poll it

**Key Files**:
- `src/functions/pipeline/orchestrator.py` - `run_generate` / `run_refresh`, the in-process entry points
- `src/functions/admin.py` - admin API and the `run_content_job` queue trigger
- `src/functions/pipeline/discover_exam_content.py` - Exam page scraping (skills mode)
- `src/functions/pipeline/deep_discover.py` - Deep discovery via Catalog API (dynamic path resolution, coverage sweep, confidence scoring)
- `src/functions/pipeline/check_content_delta.py` - Content change detection
- `src/functions/pipeline/generate_episodes.py` - Episode generation with retry/skip logic
- `src/functions/pipeline/synthesize_audio.py` - Azure AI Speech synthesis
- `src/functions/pipeline/upload_to_blob.py` - Blob storage upload
- `src/functions/pipeline/prompts/*` - LLM prompt templates

**Context**:
- PromptFlow was removed. `flow.dag.yaml` never actually executed; the tools are plain Python called by `orchestrator.py`.
- Every module here is packaged into the Function App, so keep `requirements.in` lean and recompile the lock after changes.
- Target episode length: ~20-25 minutes (~2,500-3,500 words)
- SSML for standard Neural voices: 500ms breaks after key concepts, -5% rate for comprehension
- SSML for Dragon HD voices: simplified (no prosody rate, no breaks) to avoid audio artifacts
- Retry with exponential backoff for OpenAI rate limits (429 errors)

**Auth & Access (no keys)**:
- Everything uses `DefaultAzureCredential()`: the managed identity at runtime, and the deployer's `az login` for `azd`.
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
- Handle SWA built-in authentication (Microsoft provider)
- Merge server + local progress on sign-in for cross-device sync
- Populate certification dropdown dynamically from the backend

**Key Files**:
- `src/web/index.html` - Main application shell
- `src/web/js/app.js` - Application logic (includes player, progress, auth)
- `src/web/css/styles.css` - Styling
- `src/web/staticwebapp.config.json` - Static Web Apps routing and auth config
- `src/functions/test_auth.py` - Unit tests for auth helpers and progress endpoints

**Context**:
- Deployed to Azure Static Web Apps
- Calls Azure Functions API for episode data and audio streaming
- Audio served via Functions proxy (no public Blob access)
- Authentication via SWA built-in Microsoft (AAD) provider (zero setup, free)
- GitHub, Twitter, and Google providers blocked via route config

**Authentication flow**:
- Sign-in redirects to `/.auth/login/aad` (SWA built-in Microsoft OAuth)
- SWA injects `x-ms-client-principal` header to linked Functions backend
- Backend decodes header via `_get_swa_user()` for stable userId
- Authenticated progress stored in Cosmos `userProgress` container
- Cross-device sync: on sign-in, merges server + localStorage progress (keeps most-complete state)

**Implementation details**:
- Certifications are fetched from `GET /api/certifications` (Cosmos DISTINCT over `episodes`).
- If no content exists yet, the UI still offers a fallback list (includes `dp-700`) and shows “No episodes found”.

---

### infra

**Scope**: `infra/**`, `azure.yaml`, `scripts/postprovision.sh`

**Description**: Infrastructure as Code and the `azd` deployment definition.

**Responsibilities**:
- Bicep modules for all Azure resources, at subscription scope
- Declarative role assignments in `rbac.bicep`
- Conditional B2C and AI Foundry deployment via feature flags
- The postprovision hook: SWA backend link and EasyAuth enforcement

**Key Files**:
- `infra/main.bicep` - Orchestrator module
- `infra/main.parameters.json` - binds azd environment variables to Bicep parameters
- `infra/modules/ai-services.bicep` - OpenAI, Speech, Doc Intel, AI Search
- `infra/modules/ai-foundry.bicep` - AI Foundry account, project, connections (conditional)
- `infra/modules/data.bicep` - Cosmos DB, Storage Account (incl. the `admins` and `jobs` containers)
- `infra/modules/web.bicep` - Static Web Apps, Functions
- `infra/modules/search-persistent.bicep` - AI Search (always deployed)
- `infra/modules/identity.bicep` - B2C (conditional)
- `infra/modules/rbac.bicep` - Function managed identity data-plane roles
- `azure.yaml` - azd project: infra, services, hooks
- `scripts/postprovision.sh` - SWA backend link and EasyAuth enforcement

**Context**:
- All resources prefer Managed Identity for authentication
- Storage and Cosmos have public network access disabled and are reached over Private Link. Azure Policy enforces this in many enterprise tenants; check the effective policy before assuming it can be changed.
- Parameters: `environmentName`, `location`, `principalId`, `principalType`, `uniqueSuffix`, `enableStudyPartner`, `foundryLocation`, `adminBootstrapToken`
- Cosmos containers must be declared in Bicep. The Cosmos SQL Data Contributor role covers item operations but *not* container management, so the app cannot create them at runtime.

**Keyless storage + RBAC**:
- Functions runtime storage (`AzureWebJobsStorage`) is configured with `AzureWebJobsStorage__credential=managedidentity` and service URIs, and the Functions identity is granted:
	- Storage Blob Data Contributor
	- Storage Queue Data Contributor
	- Storage Table Data Contributor
- On the *content* storage account the Functions identity holds Storage Blob Data **Contributor** (it writes generated episodes as well as streaming them).

**Deployment notes**:
- `azd env set AZURE_UNIQUE_SUFFIX <value>` pins resource names to adopt an existing environment. Left unset, names derive deterministically from the subscription and environment name.
- `azd config set auth.useAzCliAuth true` makes azd reuse the `az login` credential instead of prompting for its own.
- Adopting pre-existing resources can fail with `RoleAssignmentExists` when an assignment already exists under a randomly generated name; delete it and let Bicep own the deterministic one.

---

### refresh

**Scope**: `src/functions/pipeline/check_content_delta.py`, `src/functions/pipeline/orchestrator.py`

**Description**: Content update detection and amendment episode generation.

**Responsibilities**:
- Compare current Microsoft Learn content against stored hashes
- Identify which source URLs have changed
- Determine which episodes are affected by changes
- Generate amendment episodes that reference prior content
- Update Cosmos DB with new content hashes and episode references

**Key Files**:
- `src/functions/pipeline/check_content_delta.py` - Delta detection logic
- `src/functions/pipeline/orchestrator.py` - `run_refresh`, which re-indexes changed sources and regenerates only the affected batches

**Context**:
- Triggered on demand by an admin submitting a `refresh` job; there is no schedule
- Amendment episodes get new sequential numbers
- Amendment metadata includes `amendmentOf` field linking to original
- Scripts reference "what we discussed in Episode X" when content changes
