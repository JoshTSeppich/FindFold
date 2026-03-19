"""
Claude API re-scorer — second-pass ICP evaluation for ambiguous leads.

Only leads in the keyword-score range [CLAUDE_AMBIGUOUS_MIN, CLAUDE_AMBIGUOUS_MAX]
are sent to Claude. Clear passes (high keyword score) and clear fails (low score)
are left alone — this keeps API spend minimal.

Model: claude-haiku-4-5-20251001  (~$0.25/1M input tokens)
Typical spend: ~500 tokens per lead × 50 leads = 25K tokens ≈ $0.006 per run

Claude's score replaces the keyword score for ambiguous leads.
If no API key is set the function is a no-op (returns leads unchanged).
"""

import asyncio
import json
import logging
import os
import re
from typing import Optional

from config import (
    CLAUDE_MODEL,
    CLAUDE_AMBIGUOUS_MIN,
    CLAUDE_AMBIGUOUS_MAX,
    CLAUDE_CONCURRENCY,
    CLAUDE_FINAL_THRESHOLD,
)

logger = logging.getLogger(__name__)

_SYSTEM = """\
You are an ICP qualification expert for FoxWorks.dev, a product that automates inbound lead handling for local service businesses.

Ideal customer profile:
- Small/medium LOCAL service business (1–50 employees)
- Gets inbound leads via phone, web form, or walk-in
- Currently handles them manually or inefficiently (no CRM, manual follow-up, missed calls)
- Industries: home services (plumbing, HVAC, roofing, electrical, cleaning, landscaping),
  appointment-based (med spa, dental, wellness, gym, salon), local service (property mgmt, agencies)

NOT a fit:
- National franchises or chains (Roto-Rooter, Servpro, Molly Maid, Planet Fitness, Aspen Dental)
- Enterprise / platform companies
- Directories, marketplaces, aggregator sites
- Tech-first or SaaS businesses
"""

_PROMPT = """\
Evaluate this local business as a potential FoxWorks customer.

Company: {company_name}
Domain: {domain}
Category: {category}
Location: {location}
Page title: {page_title}
Meta description: {page_description}
Page excerpt: {page_text}

Score this lead from 0.0 to 1.0 and explain briefly.

Score guide:
  0.8–1.0  Clear SMB fit — local, service-oriented, inbound-dependent, manually operating
  0.6–0.8  Likely fit with minor uncertainty
  0.4–0.6  Ambiguous — could be a chain, enterprise, or off-ICP
  0.0–0.4  Not a fit — franchise, enterprise, directory, or irrelevant industry

Respond ONLY with valid JSON, no markdown:
{{"score": 0.0, "reason": "one concise sentence"}}"""


def _build_prompt(lead: dict) -> str:
    return _PROMPT.format(
        company_name    = lead.get("company_name",     "")[:80],
        domain          = lead.get("domain",           ""),
        category        = lead.get("category",         ""),
        location        = lead.get("location",         ""),
        page_title      = lead.get("page_title",       "")[:120],
        page_description= lead.get("page_description", "")[:200],
        page_text       = lead.get("page_text",        "")[:400],
    )


def _parse_response(text: str) -> Optional[tuple[float, str]]:
    """Extract (score, reason) from Claude's JSON response."""
    # Strip markdown code fences if Claude wraps with them
    text = re.sub(r"```[a-z]*\n?", "", text).strip()
    try:
        data   = json.loads(text)
        score  = float(data["score"])
        reason = str(data.get("reason", "")).strip()
        return round(max(0.0, min(1.0, score)), 3), reason
    except Exception:
        # Fallback: regex hunt
        m = re.search(r'"score"\s*:\s*([0-9.]+)', text)
        if m:
            return round(float(m.group(1)), 3), ""
        return None


async def _score_one(client, lead: dict, sem: asyncio.Semaphore) -> None:
    """Score one lead in-place. Mutates lead dict."""
    async with sem:
        try:
            response = await client.messages.create(
                model      = CLAUDE_MODEL,
                max_tokens = 120,
                system     = _SYSTEM,
                messages   = [{"role": "user", "content": _build_prompt(lead)}],
            )
            raw    = response.content[0].text
            result = _parse_response(raw)

            if result:
                claude_score, reason = result
                lead["icp_score"]   = claude_score
                lead["claude_score"] = claude_score
                lead["reason_tags"] = lead.get("reason_tags", "") + f"|claude:{reason[:60]}"
                logger.debug(
                    f"[claude] {lead['company_name'][:40]} "
                    f"kw={lead.get('_kw_score', '?')} → claude={claude_score}"
                )
        except Exception as e:
            logger.debug(f"[claude] error for {lead.get('domain')}: {e}")


async def rescore_ambiguous(leads: list[dict]) -> list[dict]:
    """
    Re-score leads whose keyword score falls in the ambiguous band using Claude.
    Leads outside the band are returned unchanged.
    If ANTHROPIC_API_KEY is not set, returns all leads unchanged.

    After rescoring, leads that fall below CLAUDE_FINAL_THRESHOLD are dropped.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.info("[claude] ANTHROPIC_API_KEY not set — skipping Claude rescoring")
        return leads

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        logger.warning("[claude] anthropic package not installed — skipping")
        return leads

    ambiguous = [
        lead for lead in leads
        if CLAUDE_AMBIGUOUS_MIN <= lead.get("icp_score", 0) <= CLAUDE_AMBIGUOUS_MAX
    ]
    clear_pass = [
        lead for lead in leads
        if lead.get("icp_score", 0) > CLAUDE_AMBIGUOUS_MAX
    ]
    # Leads below MIN are already dropped before this stage

    if not ambiguous:
        logger.info("[claude] no ambiguous leads to rescore")
        return leads

    logger.info(
        f"[claude] rescoring {len(ambiguous)} ambiguous leads "
        f"({CLAUDE_AMBIGUOUS_MIN}–{CLAUDE_AMBIGUOUS_MAX}) with {CLAUDE_MODEL}"
    )

    # Stash keyword score for logging
    for lead in ambiguous:
        lead["_kw_score"] = lead["icp_score"]

    client = AsyncAnthropic(api_key=api_key)
    sem    = asyncio.Semaphore(CLAUDE_CONCURRENCY)
    await asyncio.gather(*[_score_one(client, lead, sem) for lead in ambiguous])

    # Apply final threshold to Claude-scored leads
    passed_ambiguous = [
        lead for lead in ambiguous
        if lead.get("icp_score", 0) >= CLAUDE_FINAL_THRESHOLD
    ]
    dropped = len(ambiguous) - len(passed_ambiguous)

    logger.info(
        f"[claude] {len(ambiguous)} ambiguous → "
        f"{len(passed_ambiguous)} passed / {dropped} dropped after Claude"
    )

    return clear_pass + passed_ambiguous
