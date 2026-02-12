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
- **Dynamic learning path resolution**: Uses catalog role + product filtering instead of hardcoded UIDs
- Hardcoded UIDs kept as fallback (stale UIDs auto-detected and skipped)
- Learning path content provides foundations
- Exam skills ensure all testable items are covered
- **Coverage sweep**: Each exam topic is checked against discovered content with a 3-level fallback chain:
  1. Title match against discovered module/unit titles
  2. Catalog module description search
  3. Microsoft Learn docs search API
  4. Explicit gap reporting for truly uncovered topics
- **Confidence score**: Weighted percentage showing content completeness (Grade A–F)
- ~10-12 hours for DP-700
- **Full official content coverage**

### Confidence Score

In comprehensive mode, the output includes a confidence score grading content coverage:

| Grade | Score | Meaning |
|-------|-------|---------|
| A | ≥ 90% | Excellent — nearly all exam topics have dedicated content |
| B | ≥ 75% | Good — most topics covered, some supplemented from search |
| C | ≥ 60% | Adequate — significant supplementation needed |
| D | ≥ 40% | Poor — many topics rely on best-effort search results |
| F | < 40% | Critical — major content gaps |

The score weights different coverage sources:
- **Learning path match (1.0)**: Topic directly covered by discovered modules
- **Catalog supplement (0.8)**: Topic matched to a catalog module by description
- **Search supplement (0.5)**: Topic found via Learn docs search API
- **Gap (0.0)**: No content found

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

## Understanding Audio Duration vs Microsoft's Course Times

### Why Our Output is ~6 Hours When Microsoft Says "26 Hours"

Microsoft's listed course duration (e.g., "26 hours" for DP-700) includes **all learning activities**, not just text content:

| Content Type | Microsoft Time | Audio Convertible? | Our Coverage |
|-------------|----------------|-------------------|--------------|
| **Text content** (concepts, explanations) | ~6-7 hours | ✅ Yes | **100%** |
| **Hands-on labs** | ~15 hours | ❌ No | Not applicable |
| **Knowledge checks** (quizzes) | ~2-3 hours | ❌ No | Not applicable |
| **Exercise setup/teardown** | ~1-2 hours | ❌ No | Not applicable |

### Breaking Down DP-700 Specifically

From our analysis of actual Microsoft Learn content:

```
Total learning path words:  ~95,000 (raw, with duplicates)
After deduplication:        ~57,873 unique words
At 150 words/minute:        ~386 minutes ≈ 6.4 hours of audio
```

**This 6.4 hours IS the full text portion.** The remaining ~20 hours are:
- "Exercise - Create a lakehouse" (hands-on lab)
- "Module assessment" (interactive quiz)
- Time to configure Azure environments, run notebooks, etc.

### Why Labs Can't Be Audio

Consider this exercise from DP-700:
```
Exercise - Analyze data with Apache Spark (86 words)
```

Those 86 words are just instructions like:
> "1. Navigate to your Fabric workspace
>  2. Create a new notebook
>  3. Write code to load a CSV file..."

The actual learning happens when you **do** the lab (~45 minutes), not when you read instructions (~30 seconds).

### Content Word Count Analysis

| Content Type | Units | Words | Audio Time |
|-------------|-------|-------|------------|
| **Conceptual content** | 82 | ~48,000 | ~5.3 hours |
| **Introductions** | 22 | ~3,500 | ~23 min |
| **Summaries** | 22 | ~2,200 | ~15 min |
| **Knowledge checks** | 22 | ~3,500 | (not narrated) |
| **Exercises** | 20 | ~1,700 | (instructions only) |

### Comprehensive Mode Adds More

When `--comprehensive` mode is enabled, we also include exam skill objectives:
- 8 skill domains with 45 specific skills
- Each skill gets its own episode or shares with related skills
- Adds ~1-2 hours of targeted exam prep content

**Total with comprehensive mode: ~7-8 hours**

### Completeness Guarantee

The system **NEVER truncates content**. Every topic in the discovery output is covered:

1. **Narration prompt**: Explicitly states "Cover ALL topics - NEVER truncate"
2. **Multi-part episodes**: Long modules automatically split into Part 1, Part 2, etc.
3. **Continuation logic**: If a narration ends with "[END OF PART]", the system generates the next part
4. **Quality checks**: Episodes are validated for topic coverage before saving

### Summary

| Claim | Reality |
|-------|---------|
| "Microsoft says 26 hours" | Includes ~15h of hands-on labs |
| "We only output ~6 hours" | That IS the full text content |
| "Content is truncated" | ❌ False - all text is covered |
| "Labs are missing" | ✅ Correct - labs can't be audio |

## Implementation Files

- `src/pipeline/tools/deep_discover.py` - Learning path discovery via Catalog API
- `src/pipeline/tools/discover_exam_content.py` - Exam skills outline scraping
- `src/pipeline/tools/generate_episodes.py` - Episode generation from discovered content
