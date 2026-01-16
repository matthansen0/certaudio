# Azure AI Certification Audio Learning Platform

A fully automated Azure-native system that generates podcast-style or instructional audio content from Microsoft Learn documentation for any Microsoft certification exam.

## Features

- 🎧 **Auto-generated audio episodes** from official Microsoft Learn documentation
- 📚 **Any Microsoft certification** - parameterized for AI-102, AZ-204, AZ-104, and more
- 🎙️ **Two formats**: Instructional (single authoritative voice) or Podcast (two-voice dialogue)
- 🔄 **Amendment episodes** when Microsoft updates exam content
- 📊 **Progress tracking** with optional Azure AD B2C authentication
- 🚀 **One-click deployment** with Bicep IaC

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

## Prerequisites

- Azure subscription with permissions to create resources
- GitHub repository with Actions enabled
- Azure CLI installed locally (for initial setup)

## Quick Start

### 1. Fork and Clone

```bash
git clone https://github.com/YOUR_USERNAME/ai102-audio.git
cd ai102-audio
```

### 2. Create Azure Resources

```bash
# Login to Azure
az login

# Create resource group
az group create --name rg-certaudio-dev --location canadacentral

# Create service principal for GitHub Actions
az ad sp create-for-rbac \
  --name "sp-certaudio-github" \
  --role contributor \
  --scopes /subscriptions/YOUR_SUBSCRIPTION_ID/resourceGroups/rg-certaudio-dev \
  --sdk-auth
```

### 3. Configure GitHub Secrets

Add these secrets to your GitHub repository:

| Secret | Description |
|--------|-------------|
| `AZURE_CLIENT_ID` | Service principal client ID |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |
| `AZURE_RESOURCE_GROUP` | Resource group name |

### 4. Deploy Infrastructure

Run the **Deploy Infrastructure** workflow from GitHub Actions, or:

```bash
az deployment group create \
  --resource-group rg-certaudio-dev \
  --template-file infra/main.bicep \
  --parameters certificationId=ai-102 audioFormat=instructional
```

### 5. Generate Content

Run the **Generate Content** workflow to create audio episodes.

## Configuration

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `certificationId` | `ai-102` | Microsoft certification ID |
| `audioFormat` | `instructional` | `instructional` or `podcast` |
| `enableB2C` | `false` | Enable Azure AD B2C authentication |
| `location` | `canadacentral` | Azure region |

### Supported Certifications

- **AI-102**: Azure AI Engineer Associate
- **AZ-204**: Azure Developer Associate
- **AZ-104**: Azure Administrator Associate
- **AZ-900**: Azure Fundamentals
- **AZ-400**: DevOps Engineer Expert
- **AZ-305**: Solutions Architect Expert
- **AZ-500**: Security Engineer Associate
- **DP-900**: Azure Data Fundamentals
- **DP-100**: Azure Data Scientist Associate
- **DP-203**: Azure Data Engineer Associate

## Project Structure

```
├── .github/
│   ├── agents.md              # Copilot agent definitions
│   └── workflows/
│       ├── deploy-infra.yml   # Infrastructure deployment
│       ├── generate-content.yml # Content generation
│       └── refresh-content.yml  # Content refresh
├── infra/
│   ├── main.bicep             # Main orchestrator
│   └── modules/               # Bicep modules
├── src/
│   ├── functions/             # Azure Functions API
│   ├── pipeline/              # PromptFlow content pipeline
│   └── web/                   # Static Web App frontend
└── README.md
```

## Audio Generation

### Instructional Format

- Single voice: `en-US-GuyNeural` with `newscast-casual` style
- Research-backed prosody: -8% rate, 500ms pauses after key concepts
- ~10 minute episodes targeting 1,200-1,500 words

### Podcast Format

- Two voices for natural dialogue:
  - Host: `en-US-GuyNeural` (friendly, conversational)
  - Expert: `en-US-TonyNeural` (authoritative, detailed)
- Back-and-forth Q&A style

## Content Updates

The **Refresh Content** workflow runs weekly to:

1. Check Microsoft Learn pages for content changes
2. Compare content hashes against stored versions
3. Generate amendment episodes for changed content
4. Amendment episodes reference prior content: *"In Episode 5, we discussed X. Microsoft has since updated..."*

## Local Development

### Run the Web App

```bash
cd src/web
python -m http.server 8080
# Open http://localhost:8080
```

### Run the Pipeline

```bash
cd src/pipeline
pip install -r requirements.txt
python -m tools.discover_exam_content --certification-id ai-102
```

## Cost Estimation

Approximate monthly costs (varies by usage):

| Service | Estimated Cost |
|---------|---------------|
| Azure OpenAI (GPT-4o) | $20-50 |
| Azure AI Speech | $5-15 |
| Azure AI Search (Basic) | $70 |
| Azure Cosmos DB (Serverless) | $5-10 |
| Azure Static Web Apps (Standard) | $9 |
| Azure Functions (Consumption) | $0-5 |
| Azure Storage | $1-5 |
| **Total** | **~$110-165/month** |

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

MIT License - see [LICENSE](LICENSE) for details.

## Disclaimer

This project is not affiliated with or endorsed by Microsoft. The generated audio content is based on publicly available Microsoft Learn documentation. Always verify information against official Microsoft documentation before taking certification exams.
