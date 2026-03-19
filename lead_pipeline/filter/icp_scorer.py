"""
ICP scoring engine.

Each lead is scored on a 0–1 scale using keyword-matching only
(no LLMs, no paid APIs). The score is a weighted sum of positive
and negative signals defined in config.py.

Scoring breakdown
─────────────────
Positive signals (max ~0.95)
  +0.20  ICP industry keyword found in any text field
  +0.20  target location mentioned
  +0.15  lead has a valid website domain
  +0.15  contact indicators detected (phone number or contact form)
  +0.15  booking-intent phrases found ("free estimate", "call now", …)
  +0.10  trust signals ("family owned", "since 20XX", "licensed", …)

Negative penalties
  −0.50  domain is a known directory / marketplace
  −0.40  enterprise language detected ("platform", "global", "corp.", …)
  −0.30  careers-heavy content (2+ career phrases found)

Final score is clamped to [0.0, 1.0].
Only leads with score >= ICP_THRESHOLD are returned.
"""

import re
import logging
from typing import Optional

from config import (
    DIRECTORY_DOMAINS,
    BOOKING_INTENT_PHRASES,
    TRUST_PHRASES,
    ENTERPRISE_SIGNALS,
    DIRECTORY_SIGNALS,
    CAREER_SIGNALS,
    INDUSTRY_KEYWORDS,
    SCORE_WEIGHTS,
    ICP_THRESHOLD,
)
from .dedup import normalize_domain

logger = logging.getLogger(__name__)

# Pre-compile phone regex once at import time
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _combined_text(lead: dict) -> str:
    """Merge all text-bearing fields into a single lowercased string."""
    parts = [
        lead.get("company_name",       ""),
        lead.get("category",           ""),
        lead.get("location",           ""),
        lead.get("page_title",         ""),
        lead.get("page_description",   ""),
        lead.get("page_text",          ""),
        lead.get("_snippet",           ""),  # DDG snippet if present
    ]
    return " ".join(str(p) for p in parts).lower()


def _any_match(text: str, phrases: list[str]) -> bool:
    return any(p in text for p in phrases)


def _count_matches(text: str, phrases: list[str]) -> int:
    return sum(1 for p in phrases if p in text)


def _detect_industry(text: str, input_keywords: list[str]) -> Optional[str]:
    """
    Return the first matching INDUSTRY_KEYWORDS category, or None.
    Checks user-supplied keywords first, then config categories.
    """
    # Fast path: if a user keyword is in the text, attribute its category
    for kw in input_keywords:
        if kw.lower() in text:
            for industry, phrases in INDUSTRY_KEYWORDS.items():
                if any(p in kw.lower() for p in phrases):
                    return industry

    # General category scan
    for industry, phrases in INDUSTRY_KEYWORDS.items():
        if any(p in text for p in phrases):
            return industry

    return None


# ---------------------------------------------------------------------------
# Core scoring function
# ---------------------------------------------------------------------------

def score_lead(
    lead: dict,
    location: str,
    input_keywords: list[str],
) -> tuple[float, list[str]]:
    """
    Compute an ICP score for a single lead.

    Returns:
        (score: float in [0.0, 1.0], reason_tags: list[str])
    """
    text   = _combined_text(lead)
    domain = normalize_domain(lead.get("website", ""))
    score  = 0.0
    tags:  list[str] = []

    # -----------------------------------------------------------------------
    # Negative signals — applied first; can pull score well below zero
    # -----------------------------------------------------------------------

    # Directory / marketplace
    is_dir_domain  = domain and domain in DIRECTORY_DOMAINS
    is_dir_content = _any_match(text, DIRECTORY_SIGNALS)
    if is_dir_domain or is_dir_content:
        score += SCORE_WEIGHTS["directory"]
        tags.append("directory")

    # Enterprise language
    if _any_match(text, ENTERPRISE_SIGNALS):
        score += SCORE_WEIGHTS["enterprise"]
        tags.append("enterprise")

    # Careers-heavy (need 2+ signals to penalise — one "apply now" isn't enough)
    if _count_matches(text, CAREER_SIGNALS) >= 2:
        score += SCORE_WEIGHTS["careers_heavy"]
        tags.append("careers_heavy")

    # -----------------------------------------------------------------------
    # Positive signals
    # -----------------------------------------------------------------------

    # Has a valid website domain
    if domain:
        score += SCORE_WEIGHTS["has_website"]
        tags.append("has_website")

    # ICP industry keyword match
    industry = _detect_industry(text, input_keywords)
    if industry:
        score += SCORE_WEIGHTS["keyword_match"]
        tags.append(f"industry:{industry}")

    # Location mention
    if location.lower() in text:
        score += SCORE_WEIGHTS["location_match"]
        tags.append("location_match")

    # Contact indicators: phone number OR contact-related words
    has_phone   = bool(_PHONE_RE.search(text))
    has_contact = _any_match(
        text,
        ["contact", "call us", "email us", "get in touch", "contact form", "contact us"],
    )
    has_form = lead.get("has_form", False)  # from scanner
    if has_phone or has_contact or has_form:
        score += SCORE_WEIGHTS["has_contact_indicators"]
        tags.append("has_contact")

    # Booking intent
    if _any_match(text, BOOKING_INTENT_PHRASES):
        score += SCORE_WEIGHTS["booking_intent"]
        tags.append("booking_intent")

    # Trust signals
    if _any_match(text, TRUST_PHRASES):
        score += SCORE_WEIGHTS["trust_signals"]
        tags.append("trust_signals")

    score = round(max(0.0, min(1.0, score)), 3)
    return score, tags


# ---------------------------------------------------------------------------
# Batch filter
# ---------------------------------------------------------------------------

def filter_leads(
    leads: list[dict],
    location: str,
    input_keywords: list[str],
) -> list[dict]:
    """
    Score every lead and return those meeting ICP_THRESHOLD.

    Attaches 'domain', 'icp_score', and 'reason_tags' to each passing lead.
    Leads without a website are always dropped here (hard filter).
    """
    passed:  list[dict] = []
    dropped: int        = 0

    for lead in leads:
        domain = normalize_domain(lead.get("website", ""))

        # Hard filter: no website → not useful for Apollo enrichment
        if not domain:
            dropped += 1
            logger.debug(f"[filter] no_website — {lead.get('company_name')}")
            continue

        score, tags = score_lead(lead, location, input_keywords)

        # Attach enriched fields in-place
        lead["domain"]      = domain
        lead["icp_score"]   = score
        lead["reason_tags"] = "|".join(tags)

        if score >= ICP_THRESHOLD:
            passed.append(lead)
            logger.debug(f"[filter] ✓ {lead['company_name']}  score={score}  {tags}")
        else:
            dropped += 1
            logger.debug(f"[filter] ✗ {lead['company_name']}  score={score}")

    logger.info(
        f"[filter] {len(leads)} in → {len(passed)} passed "
        f"/ {dropped} dropped  (threshold={ICP_THRESHOLD})"
    )
    return passed
