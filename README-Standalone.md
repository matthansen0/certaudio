# AI-102 Audio Course Generator (AI Foundry UI-First)

A **UI-first approach** to building the AI-102 audio training course generator using **Azure AI Foundry's graphical interface** for maximum ease of use. This guide prioritizes using AI Foundry's visual tools, drag-and-drop builders, and point-and-click configuration over custom coding.

---

## 🧩 Overview

This **AI Foundry UI-centric** pipeline leverages the visual interface for most operations:

1. **Course Outline Creation** - Using AI Foundry's Chat Playground with saved prompts
2. **Prompt Management** - Visual Prompt Asset creation and version control
3. **Content Generation** - Batch processing through AI Foundry's UI tools
4. **Quality Assessment** - Visual evaluation dashboards and automated scoring
5. **Content Safety** - Point-and-click safety configuration and monitoring
6. **Audio Synthesis** - UI-driven Speech Service integration
7. **Asset Management** - Visual file management and organization

**Key Advantages of UI-First Approach:**
- ✅ **No Coding Required** - Everything configurable through web interface
- ✅ **Visual Prompt Builder** - Drag-and-drop prompt creation and testing
- ✅ **Point-and-Click Connections** - GUI-based service integration
- ✅ **Dashboard Monitoring** - Real-time visual progress tracking  
- ✅ **Built-in Templates** - Pre-configured workflows for common tasks
- ✅ **Team Collaboration** - Share and collaborate through web interface
- ✅ **Minimal Learning Curve** - Intuitive graphical workflows

---

## 🧱 Prerequisites

### 1️⃣ Azure AI Foundry Access

- **Azure Subscription** with AI Foundry access
- **Web Browser** (Chrome, Edge, or Firefox recommended)
- **Azure AI Foundry Portal** access at [ai.azure.com](https://ai.azure.com)

### 2️⃣ Azure Services (Created via UI)

- **Azure AI Foundry Hub** (created through the portal)
- **Azure AI Foundry Project** (created through the portal)
- **Azure OpenAI Service** with GPT-4o or GPT-4 Turbo deployment
- **Azure AI Speech Service** 
- **Azure Storage Account**

### 3️⃣ Permissions (Assigned via Azure Portal)

- **AI Developer** role on the AI Foundry project
- **Cognitive Services User** on OpenAI and Speech services
- **Storage Blob Data Contributor** on Storage Account

**No coding experience required** - Everything will be done through web interfaces!

---

## 🎯 Complete UI-Based Setup Guide

### 1️⃣ Create AI Foundry Hub & Project (Web UI)

**Step 1: Navigate to AI Foundry Portal**
1. Go to [https://ai.azure.com](https://ai.azure.com)
2. Sign in with your Azure account
3. Click **"+ New Hub"** in the left sidebar

**Step 2: Create AI Foundry Hub**
1. **Hub name**: `ai102-hub`
2. **Subscription**: Select your Azure subscription
3. **Resource group**: Create new `rg-ai102` or select existing
4. **Location**: Choose your preferred region (e.g., East US)
5. Click **"Create"** and wait for deployment

**Step 3: Create AI Foundry Project**
1. In your new hub, click **"+ New Project"**
2. **Project name**: `ai102-course-generator`
3. **Description**: AI-102 Audio Course Generation Pipeline
4. Click **"Create"**

### 2️⃣ Set Up Service Connections (Web UI)

**Step 1: Azure OpenAI Connection**
1. In your project, navigate to **Settings → Connections**
2. Click **"+ New Connection"**
3. Select **"Azure OpenAI"**
4. **Connection name**: `azure_openai_connection`
5. **Azure OpenAI resource**: Select your OpenAI service
6. **API version**: Use latest (2024-02-01 or newer)
7. Click **"Create"**

**Step 2: Azure AI Speech Connection**
1. Click **"+ New Connection"** again
2. Select **"Azure AI Services"**
3. **Connection name**: `speech_service_connection`
4. **Azure AI Services resource**: Select your Speech service
5. Click **"Create"**

**Step 3: Azure Storage Connection**
1. Click **"+ New Connection"** again  
2. Select **"Azure Blob Storage"**
3. **Connection name**: `storage_connection`
4. **Storage account**: Select your storage account
5. **Container name**: `ai102-audio` (will be created automatically)
6. Click **"Create"**

### 3️⃣ Configure Model Deployments (Web UI)

**Step 1: Navigate to Model Deployments**
1. In your AI Foundry project, go to **Models → Deployments**
2. Click **"+ Create Deployment"**

**Step 2: Deploy GPT-4o Model**
1. **Model**: Select `gpt-4o` or `gpt-4-turbo`
2. **Deployment name**: `gpt-4o-deployment`
3. **Version**: Use latest available
4. **Tokens per minute rate limit**: 50K (adjust based on needs)
5. Click **"Deploy"**

**No installation or coding required** - everything is configured through the web interface!

---

## 🚀 UI-First Course Generation Workflow

### Step 1: Create Prompt Assets (Web UI)

**Navigate to Prompt Flow → Prompt Assets**

1. Click **"+ New Prompt Asset"**
2. **Name**: `course_outline_prompt`
3. **Content**: 
   ```
   You are a senior Microsoft Azure instructor.
   Create a {{episodes_count}}-episode plan for "AI-102 Azure AI Engineer Associate" certification.
   Format as JSON with: number, slug, title, outcomes[], services[], links[], exam_tips[].
   ```
4. **Save** and **Publish** as version 1.0

Repeat for other prompts:
- `narration_prompt` - Episode content generation
- `quality_check_prompt` - Content review and revision
- `ssml_conversion_prompt` - SSML formatting

### Step 2: Use Chat Playground (Web UI)

**Navigate to Playground → Chat**

1. **System Prompt**: Select your `course_outline_prompt` asset
2. **User Message**: "Create 35 episodes for AI-102 certification"  
3. **Parameters**:
   - **Temperature**: 0.3
   - **Max tokens**: 4000
   - **Model**: Your deployed `gpt-4o-deployment`
4. Click **"Run"** to generate course outline
5. **Save Response** to your workspace files

### Step 3: Batch Content Generation (Web UI)

**Navigate to Playground → Batch**

1. **Upload CSV** with episode data from Step 2
2. **Select Model**: `gpt-4o-deployment`  
3. **Prompt Template**: Select `narration_prompt` asset
4. **Configure Mapping**: 
   - `episode_data` → CSV column
   - `voice_preference` → "en-US-GuyNeural"
5. **Start Batch Job** - processes all episodes automatically
6. **Monitor Progress** in the Jobs tab
7. **Download Results** when complete

### Step 4: Content Safety Check (Web UI)

**Navigate to Content Safety → Analyze**

1. **Upload Batch Results** from Step 3
2. **Select Categories**: 
   - ✅ Hate speech
   - ✅ Violence  
   - ✅ Sexual content
   - ✅ Self-harm
3. **Run Analysis** - automatic flagging and scoring
4. **Review Flagged Content** and make revisions
5. **Re-run Analysis** after changes

### Step 5: Audio Generation (Web UI)

**Navigate to Speech → Speech Studio**

1. **Upload SSML Files** from content generation
2. **Select Voice**: `en-US-GuyNeural`
3. **Audio Format**: MP3, 16kHz
4. **Batch Synthesize** all episodes
5. **Preview Audio** samples for quality
6. **Download Audio Files** when complete

---

## 📁 AI Foundry Workspace Organization

Everything is managed through the **AI Foundry web interface** - no local files needed!

### AI Foundry Project Structure

```
📦 AI Foundry Project: ai102-course-generator
├─ 🔗 Connections/
│  ├─ azure_openai_connection      # GPT-4o deployment
│  ├─ speech_service_connection    # Azure Speech Service
│  └─ storage_connection           # Blob storage for outputs
├─ 💬 Prompt Assets/
│  ├─ course_outline_prompt v1.0   # Course planning system
│  ├─ narration_prompt v1.0        # Episode content generation
│  ├─ quality_check_prompt v1.0    # Content review & revision
│  └─ ssml_conversion_prompt v1.0  # Audio-ready formatting
├─ 🧪 Playground/
│  ├─ Chat Sessions                # Interactive testing
│  ├─ Batch Jobs                   # Bulk content generation
│  └─ Completions History          # All generation attempts
├─ 📊 Evaluation/
│  ├─ Content Quality Metrics      # Automated scoring
│  ├─ Safety Analysis Results      # Content filtering
│  └─ Performance Dashboards       # Token usage & costs
├─ 🛡️ Content Safety/
│  ├─ Safety Filters              # Automated content checks
│  ├─ Custom Categories           # AI-102 specific rules
│  └─ Violation Reports           # Flagged content review
├─ 💾 Data/
│  ├─ Course Outline (JSON)       # Generated episode structure
│  ├─ Episode Narrations (MD)     # Formatted text content
│  ├─ SSML Files                  # Speech-ready markup
│  └─ Audio Files (MP3)           # Final synthesized audio
└─ 📈 Monitoring/
   ├─ Job History                 # All batch operations
   ├─ Cost Tracking               # Token & service usage
   └─ Performance Metrics         # Speed & quality stats
```

### Access Your Work

**All outputs are accessible via AI Foundry:**
- **Data Explorer**: Browse all generated files
- **Jobs Dashboard**: Monitor batch processing  
- **Evaluation Reports**: Quality assessment results
- **Cost Management**: Track spending across services
- **Download Portal**: Export files for external use

---

## 🛠️ AI Foundry UI Workflows

### Course Outline Generation (Chat Playground)

**Navigate to: Playground → Chat**

1. **Select Model**: `gpt-4o-deployment`
2. **System Message**: Load `course_outline_prompt` asset
3. **User Message**: 
   ```
   Create a 35-episode AI-102 certification course optimized for audio learning.
   Include practical examples and exam tips for each episode.
   ```
4. **Parameters**:
   - Temperature: `0.3`
   - Max tokens: `4000`
   - Top P: `1.0`
5. **Run** and **Save to Files**

### Batch Episode Generation (Batch Playground)

**Navigate to: Playground → Batch**

1. **Upload CSV** with episode data:
   ```csv
   episode_number,title,learning_objectives,exam_topics
   1,"Introduction to AI-102","Understand certification scope","Exam structure and requirements"
   2,"Azure Cognitive Services Overview","Service categories","API fundamentals"
   ...
   ```
2. **Prompt Template**: Select `narration_prompt` asset
3. **Configure Variables**: Map CSV columns to prompt variables
4. **Batch Settings**:
   - Model: `gpt-4o-deployment`
   - Temperature: `0.4`
   - Max tokens: `2000`
5. **Start Job** - AI Foundry processes all episodes
6. **Monitor Progress** in Jobs tab
7. **Download Results** as JSON/CSV

### Content Safety Analysis (Safety Studio)

**Navigate to: Content Safety → Text Analysis**

1. **Upload Batch Results** from episode generation
2. **Analysis Settings**:
   - ✅ Hate and fairness
   - ✅ Sexual content
   - ✅ Violence and harm
   - ✅ Self-harm content
3. **Custom Categories** for AI-102:
   - Technical accuracy
   - Certification relevance
   - Professional tone
4. **Run Analysis** - automatic flagging
5. **Review Results** in dashboard
6. **Export Report** for compliance

### Quality Evaluation (Evaluation Studio)

**Navigate to: Evaluation → Custom Evaluations**

1. **Create Evaluation Set** with sample episodes
2. **Define Metrics**:
   - Content accuracy (1-5 scale)
   - Learning objective alignment
   - Exam relevance
   - Audio-friendly formatting
3. **Run Evaluation** against generated content
4. **View Results Dashboard**:
   - Overall quality scores
   - Individual episode ratings  
   - Improvement recommendations
5. **Export Evaluation Report**

### Audio Synthesis (Speech Studio Integration)

**Navigate to: Speech Studio → Text to Speech**

1. **Import Text Files** from AI Foundry Data
2. **Voice Selection**: `en-US-GuyNeural`
3. **SSML Enhancement**:
   - Add pauses between sections
   - Emphasize key terms
   - Adjust speaking rate for clarity
4. **Batch Synthesis**:
   - Process all episodes at once
   - Choose MP3 format (16kHz)
   - Enable quality enhancement
5. **Preview & Download**:
   - Sample audio quality
   - Download individual files
   - Bulk download ZIP archive

### Asset Management (Data Explorer)

**Navigate to: Data → Data Explorer**

1. **Organize Files** by type:
   - `course-outlines/` - JSON structure files
   - `narrations/` - Episode text content  
   - `ssml/` - Speech markup files
   - `audio/` - Generated MP3 files
2. **Version Control**: Automatic versioning of all assets
3. **Metadata Tracking**: Auto-tagged with generation parameters
4. **Sharing & Export**: Generate download links
5. **Storage Integration**: Automatic backup to Azure Storage

## 🔧 Advanced AI Foundry Features

### Automated Workflows (Flow Designer)

**Navigate to: Prompt Flow → Create Flow**

1. **Start with Template**: Select "Batch Text Processing"
2. **Visual Flow Builder**:
   - 📥 **Input Node**: Episode data (CSV/JSON)
   - 🤖 **LLM Node**: Connected to `gpt-4o-deployment`
   - 🛡️ **Safety Node**: Content filtering
   - ⚖️ **Evaluation Node**: Quality scoring  
   - 💾 **Output Node**: Save to storage
3. **Configure Connections**: Point-and-click service linking
4. **Test Flow**: Use sample data for validation
5. **Deploy Flow**: One-click deployment to endpoint
6. **Schedule Jobs**: Set up automated batch processing

### Real-Time Monitoring (Dashboard)

**Navigate to: Monitoring → Dashboards**

1. **Cost Tracking Dashboard**:
   - Token usage by model
   - Cost per episode generated
   - Monthly spend trending
   - Service usage breakdown
2. **Quality Metrics Dashboard**:
   - Content safety scores
   - Evaluation results trending
   - Error rates and failures
   - Processing speed metrics
3. **Custom Alerts**:
   - Budget threshold warnings
   - Quality score drops
   - Service availability issues
   - Job completion notifications

### Content Versioning (Asset Management)

**Navigate to: Data → Version Control**

1. **Automatic Versioning**:
   - Every generation creates new version
   - Metadata tracking (timestamp, parameters)
   - Comparison tools between versions
   - Rollback capabilities
2. **Branch Management**:
   - Development vs Production branches
   - A/B testing different prompts
   - Collaborative editing workflows
   - Change approval processes
3. **Asset Lineage**:
   - Track prompt → generation → evaluation chain
   - Identify which prompts produce best results
   - Audit trail for compliance
   - Impact analysis for changes

## 📊 Performance Optimization (UI-Driven)

### Batch Processing Configuration

**Navigate to: Compute → Batch Endpoints**

1. **Create Batch Endpoint**: One-click deployment
2. **Configure Resources**:
   - Instance type: Standard_E4s_v3 (recommended)
   - Auto-scaling: 1-10 instances
   - Timeout: 60 minutes per episode
3. **Submit Batch Jobs**:
   - Upload episode data via web interface
   - Monitor progress in real-time dashboard
   - Download results automatically
4. **Cost Optimization**:
   - Spot instance usage for cost savings
   - Automatic shutdown when idle
   - Resource usage recommendations

### Prompt Performance Tuning

**Navigate to: Evaluation → A/B Testing**

1. **Create Prompt Variants**:
   - Version A: Current prompt
   - Version B: Optimized version  
   - Version C: Alternative approach
2. **Configure Test Parameters**:
   - Split traffic: 33/33/34%
   - Success metrics: Quality score, speed, cost
   - Test duration: 1 week
3. **Monitor Results**:
   - Real-time performance comparison
   - Statistical significance tracking
   - Automatic winner selection
4. **Deploy Best Version**:
   - One-click promotion to production
   - Gradual rollout capabilities
   - Rollback safety net

---

## 🆚 Comparison: AI Foundry UI-First vs Other Approaches

| Feature | AI Foundry UI-First | Pure PromptFlow | Traditional Coding |
|---------|-------------------|-----------------|-------------------|
| **No Coding Required** | ✅ 100% web interface | ✅ Visual flows only | ❌ Requires programming |
| **Learning Curve** | 🟢 Very Low | 🟡 Low-Medium | 🔴 High |
| **Setup Time** | 🟢 Minutes | 🟡 Hours | 🔴 Days |
| **Prompt Management** | ✅ Visual editor + versioning | ✅ Visual editor | ❌ Manual files |
| **Content Safety** | ✅ Built-in dashboard | ⚠️ Manual configuration | ❌ External integration |
| **Batch Processing** | ✅ Point-and-click batch jobs | ⚠️ Flow-based only | ⚠️ Custom scripting |
| **Monitoring & Analytics** | ✅ Real-time dashboards | ⚠️ Basic flow metrics | ❌ Custom implementation |
| **Team Collaboration** | ✅ Web-based sharing | ⚠️ Flow sharing only | ❌ Code repository needed |
| **Cost Tracking** | ✅ Built-in cost dashboards | ⚠️ Manual tracking | ❌ Custom implementation |
| **A/B Testing** | ✅ Built-in experimentation | ❌ Not available | ❌ Custom implementation |
| **Scalability** | ✅ Auto-scaling compute | ✅ Managed scaling | ⚠️ Manual scaling |
| **Enterprise Features** | ✅ Full governance suite | ⚠️ Basic governance | ❌ Custom governance |

---

## 🎯 When to Choose AI Foundry UI-First Approach

**Perfect for organizations that want:**

- **✅ No technical barriers** - Anyone can create and manage AI workflows
- **✅ Rapid deployment** - From idea to production in hours, not weeks  
- **✅ Built-in best practices** - Enterprise-grade governance and safety
- **✅ Visual collaboration** - Share and iterate through web interface
- **✅ Cost transparency** - Real-time spend tracking and optimization
- **✅ Quality assurance** - Automated evaluation and content safety
- **✅ Scalable infrastructure** - Auto-scaling without configuration

**Choose Other Approaches when you:**

- **PromptFlow**: Need custom logic within visual workflows
- **Traditional Coding**: Require integration with existing codebases
- **Hybrid Approach**: Want UI simplicity with code flexibility

---

## 📚 Next Steps

1. **🌐 Access AI Foundry**: Navigate to [ai.azure.com](https://ai.azure.com) and create your project
2. **🔗 Set Up Connections**: Use the web UI to configure all service connections
3. **📝 Create Prompt Assets**: Build your prompts using the visual prompt editor
4. **🧪 Test in Playground**: Validate your prompts with the interactive chat interface
5. **🚀 Run Batch Jobs**: Generate all episodes using the batch processing interface
6. **📊 Monitor Results**: Track progress, costs, and quality through built-in dashboards

This **AI Foundry UI-First approach** eliminates technical barriers and lets anyone create professional AI-powered content pipelines. You get enterprise-grade capabilities—prompt management, content safety, quality evaluation, cost tracking, and scalable infrastructure—all through an intuitive web interface.

**Perfect for organizations that want maximum AI capabilities with zero coding requirements.**