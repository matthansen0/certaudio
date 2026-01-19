# Content Discovery Logic

This document explains how CertAudio discovers and organizes content for Microsoft certification exams.

## Two Content Sources

Microsoft provides two distinct content structures for certification preparation:

### 1. Learning Paths (Educational Content)

**Source**: Microsoft Learn Catalog API (`https://learn.microsoft.com/api/catalog/`)

**Structure**:
```
Learning Path
  └─ Module
       └─ Unit (topic)
```

**Purpose**: Teaches concepts and foundational knowledge.

**Example (DP-700)**:
- "Use Apache Spark in Microsoft Fabric" (module)
  - "Introduction to Apache Spark" (unit)
  - "Run Spark code in notebooks" (unit)
  - "Work with data in Spark" (unit)

**Characteristics**:
- Conceptual, educational tone
- Includes intro/summary/exercise units
- Same module can appear in multiple learning paths (deduplication needed)
- ~22 unique modules for DP-700

### 2. Exam Skills Outline (Testable Skills)

**Source**: Exam Study Guide page (e.g., `https://aka.ms/DP700-StudyGuide`)

**Structure**:
```
Domain (with % weight)
  └─ Objective
       └─ Skill (specific testable item)
```

**Purpose**: Defines exactly what Microsoft will test on the exam.

**Example (DP-700)**:
- "Monitor and optimize an analytics solution (30-35%)" (domain)
  - "Optimize performance" (objective)
    - "Optimize Spark performance" (skill)
    - "Optimize query performance" (skill)
    - "Optimize eventstreams and eventhouses" (skill)

**Characteristics**:
- Action-oriented, specific
- Maps directly to exam questions
- Some skills have NO dedicated learning path content
- 55 specific skills for DP-700

## The Gap: Why Both Are Needed

Learning paths and exam skills are **complementary, not overlapping**:

| Learning Path Unit | Exam Skill |
|-------------------|------------|
| "Use Apache Spark in Microsoft Fabric" | "Optimize Spark performance" |
| "Work with Delta Lake tables" | "Optimize a lakehouse table" |
| "Introduction to eventstreams" | "Process data by using eventstreams" |

The learning path teaches **"what is this thing"** while the exam skill requires **"how do I do this specific action"**.

### Skills Without Learning Path Coverage

Some exam skills don't have dedicated learning path content:

- ❌ "Implement database projects"
- ❌ "Implement dynamic data masking"
- ❌ "Apply sensitivity labels to items"
- ❌ "Endorse items"
- ❌ "Implement mirroring"
- ❌ "Handle duplicate, missing, and late-arriving data"
- ❌ "Choose between accelerated vs non-accelerated shortcuts"
- ❌ "Create windowing functions"

## Discovery Modes

### `skills` Mode (Basic)
- Scrapes exam page for skills outline only
- Fastest, least content
- Good for quick overview

### `deep` Mode (Learning Paths Only - Legacy)
- Fetches all learning paths via Catalog API
- ~5-7 hours for DP-700
- Missing some testable skills

### `comprehensive` Mode (Recommended)
- Combines BOTH learning paths AND exam skills outline
- Learning path content provides foundations
- Exam skills ensure all testable items are covered
- ~10-12 hours for DP-700
- **Full official content coverage**

## Expected Duration by Certification

| Certification | Learning Paths | Exam Skills | Combined (Comprehensive) |
|--------------|----------------|-------------|--------------------------|
| DP-700 | 22 modules (~5h) | 55 skills (~7h) | ~10-12 hours |
| AZ-104 | 28 modules (~6h) | ~60 skills (~8h) | ~12-14 hours |
| AI-102 | ~20 modules (~5h) | ~45 skills (~6h) | ~9-11 hours |

## Episode Structure

### Learning Path Episodes
- Grouped by module (5 units per episode)
- Title: "Module Name (Part N)" if split
- Focus: Explaining concepts

### Exam Skill Episodes  
- One episode per skill (or grouped by objective)
- Title: "Skill Name" or "Objective: Skill Focus"
- Focus: How to perform the specific action

### Combined Flow
1. Learning path episodes come first (foundations)
2. Exam skill episodes follow (targeted prep)
3. Listener progresses: understand → apply → test-ready

## Deduplication Rules

1. **Module deduplication**: Same module appearing in multiple learning paths is only processed once (by UID)
2. **Unit filtering**: Exercise/summary/knowledge-check units are filtered as lower priority
3. **No skill deduplication**: All exam skills are included even if conceptually similar to a learning path unit

## Content Hashing for Updates

Each content item has a hash stored in Cosmos DB:
- Learning path unit: hash of unit content
- Exam skill: hash of skill description

When Microsoft updates content, the hash changes, triggering amendment episode generation.

## Implementation Files

- `src/pipeline/tools/deep_discover.py` - Learning path discovery via Catalog API
- `src/pipeline/tools/discover_exam_content.py` - Exam skills outline scraping
- `src/pipeline/tools/generate_episodes.py` - Episode generation from discovered content
