"""
ICP scoring engine.

Scoring breakdown (keyword-matching only — no LLMs at this stage)
─────────────────────────────────────────────────────────────────
Positive signals (max ~1.0)
  +0.20  ICP industry keyword found
  +0.20  target location mentioned
  +0.15  has a valid website domain
  +0.15  contact indicators (phone or form)
  +0.15  booking-intent phrases ("free estimate", "call now", …)
  +0.10  trust signals ("family owned", "since 20XX", "licensed", …)
  +0.05  has reviews (social proof from Maps)

Negative penalties
  −0.90  franchise / national chain (hard kill)
  −0.50  directory / marketplace
  −0.40  enterprise language
  −0.30  careers-heavy (2+ career signals)

Final score is clamped to [0.0, 1.0].
Leads in the ambiguous zone (CLAUDE_AMBIGUOUS_MIN–MAX) are re-scored by
Claude in a later stage. Leads below ICP_THRESHOLD are dropped.
"""

import re
import logging
from typing import Optional

from config import (
    DIRECTORY_DOMAINS,
    FRANCHISE_DOMAINS,
    FRANCHISE_NAME_SIGNALS,
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

_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")

# Pre-compiled word-boundary patterns for location matching
_LOCATION_RE_CACHE: dict[str, re.Pattern] = {}

# Rating/review: extract first decimal number from strings like "4.5" or "4 (1,234)"
_RATING_RE  = re.compile(r"(\d+\.?\d*)")
_REVIEW_RE  = re.compile(r"([\d,]+)")


def _location_re(location: str) -> re.Pattern:
    if location not in _LOCATION_RE_CACHE:
        _LOCATION_RE_CACHE[location] = re.compile(
            r"\b" + re.escape(location) + r"\b", re.IGNORECASE
        )
    return _LOCATION_RE_CACHE[location]


def _combined_text(lead: dict) -> str:
    parts = [
        lead.get("company_name",     ""),
        lead.get("category",         ""),
        lead.get("location",         ""),
        lead.get("page_title",       ""),
        lead.get("page_description", ""),
        lead.get("page_text",        ""),
        lead.get("_snippet",         ""),
    ]
    return " ".join(str(p) for p in parts).lower()


def _any_match(text: str, phrases: list[str]) -> bool:
    return any(p in text for p in phrases)


def _count_matches(text: str, phrases: list[str]) -> int:
    return sum(1 for p in phrases if p in text)


def _is_franchise(domain: str, text: str) -> bool:
    """Return True for known national chains/franchises."""
    if domain and domain in FRANCHISE_DOMAINS:
        return True
    return _any_match(text, FRANCHISE_NAME_SIGNALS)


def _detect_industry(text: str, input_keywords: list[str]) -> Optional[str]:
    """
    Return the best-matching industry label or None.
    Checks user-supplied keywords first, then scans all industry phrase lists.
    """
    # Check if any user keyword directly matches an industry phrase list
    for kw in input_keywords:
        kw_lower = kw.lower()
        for industry, phrases in INDUSTRY_KEYWORDS.items():
            if any(p == kw_lower or kw_lower in p or p in kw_lower for p in phrases):
                return industry
    # Fallback: scan combined text for any industry phrase
    for industry, phrases in INDUSTRY_KEYWORDS.items():
        if any(p in text for p in phrases):
            return industry
    return None


def _parse_rating(rating_str: str) -> Optional[float]:
    """Parse rating from strings like '4.5', '4 (1,234 reviews)', '★★★★☆ 4.5'."""
    if not rating_str:
        return None
    m = _RATING_RE.search(rating_str)
    return float(m.group(1)) if m else None


def _parse_review_count(review_str: str) -> int:
    """Parse review count from strings like '(42)', '1,234', '42K'."""
    if not review_str:
        return 0
    # Handle 'K' suffix: "2K" → 2000
    review_str = review_str.strip().upper()
    if review_str.endswith("K"):
        m = _RATING_RE.search(review_str)
        return int(float(m.group(1)) * 1000) if m else 0
    m = _REVIEW_RE.search(review_str)
    return int(m.group(1).replace(",", "")) if m else 0


def score_lead(
    lead: dict,
    location: str,
    input_keywords: list[str],
) -> tuple[float, list[str]]:
    """
    Compute a keyword-based ICP score for a single lead.
    Returns (score: float in [0.0, 1.0], reason_tags: list[str]).
    """
    text   = _combined_text(lead)
    domain = normalize_domain(lead.get("website", ""))
    score  = 0.0
    tags:  list[str] = []

    # ── Negative signals (applied first; franchise is a hard kill) ──────────

    if _is_franchise(domain, text):
        score += SCORE_WEIGHTS["franchise"]
        tags.append("franchise")

    is_dir = (domain and domain in DIRECTORY_DOMAINS) or _any_match(text, DIRECTORY_SIGNALS)
    if is_dir:
        score += SCORE_WEIGHTS["directory"]
        tags.append("directory")

    if _any_match(text, ENTERPRISE_SIGNALS):
        score += SCORE_WEIGHTS["enterprise"]
        tags.append("enterprise")

    if _count_matches(text, CAREER_SIGNALS) >= 2:
        score += SCORE_WEIGHTS["careers_heavy"]
        tags.append("careers_heavy")

    # Early exit: if score is already disqualifying, skip positive signals
    if score <= -0.70:
        return round(max(0.0, min(1.0, score)), 3), tags

    # ── Positive signals ─────────────────────────────────────────────────────

    if domain:
        score += SCORE_WEIGHTS["has_website"]
        tags.append("has_website")

    industry = _detect_industry(text, input_keywords)
    if industry:
        score += SCORE_WEIGHTS["keyword_match"]
        tags.append(f"industry:{industry}")

    # Word-boundary location match (prevents "Utahans", "Utahorum", etc.)
    if _location_re(location).search(text):
        score += SCORE_WEIGHTS["location_match"]
        tags.append("location_match")

    has_phone = bool(_PHONE_RE.search(text)) or bool(lead.get("phone"))
    has_contact = _any_match(text, [
        "contact", "call us", "email us", "get in touch", "contact us",
        "live chat", "whatsapp", "contact form",
    ])
    has_form = lead.get("has_form", False)
    if has_phone or has_contact or has_form:
        score += SCORE_WEIGHTS["has_contact_indicators"]
        tags.append("has_contact")

    if _any_match(text, BOOKING_INTENT_PHRASES):
        score += SCORE_WEIGHTS["booking_intent"]
        tags.append("booking_intent")

    if _any_match(text, TRUST_PHRASES):
        score += SCORE_WEIGHTS["trust_signals"]
        tags.append("trust_signals")

    # Reviews from Maps — differentiates within the top band
    rating_str   = str(lead.get("rating", "")).strip()
    review_str   = str(lead.get("review_count", "")).strip()
    rating_val   = _parse_rating(rating_str)
    review_count = _parse_review_count(review_str)

    if rating_val is not None and rating_val >= 4.0 and review_count >= 10:
        score += 0.05
        tags.append(f"rated:{rating_val:.1f}({review_count})")

    score = round(max(0.0, min(1.0, score)), 3)
    return score, tags


def filter_leads(
    leads: list[dict],
    location: str,
    input_keywords: list[str],
) -> list[dict]:
    """
    Score every lead. Drop those below ICP_THRESHOLD.
    Attaches domain, icp_score, reason_tags to each passing lead.
    """
    passed:  list[dict] = []
    dropped: int        = 0

    for lead in leads:
        domain = normalize_domain(lead.get("website", ""))

        if not domain:
            dropped += 1
            logger.debug(f"[filter] no_website — {lead.get('company_name')}")
            continue

        score, tags = score_lead(lead, location, input_keywords)

        lead["domain"]       = domain
        lead["icp_score"]    = score
        lead["claude_score"] = None   # populated later if Claude rescores
        lead["reason_tags"]  = "|".join(tags)

        if score >= ICP_THRESHOLD:
            passed.append(lead)
        else:
            dropped += 1
            logger.debug(f"[filter] ✗ {lead['company_name']}  score={score}")

    logger.info(
        f"[filter] {len(leads)} in → {len(passed)} passed "
        f"/ {dropped} dropped  (threshold={ICP_THRESHOLD})"
    )
    return passed
