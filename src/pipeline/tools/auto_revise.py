"""
Auto-revise narration based on quality check results.
"""

import json
import os

from azure.identity import DefaultAzureCredential
from openai import AzureOpenAI
from promptflow.core import tool


REVISION_SYSTEM_PROMPT = """You are an expert educational content editor. Your task is to revise narration scripts based on quality feedback.

## Revision Guidelines
1. Address ALL issues marked as 'critical' or 'major' severity
2. Maintain the original structure and flow where possible
3. Keep the same format (instructional or podcast with [HOST]/[EXPERT] markers)
4. Preserve [PAUSE] markers and add more where needed
5. Ensure technical accuracy is maintained or improved
6. Target 1,200-1,500 words

## Format Preservation
- If the original uses [HOST] and [EXPERT] markers, keep using them
- If the original is instructional (single voice), keep it that way
- Maintain the same level of technical depth

Output ONLY the revised narration - no explanation or meta-commentary."""


@tool
def auto_revise(
    narration: str,
    quality_check_result: str,
    skill_domain: str,
    skill_topics: list[str],
    audio_format: str,
) -> str:
    """
    Auto-revise narration if quality check indicates issues.

    Args:
        narration: Original narration text
        quality_check_result: JSON string with QC results
        skill_domain: Skill domain being covered
        skill_topics: Topics being covered
        audio_format: 'instructional' or 'podcast'

    Returns:
        Revised narration (or original if no revision needed)
    """
    # Parse quality check result
    try:
        qc_result = json.loads(quality_check_result)
    except json.JSONDecodeError:
        # If we can't parse QC result, return original
        print("Warning: Could not parse quality check result, returning original narration")
        return narration

    # Check if revision is needed
    if qc_result.get("passed", True):
        print("Quality check passed, no revision needed")
        return narration

    # Get issues that need addressing
    issues = qc_result.get("issues", [])
    critical_issues = [i for i in issues if i.get("severity") in ["critical", "major"]]

    if not critical_issues:
        print("No critical/major issues found, returning original narration")
        return narration

    print(f"Revising narration to address {len(critical_issues)} critical/major issues")

    # Get configuration from environment
    openai_endpoint = os.environ.get("OPENAI_ENDPOINT")
    if not openai_endpoint:
        raise ValueError("OPENAI_ENDPOINT environment variable required")

    credential = DefaultAzureCredential()
    client = AzureOpenAI(
        azure_endpoint=openai_endpoint,
        azure_ad_token_provider=lambda: credential.get_token(
            "https://cognitiveservices.azure.com/.default"
        ).token,
        api_version="2024-02-01",
    )

    # Build revision prompt
    issues_text = "\n".join(
        f"- [{i['severity'].upper()}] {i['category']}: {i['description']}\n  Suggestion: {i.get('suggestion', 'N/A')}"
        for i in critical_issues
    )

    user_prompt = f"""## Original Narration:
{narration}

## Quality Issues to Address:
{issues_text}

## Context:
- Skill Domain: {skill_domain}
- Topics: {', '.join(skill_topics)}
- Format: {audio_format}

Please revise the narration to address all listed issues while maintaining the overall structure and quality."""

    # Call GPT-4o for revision
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": REVISION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        max_tokens=4000,
    )

    revised_narration = response.choices[0].message.content

    print("Narration revised successfully")
    return revised_narration
