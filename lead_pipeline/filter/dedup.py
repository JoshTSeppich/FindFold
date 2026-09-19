"""
Remove duplicate leads within one run.

Leads with a website are keyed by bare domain. Leads without one are keyed
by company name so they survive to the scorer, which drops them with a
reason instead of losing them silently here. When two records share a key,
the one with a website wins, then the one with more fields filled in.
"""

import logging
import re

import tldextract

logger = logging.getLogger(__name__)

LEGAL_SUFFIX_RE = re.compile(
    r"\s*(,?\s*(llc|inc|corp|co|ltd|lp|plc|pllc|dba|s\.a\.|p\.a\.)\.?\s*$)",
    re.IGNORECASE,
)


def normalize_domain(url: str) -> str:
    """Reduce any URL to its registrable domain, or "" if there is none.

    "https://www.bestplumbing.com/services" gives "bestplumbing.com".
    "http://abc.co.uk" gives "abc.co.uk".
    """
    if not url or not url.strip():
        return ""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    ext = tldextract.extract(url)
    if not ext.domain or not ext.suffix:
        return ""
    return f"{ext.domain}.{ext.suffix}".lower()


def normalize_name(name: str) -> str:
    return LEGAL_SUFFIX_RE.sub("", name).strip().lower()


def completeness(lead: dict) -> int:
    """Count filled fields. A website counts for ten so it always wins."""
    score = 0
    if str(lead.get("website", "")).strip():
        score += 10
    for f in ("location", "category", "company_name", "phone", "rating"):
        if str(lead.get(f, "")).strip():
            score += 1
    return score


def deduplicate(leads: list[dict]) -> list[dict]:
    """Keep one record per domain, or per company name when there is no domain."""
    by_key: dict[str, dict] = {}

    for lead in leads:
        domain = normalize_domain(str(lead.get("website", "")).strip())
        key = domain or "__nosite__" + normalize_name(str(lead.get("company_name", "")))

        if key not in by_key or completeness(lead) > completeness(by_key[key]):
            by_key[key] = lead

    result = list(by_key.values())
    logger.info(f"[dedup] {len(leads)} in, {len(result)} out")
    return result
