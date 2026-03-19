"""
Cross-run domain deduplication.

Persists a JSON file (output/seen_domains.json) that tracks every domain
we've already sent to Apollo. On subsequent runs, these domains are skipped
so Apollo is never billed twice for the same enrichment.

Use --fresh to ignore the seen list for a clean run.
"""

import json
import logging
from datetime import date
from pathlib import Path

from config import SEEN_DOMAINS_FILE

logger = logging.getLogger(__name__)


def load(path: Path = SEEN_DOMAINS_FILE) -> dict[str, str]:
    """Load seen domains from disk. Returns {domain: first_seen_date}."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[seen_domains] could not load {path}: {e}")
        return {}


def save(seen: dict[str, str], path: Path = SEEN_DOMAINS_FILE) -> None:
    """Persist seen domains to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(seen, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[seen_domains] could not save {path}: {e}")


def filter_new(leads: list[dict], fresh: bool = False) -> tuple[list[dict], dict[str, str]]:
    """
    Remove leads whose domain has already been processed.

    Args:
        leads: ICP-qualified leads (must have 'domain' field)
        fresh: if True, ignore the seen list (return all leads)

    Returns:
        (new_leads, seen_dict)  — seen_dict includes both old and new domains
    """
    seen = {} if fresh else load()

    if fresh:
        logger.info("[seen_domains] --fresh: ignoring seen domains")
    else:
        logger.info(f"[seen_domains] {len(seen)} previously seen domains loaded")

    new_leads   = []
    today_str   = str(date.today())
    duplicates  = 0

    for lead in leads:
        domain = (lead.get("domain") or "").strip().lower()
        if not domain:
            continue
        if domain in seen:
            duplicates += 1
            logger.debug(f"[seen_domains] skip (seen {seen[domain]}): {domain}")
        else:
            seen[domain] = today_str
            new_leads.append(lead)

    if duplicates:
        logger.info(
            f"[seen_domains] {duplicates} already-seen domains skipped "
            f"(saves Apollo enrichment cost)"
        )

    logger.info(f"[seen_domains] {len(new_leads)} new domains to export")
    return new_leads, seen
