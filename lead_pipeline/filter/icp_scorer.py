"""
Keyword scoring. No model calls here.

Each lead gets a score from 0 to 1 built from the weights in config.py:

  +0.20  industry keyword found
  +0.20  target location mentioned
  +0.15  has a website domain
  +0.15  phone number, contact page, or contact form
  +0.15  booking phrases like "free estimate" or "call now"
  +0.10  trust phrases like "family owned" or "licensed"
  +0.05  rated 4.0 or better with at least ten reviews

  -0.90  franchise or national chain
  -0.50  directory or marketplace
  -0.40  enterprise language
  -0.30  two or more careers phrases

I run the penalties first. If they already sink the lead, the positives are
skipped. The result is clamped to 0 to 1 and rounded to three places.
"""

import logging
import re
from typing import Optional

from config import (
    BOOKING_INTENT_PHRASES,
    CAREER_SIGNALS,
    DIRECTORY_DOMAINS,
    DIRECTORY_SIGNALS,
    ENTERPRISE_SIGNALS,
    FRANCHISE_DOMAINS,
    FRANCHISE_NAME_SIGNALS,
    ICP_THRESHOLD,
    INDUSTRY_KEYWORDS,
    SCORE_WEIGHTS,
    TRUST_PHRASES,
)
from .dedup import normalize_domain

logger = logging.getLogger(__name__)

PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
NUMBER_RE = re.compile(r"(\d+\.?\d*)")
COUNT_RE = re.compile(r"([\d,]+)")

CONTACT_PHRASES = [
    "contact", "call us", "email us", "get in touch", "contact us",
    "live chat", "whatsapp", "contact form",
]

# One compiled pattern per location string, built on first use.
_location_patterns: dict[str, re.Pattern] = {}


def location_pattern(location: str) -> re.Pattern:
    """Whole-word match, so "Utah" does not match "Utahns"."""
    if location not in _location_patterns:
        _location_patterns[location] = re.compile(r"\b" + re.escape(location) + r"\b", re.IGNORECASE)
    return _location_patterns[location]


def combined_text(lead: dict) -> str:
    parts = [
        lead.get("company_name", ""),
        lead.get("category", ""),
        lead.get("location", ""),
        lead.get("page_title", ""),
        lead.get("page_description", ""),
        lead.get("page_text", ""),
        lead.get("_snippet", ""),
    ]
    return " ".join(str(p) for p in parts).lower()


def any_match(text: str, phrases: list[str]) -> bool:
    return any(p in text for p in phrases)


def count_matches(text: str, phrases: list[str]) -> int:
    return sum(1 for p in phrases if p in text)


def is_franchise(domain: str, text: str) -> bool:
    if domain and domain in FRANCHISE_DOMAINS:
        return True
    return any_match(text, FRANCHISE_NAME_SIGNALS)


def detect_industry(text: str, input_keywords: list[str]) -> Optional[str]:
    """Return the industry group that matches, or None.

    The search keywords are checked first because they tell me what I was
    looking for. The page text is the fallback.
    """
    for kw in input_keywords:
        kw_lower = kw.lower()
        for industry, phrases in INDUSTRY_KEYWORDS.items():
            if any(p == kw_lower or kw_lower in p or p in kw_lower for p in phrases):
                return industry
    for industry, phrases in INDUSTRY_KEYWORDS.items():
        if any(p in text for p in phrases):
            return industry
    return None


def parse_rating(rating_str: str) -> Optional[float]:
    """Pull the number out of strings like "4.5" or "4 (1,234 reviews)"."""
    if not rating_str:
        return None
    m = NUMBER_RE.search(rating_str)
    return float(m.group(1)) if m else None


def parse_review_count(review_str: str) -> int:
    """Pull the count out of strings like "(42)", "1,234", or "2K"."""
    if not review_str:
        return 0
    review_str = review_str.strip().upper()
    if review_str.endswith("K"):
        m = NUMBER_RE.search(review_str)
        return int(float(m.group(1)) * 1000) if m else 0
    m = COUNT_RE.search(review_str)
    return int(m.group(1).replace(",", "")) if m else 0


def score_lead(lead: dict, location: str, input_keywords: list[str]) -> tuple[float, list[str]]:
    """Score one lead. Returns the score and the list of reasons behind it."""
    text = combined_text(lead)
    domain = normalize_domain(lead.get("website", ""))
    score = 0.0
    tags: list[str] = []

    # Penalties first.
    if is_franchise(domain, text):
        score += SCORE_WEIGHTS["franchise"]
        tags.append("franchise")

    if (domain and domain in DIRECTORY_DOMAINS) or any_match(text, DIRECTORY_SIGNALS):
        score += SCORE_WEIGHTS["directory"]
        tags.append("directory")

    if any_match(text, ENTERPRISE_SIGNALS):
        score += SCORE_WEIGHTS["enterprise"]
        tags.append("enterprise")

    if count_matches(text, CAREER_SIGNALS) >= 2:
        score += SCORE_WEIGHTS["careers_heavy"]
        tags.append("careers_heavy")

    # Nothing below can lift a lead this far down, so stop here.
    if score <= -0.70:
        return round(max(0.0, min(1.0, score)), 3), tags

    # Positives.
    if domain:
        score += SCORE_WEIGHTS["has_website"]
        tags.append("has_website")

    industry = detect_industry(text, input_keywords)
    if industry:
        score += SCORE_WEIGHTS["keyword_match"]
        tags.append(f"industry:{industry}")

    if location_pattern(location).search(text):
        score += SCORE_WEIGHTS["location_match"]
        tags.append("location_match")

    has_phone = bool(PHONE_RE.search(text)) or bool(lead.get("phone"))
    if has_phone or any_match(text, CONTACT_PHRASES) or lead.get("has_form", False):
        score += SCORE_WEIGHTS["has_contact_indicators"]
        tags.append("has_contact")

    if any_match(text, BOOKING_INTENT_PHRASES):
        score += SCORE_WEIGHTS["booking_intent"]
        tags.append("booking_intent")

    if any_match(text, TRUST_PHRASES):
        score += SCORE_WEIGHTS["trust_signals"]
        tags.append("trust_signals")

    # Reviews from Maps separate the good leads at the top of the band.
    rating = parse_rating(str(lead.get("rating", "")).strip())
    reviews = parse_review_count(str(lead.get("review_count", "")).strip())
    if rating is not None and rating >= 4.0 and reviews >= 10:
        score += 0.05
        tags.append(f"rated:{rating:.1f}({reviews})")

    return round(max(0.0, min(1.0, score)), 3), tags


def filter_leads(
    leads: list[dict],
    location: str,
    input_keywords: list[str],
    threshold: float = ICP_THRESHOLD,
) -> list[dict]:
    """Score every lead and keep the ones at or above the threshold.

    Leads with no website are dropped before scoring. Each kept lead gets
    domain, icp_score, claude_score, and reason_tags fields.
    """
    passed: list[dict] = []
    dropped = 0

    for lead in leads:
        domain = normalize_domain(lead.get("website", ""))
        if not domain:
            dropped += 1
            logger.debug(f"[filter] no website: {lead.get('company_name')}")
            continue

        score, tags = score_lead(lead, location, input_keywords)

        lead["domain"] = domain
        lead["icp_score"] = score
        lead["claude_score"] = None
        lead["reason_tags"] = "|".join(tags)

        if score >= threshold:
            passed.append(lead)
        else:
            dropped += 1
            logger.debug(f"[filter] dropped {lead['company_name']} at {score}")

    logger.info(f"[filter] {len(leads)} in, {len(passed)} passed, {dropped} dropped, threshold {threshold}")
    return passed
