"""
Deduplication utilities.

Normalizes raw URLs to bare domains, then keeps the most complete
record for each domain. Leads without a website are grouped by
lowercased company name to avoid silently discarding them before
the ICP scorer has a chance to drop them explicitly.

When merging duplicates, the version WITH a website always beats
the version without, regardless of other field completeness.
"""

import logging
import re
from typing import Optional

import tldextract

logger = logging.getLogger(__name__)

# Strip common legal suffixes before name-based dedup
_LEGAL_SUFFIX_RE = re.compile(
    r"\s*(,?\s*(llc|inc|corp|co|ltd|lp|plc|pllc|dba|s\.a\.|p\.a\.)\.?\s*$)",
    re.IGNORECASE,
)


def normalize_domain(url: str) -> str:
    """
    Extract a clean, normalized domain from any URL string.

    Examples:
        "https://www.bestplumbing.com/services" → "bestplumbing.com"
        "http://abc.co.uk"                      → "abc.co.uk"
        "not a url"                             → ""

    Returns empty string if no valid registrable domain is found.
    """
    if not url or not url.strip():
        return ""

    url = url.strip()
    # tldextract handles scheme-less URLs but is more reliable with one
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    ext = tldextract.extract(url)
    if not ext.domain or not ext.suffix:
        return ""

    return f"{ext.domain}.{ext.suffix}".lower()


def _normalize_name(name: str) -> str:
    """Strip legal suffixes and extra whitespace for company-name dedup."""
    return _LEGAL_SUFFIX_RE.sub("", name).strip().lower()


def _completeness(lead: dict) -> int:
    """
    Score a lead by populated fields. Website presence gets extra weight
    so the version with a website always wins when merging duplicates.
    """
    score = 0
    if str(lead.get("website", "")).strip():
        score += 10   # heavy bonus — website is the most valuable field
    for f in ("location", "category", "company_name", "phone", "rating"):
        if str(lead.get(f, "")).strip():
            score += 1
    return score


def deduplicate(leads: list[dict]) -> list[dict]:
    """
    Remove duplicate leads, keeping the most complete record per domain.

    Deduplication key:
      - Leads WITH a website  → normalized domain
      - Leads WITHOUT website → "__nosite__" + normalized company name

    Returns deduplicated list preserving insertion order of first-seen keys.
    """
    by_key: dict[str, dict] = {}

    for lead in leads:
        website = str(lead.get("website", "")).strip()
        domain  = normalize_domain(website)

        if domain:
            key = domain
        else:
            name = _normalize_name(str(lead.get("company_name", "")))
            key  = f"__nosite__{name}"

        if key not in by_key:
            by_key[key] = lead
        else:
            # Prefer the lead with higher completeness (website presence wins)
            if _completeness(lead) > _completeness(by_key[key]):
                by_key[key] = lead

    result = list(by_key.values())
    logger.info(f"[dedup] {len(leads)} → {len(result)} leads")
    return result
