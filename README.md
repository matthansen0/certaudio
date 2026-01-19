# CertAudio - Microsoft Certification Audio Learning Platform

A fully automated Azure-native system that generates podcast-style or instructional audio content from Microsoft Learn documentation for **all 50+ Microsoft certification exams**.

## Features

- 🎧 **Auto-generated audio episodes** from official Microsoft Learn documentation
- 📚 **All Microsoft certifications** - Azure, AI, Data, Security, M365, Power Platform, Dynamics 365
- 🎙️ **Two formats**: Instructional (single authoritative voice) or Podcast (two-voice dialogue)
- 🔄 **Amendment episodes** when Microsoft updates exam content
- 📊 **Progress tracking** with optional Azure AD B2C authentication
- 🚀 **One-click deployment** with Bicep IaC

## Supported Certifications

### Azure
| Exam | Certification |
|------|---------------|
| AZ-900 | Azure Fundamentals |
| AZ-104 | Azure Administrator Associate |
| AZ-204 | Azure Developer Associate |
| AZ-305 | Azure Solutions Architect Expert |
| AZ-400 | DevOps Engineer Expert |
| AZ-500 | Azure Security Engineer Associate |
| AZ-700 | Azure Network Engineer Associate |
| AZ-140 | Azure Virtual Desktop Specialty |
| AZ-800/801 | Windows Server Hybrid Administrator |

### AI & Data
| Exam | Certification |
|------|---------------|
| AI-900 | Azure AI Fundamentals |
| AI-102 | Azure AI Engineer Associate |
| DP-900 | Azure Data Fundamentals |
| DP-100 | Azure Data Scientist Associate |
| DP-203 | Azure Data Engineer Associate |
| DP-300 | Azure Database Administrator Associate |
| DP-600 | Microsoft Fabric Analytics Engineer |
| DP-700 | Microsoft Fabric Data Engineer |

### Security, Compliance & Identity
| Exam | Certification |
|------|---------------|
| SC-900 | Security, Compliance, Identity Fundamentals |
| SC-100 | Cybersecurity Architect Expert |
| SC-200 | Security Operations Analyst Associate |
| SC-300 | Identity and Access Administrator Associate |
| SC-400 | Information Protection Administrator |

### Microsoft 365
| Exam | Certification |
|------|---------------|
| MS-900 | Microsoft 365 Fundamentals |
| MS-102 | Microsoft 365 Administrator |
| MS-700 | Microsoft Teams Administrator |
| MD-102 | Endpoint Administrator |

### Power Platform
| Exam | Certification |
|------|---------------|
| PL-900 | Power Platform Fundamentals |
| PL-100 | Power Platform App Maker |
| PL-200 | Power Platform Functional Consultant |
| PL-300 | Power BI Data Analyst Associate |
| PL-400 | Power Platform Developer |
| PL-500 | Power Automate RPA Developer |
| PL-600 | Power Platform Solution Architect Expert |

### Dynamics 365
| Exam | Certification |
|------|---------------|
| MB-910 | Dynamics 365 Fundamentals (CRM) |
| MB-920 | Dynamics 365 Fundamentals (ERP) |
| MB-210 | Dynamics 365 Sales Functional Consultant |
| MB-220 | Dynamics 365 Customer Insights - Journeys |
| MB-230 | Dynamics 365 Customer Service |
| MB-240 | Dynamics 365 Field Service |
| MB-260 | Dynamics 365 Customer Insights - Data |
| MB-300 | Dynamics 365 Core Finance and Operations |
| MB-310 | Dynamics 365 Finance Functional Consultant |
| MB-330 | Dynamics 365 Supply Chain Management |
| MB-335 | Dynamics 365 Supply Chain Management Expert |
| MB-500 | Dynamics 365 Finance & Operations Developer |
| MB-700 | Dynamics 365 Finance & Operations Solution Architect |
| MB-800 | Dynamics 365 Business Central Functional Consultant |
| MB-820 | Dynamics 365 Business Central Developer |

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
git clone https://github.com/YOUR_USERNAME/certaudio.git
cd certaudio
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
| `certificationId` | `ai-102` | Microsoft certification ID (see supported list above) |
| `audioFormat` | `instructional` | `instructional` or `podcast` |
| `discoveryMode` | `comprehensive` | `skills`, `deep`, or `comprehensive` (recommended - full coverage) |
| `instructionalVoice` | `en-US-AndrewNeural` | Voice for instructional format |
| `podcastHostVoice` | `en-US-BrianNeural` | Host voice for podcast format |
| `podcastExpertVoice` | `en-US-AvaNeural` | Expert voice for podcast format |
| `forceRegenerate` | `false` | Regenerate episodes that already exist |
| `enableB2C` | `false` | Enable Azure AD B2C authentication |
| `location` | `canadacentral` | Azure region |

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

### Discovery Modes

- **Skills Mode**: Scrapes exam skills outline page for topics (~2-3 hours)
- **Deep Mode**: Uses Microsoft Learn Catalog API to discover learning paths, modules, and units (~5-7 hours for DP-700)
- **Comprehensive Mode** (recommended): Combines BOTH learning paths AND exam skills outline for full official coverage (~10-12 hours for DP-700). See [docs/CONTENT_DISCOVERY.md](docs/CONTENT_DISCOVERY.md) for details.

### Instructional Format

- Single voice: Configurable (default `en-US-AndrewNeural`)
- Research-backed prosody: -8% rate, 500ms pauses after key concepts
- ~20-25 minute episodes targeting 2,500-3,500 words

### Podcast Format

- Two voices for natural dialogue:
  - Host: Configurable (default `en-US-BrianNeural`) - friendly, conversational
  - Expert: Configurable (default `en-US-AvaNeural`) - authoritative, detailed
- Back-and-forth Q&A style

### Voice Options

Available voices: `en-US-AndrewNeural`, `en-US-BrianNeural`, `en-US-AvaNeural`, `en-US-EmmaNeural`, `en-US-JennyNeural`, `en-US-GuyNeural`, `en-US-AriaNeural`, `en-US-DavisNeural`, `en-US-TonyNeural`, `en-US-SaraNeural`, `en-US-JaneNeural`

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
