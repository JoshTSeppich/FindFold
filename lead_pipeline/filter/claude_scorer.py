"""
Claude API re-scorer — batched second-pass ICP evaluation for ambiguous leads.

Only leads in the keyword-score range [CLAUDE_AMBIGUOUS_MIN, CLAUDE_AMBIGUOUS_MAX]
are sent to Claude. Clear passes (high keyword score) and clear fails (low score)
are left alone — this keeps API spend minimal.

Batching: 10 leads per API call. System prompt paid once per batch instead of
once per lead → 90% reduction in system-prompt token spend.

Model: claude-haiku-4-5-20251001  (~$0.25/1M input tokens)
Typical spend: ~$0.003 per 50 ambiguous leads (batched)

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

_BATCH_SIZE = 10   # leads per API call — amortises system prompt cost

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

_BATCH_PROMPT_HEADER = """\
Evaluate each local business as a potential FoxWorks customer. Return a JSON array with one object per lead, in the same order.

Score guide:
  0.8–1.0  Clear SMB fit — local, service-oriented, inbound-dependent, manually operating
  0.6–0.8  Likely fit with minor uncertainty
  0.4–0.6  Ambiguous — could be a chain, enterprise, or off-ICP
  0.0–0.4  Not a fit — franchise, enterprise, directory, or irrelevant industry

Respond ONLY with a valid JSON array, no markdown, no explanation outside the array:
[{"index": 0, "score": 0.0, "reason": "one concise sentence"}, ...]

Leads to evaluate:
"""


def _lead_entry(idx: int, lead: dict) -> str:
    """Serialise one lead to JSON for the batch prompt (injection-safe)."""
    entry = {
        "index":            idx,
        "company_name":     (lead.get("company_name") or "")[:80],
        "domain":           lead.get("domain") or "",
        "category":         (lead.get("category") or "")[:60],
        "location":         (lead.get("location") or "")[:60],
        "page_title":       (lead.get("page_title") or "")[:120],
        "page_description": (lead.get("page_description") or "")[:200],
        "page_text":        (lead.get("page_text") or "")[:300],
    }
    return json.dumps(entry, ensure_ascii=False)


def _build_batch_prompt(batch: list[dict]) -> str:
    entries = "\n".join(_lead_entry(i, lead) for i, lead in enumerate(batch))
    return _BATCH_PROMPT_HEADER + entries


def _parse_batch_response(text: str, batch_size: int) -> list[Optional[tuple[float, str]]]:
    """
    Parse Claude's batch JSON response.
    Returns list of (score, reason) or None per index position.
    """
    # Strip markdown fences (handles ``` and ```json and trailing ```)
    text = re.sub(r"```[a-z]*", "", text).replace("```", "").strip()

    results: list[Optional[tuple[float, str]]] = [None] * batch_size

    try:
        data = json.loads(text)
        if isinstance(data, list):
            for item in data:
                idx = int(item.get("index", -1))
                if 0 <= idx < batch_size:
                    score  = round(max(0.0, min(1.0, float(item["score"]))), 3)
                    reason = str(item.get("reason", "")).strip()
                    results[idx] = (score, reason)
    except Exception:
        # Fallback: extract individual score objects by index
        for m in re.finditer(r'"index"\s*:\s*(\d+)[^}]*"score"\s*:\s*([0-9.]+)', text):
            idx   = int(m.group(1))
            score = round(float(m.group(2)), 3)
            if 0 <= idx < batch_size:
                results[idx] = (score, "")

    return results


async def _score_batch(client, batch: list[dict], sem: asyncio.Semaphore) -> None:
    """Score a batch of leads in one API call. Mutates each lead dict."""
    async with sem:
        try:
            response = await asyncio.wait_for(
                client.messages.create(
                    model      = CLAUDE_MODEL,
                    max_tokens = 80 * len(batch),   # ~80 tokens per lead in response
                    system     = _SYSTEM,
                    messages   = [{"role": "user", "content": _build_batch_prompt(batch)}],
                ),
                timeout=45.0,
            )
            raw     = response.content[0].text
            results = _parse_batch_response(raw, len(batch))

            for i, result in enumerate(results):
                lead = batch[i]
                if result:
                    claude_score, reason = result
                    lead["icp_score"]    = claude_score
                    lead["claude_score"] = claude_score
                    lead["reason_tags"]  = lead.get("reason_tags", "") + f"|claude:{reason[:80]}"
                    logger.debug(
                        f"[claude] {lead.get('company_name', '')[:40]} "
                        f"kw={lead.get('_kw_score', '?'):.2f} → claude={claude_score}"
                    )
                else:
                    logger.debug(f"[claude] no result for index {i}: {lead.get('domain')}")

        except asyncio.TimeoutError:
            logger.warning(f"[claude] batch timeout ({len(batch)} leads) — skipping")
        except Exception as e:
            logger.warning(f"[claude] batch error: {e}")


async def rescore_ambiguous(leads: list[dict]) -> list[dict]:
    """
    Re-score leads whose keyword score falls in the ambiguous band using Claude.
    Uses batched API calls (10 leads/call) to minimise token spend.
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

    if not ambiguous:
        logger.info("[claude] no ambiguous leads to rescore")
        return leads

    logger.info(
        f"[claude] rescoring {len(ambiguous)} ambiguous leads in "
        f"{(len(ambiguous) + _BATCH_SIZE - 1) // _BATCH_SIZE} batches of {_BATCH_SIZE} "
        f"({CLAUDE_AMBIGUOUS_MIN}–{CLAUDE_AMBIGUOUS_MAX}) with {CLAUDE_MODEL}"
    )

    # Stash keyword score for logging/audit
    for lead in ambiguous:
        lead["_kw_score"]    = lead["icp_score"]
        lead["claude_score"] = None   # explicitly unset until scored

    # Split into batches
    batches = [
        ambiguous[i : i + _BATCH_SIZE]
        for i in range(0, len(ambiguous), _BATCH_SIZE)
    ]

    client = AsyncAnthropic(api_key=api_key)
    sem    = asyncio.Semaphore(CLAUDE_CONCURRENCY)
    await asyncio.gather(*[_score_batch(client, batch, sem) for batch in batches])

    # Apply final threshold
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
