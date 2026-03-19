"""
Deduplication utilities.

Normalizes raw URLs to bare domains, then keeps the most complete
record for each domain. Leads without a website are grouped by
lowercased company name to avoid silently discarding them before
the ICP scorer has a chance to drop them explicitly.
"""

import logging
from typing import Optional

import tldextract

logger = logging.getLogger(__name__)


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


def _completeness(lead: dict) -> int:
    """Score a lead by number of non-empty fields (higher = keep this one)."""
    fields = ["company_name", "website", "location", "category"]
    return sum(1 for f in fields if str(lead.get(f, "")).strip())


def deduplicate(leads: list[dict]) -> list[dict]:
    """
    Remove duplicate leads, keeping the most complete record per domain.

    Deduplication key:
      - Leads WITH a website  → normalized domain
      - Leads WITHOUT website → "__nosite__" + lowercased company name

    Returns deduplicated list preserving insertion order of first-seen keys.
    """
    by_key: dict[str, dict] = {}

    for lead in leads:
        website = str(lead.get("website", "")).strip()
        domain  = normalize_domain(website)

        if domain:
            key = domain
        else:
            name = str(lead.get("company_name", "")).lower().strip()
            key  = f"__nosite__{name}"

        if key not in by_key:
            by_key[key] = lead
        else:
            if _completeness(lead) > _completeness(by_key[key]):
                by_key[key] = lead

    result = list(by_key.values())
    logger.info(f"[dedup] {len(leads)} → {len(result)} leads")
    return result
