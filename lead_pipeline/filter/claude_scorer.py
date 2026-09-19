"""
Second opinion from Claude on the leads the keyword rules could not decide.

Only leads with a keyword score inside the ambiguous band go to Claude. Clear
passes and clear fails never cost an API call. Leads go ten per request so the
system prompt is paid once per ten leads instead of once per lead. Claude's
score replaces the keyword score, and the lead must then clear
CLAUDE_FINAL_THRESHOLD.

Without ANTHROPIC_API_KEY the keyword threshold is final and nothing is sent.
"""

import asyncio
import json
import logging
import os
import re
from typing import Optional

from config import (
    CLAUDE_AMBIGUOUS_MAX,
    CLAUDE_AMBIGUOUS_MIN,
    CLAUDE_CONCURRENCY,
    CLAUDE_FINAL_THRESHOLD,
    CLAUDE_MODEL,
    ICP_THRESHOLD,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 10

SYSTEM_PROMPT = """\
You qualify leads for FoxWorks.dev, a product that automates inbound lead handling for local service businesses.

Ideal customer:
- Small or medium local service business, 1 to 50 employees
- Gets inbound leads by phone, web form, or walk-in
- Handles them by hand today: no CRM, manual follow-up, missed calls
- Industries: home services (plumbing, HVAC, roofing, electrical, cleaning, landscaping),
  appointment based (med spa, dental, wellness, gym, salon), local services (property management, agencies)

Not a fit:
- National franchises or chains (Roto-Rooter, Servpro, Molly Maid, Planet Fitness, Aspen Dental)
- Enterprise or platform companies
- Directories, marketplaces, aggregator sites
- Tech-first or SaaS businesses
"""

BATCH_PROMPT_HEADER = """\
Score each business as a potential FoxWorks customer. Return a JSON array with one object per lead, in the same order.

Score guide:
  0.8 to 1.0  Clear fit: local, service based, depends on inbound work, runs by hand
  0.6 to 0.8  Likely fit with some doubt
  0.4 to 0.6  Unclear: could be a chain, enterprise, or the wrong industry
  0.0 to 0.4  Not a fit: franchise, enterprise, directory, or unrelated industry

Respond with only a valid JSON array, no markdown, nothing outside the array:
[{"index": 0, "score": 0.0, "reason": "one short sentence"}, ...]

Leads:
"""


def lead_entry(idx: int, lead: dict) -> str:
    """One lead as a JSON line. Fields are truncated so a page cannot flood the prompt."""
    entry = {
        "index": idx,
        "company_name": (lead.get("company_name") or "")[:80],
        "domain": lead.get("domain") or "",
        "category": (lead.get("category") or "")[:60],
        "location": (lead.get("location") or "")[:60],
        "page_title": (lead.get("page_title") or "")[:120],
        "page_description": (lead.get("page_description") or "")[:200],
        "page_text": (lead.get("page_text") or "")[:300],
    }
    return json.dumps(entry, ensure_ascii=False)


def build_batch_prompt(batch: list[dict]) -> str:
    return BATCH_PROMPT_HEADER + "\n".join(lead_entry(i, lead) for i, lead in enumerate(batch))


def parse_batch_response(text: str, batch_size: int) -> list[Optional[tuple[float, str]]]:
    """Turn Claude's reply into a (score, reason) per index, or None where it gave nothing."""
    text = re.sub(r"```[a-z]*", "", text).replace("```", "").strip()
    results: list[Optional[tuple[float, str]]] = [None] * batch_size

    try:
        data = json.loads(text)
        if isinstance(data, list):
            for item in data:
                idx = int(item.get("index", -1))
                if 0 <= idx < batch_size:
                    score = round(max(0.0, min(1.0, float(item["score"]))), 3)
                    results[idx] = (score, str(item.get("reason", "")).strip())
    except Exception:
        # The JSON was malformed. Salvage whatever index and score pairs are there.
        for m in re.finditer(r'"index"\s*:\s*(\d+)[^}]*"score"\s*:\s*([0-9.]+)', text):
            idx = int(m.group(1))
            if 0 <= idx < batch_size:
                results[idx] = (round(float(m.group(2)), 3), "")

    return results


async def score_batch(client, batch: list[dict], sem: asyncio.Semaphore) -> None:
    """Send one batch and write the scores back onto the lead dicts."""
    async with sem:
        try:
            response = await asyncio.wait_for(
                client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=80 * len(batch),
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": build_batch_prompt(batch)}],
                ),
                timeout=45.0,
            )
            results = parse_batch_response(response.content[0].text, len(batch))

            for lead, result in zip(batch, results):
                if result is None:
                    logger.debug(f"[claude] no score for {lead.get('domain')}")
                    continue
                score, reason = result
                lead["icp_score"] = score
                lead["claude_score"] = score
                lead["reason_tags"] = lead.get("reason_tags", "") + f"|claude:{reason[:80]}"
                logger.debug(
                    f"[claude] {lead.get('company_name', '')[:40]} "
                    f"keyword {lead.get('_kw_score', 0):.2f}, claude {score}"
                )

        except asyncio.TimeoutError:
            logger.warning(f"[claude] batch of {len(batch)} timed out, keeping keyword scores")
        except Exception as e:
            logger.warning(f"[claude] batch error: {e}")


def keyword_threshold_only(leads: list[dict]) -> list[dict]:
    return [lead for lead in leads if lead.get("icp_score", 0) >= ICP_THRESHOLD]


async def rescore_ambiguous(leads: list[dict]) -> list[dict]:
    """Rescore the ambiguous band with Claude and apply the final threshold.

    Leads above the band pass through untouched. If Claude cannot run, the
    plain keyword threshold is applied instead so nothing below it leaks out.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.info("[claude] ANTHROPIC_API_KEY not set, keyword scores are final")
        return keyword_threshold_only(leads)

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        logger.warning("[claude] anthropic package not installed, keyword scores are final")
        return keyword_threshold_only(leads)

    ambiguous = [
        lead for lead in leads
        if CLAUDE_AMBIGUOUS_MIN <= lead.get("icp_score", 0) <= CLAUDE_AMBIGUOUS_MAX
    ]
    clear_pass = [lead for lead in leads if lead.get("icp_score", 0) > CLAUDE_AMBIGUOUS_MAX]

    if not ambiguous:
        logger.info("[claude] no ambiguous leads")
        return clear_pass

    batches = [ambiguous[i:i + BATCH_SIZE] for i in range(0, len(ambiguous), BATCH_SIZE)]
    logger.info(
        f"[claude] rescoring {len(ambiguous)} leads in {len(batches)} batches "
        f"({CLAUDE_AMBIGUOUS_MIN} to {CLAUDE_AMBIGUOUS_MAX}) with {CLAUDE_MODEL}"
    )

    for lead in ambiguous:
        lead["_kw_score"] = lead["icp_score"]
        lead["claude_score"] = None

    client = AsyncAnthropic(api_key=api_key)
    sem = asyncio.Semaphore(CLAUDE_CONCURRENCY)
    await asyncio.gather(*[score_batch(client, batch, sem) for batch in batches])

    passed = [lead for lead in ambiguous if lead.get("icp_score", 0) >= CLAUDE_FINAL_THRESHOLD]
    logger.info(f"[claude] {len(ambiguous)} ambiguous, {len(passed)} passed, {len(ambiguous) - len(passed)} dropped")

    return clear_pass + passed
