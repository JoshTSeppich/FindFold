"""
Cross-run domain deduplication.

Persists a JSON file (output/seen_domains.json) that tracks every domain
we've already sent to Apollo. On subsequent runs, these domains are skipped
so Apollo is never billed twice for the same enrichment.

Use --fresh to ignore the seen list for a clean run.
Use --unseen-older-than N to re-process domains first seen more than N days ago.
"""

import json
import logging
import os
import tempfile
from datetime import date, timedelta
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
    """
    Atomically persist seen domains to disk.
    Writes to a temp file then renames to avoid corruption on partial writes.
    """
    if not seen:
        return
    # Drop invalid (empty) keys before saving
    clean = {k: v for k, v in seen.items() if k and k.strip()}
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(clean, indent=2, sort_keys=True))
            os.replace(tmp, path)   # atomic on POSIX and Windows 10+
        except Exception:
            os.unlink(tmp)
            raise
    except Exception as e:
        logger.warning(f"[seen_domains] could not save {path}: {e}")


def filter_new(
    leads: list[dict],
    fresh: bool = False,
    unseen_older_than: int = 0,
) -> tuple[list[dict], dict[str, str]]:
    """
    Remove leads whose domain has already been processed.

    Args:
        leads:             ICP-qualified leads (must have 'domain' field)
        fresh:             if True, ignore the seen list (return all leads)
        unseen_older_than: if > 0, re-admit domains first seen more than this
                           many days ago (useful for periodic re-enrichment)

    Returns:
        (new_leads, seen_dict)  — seen_dict includes both old and new domains
    """
    if fresh:
        seen: dict[str, str] = {}
        logger.info("[seen_domains] --fresh: ignoring seen domains")
    else:
        seen = load()
        logger.info(f"[seen_domains] {len(seen)} previously seen domains loaded")

    # Apply expiry if requested
    if unseen_older_than > 0 and seen:
        cutoff     = date.today() - timedelta(days=unseen_older_than)
        cutoff_str = str(cutoff)
        before     = len(seen)
        seen = {
            domain: dt for domain, dt in seen.items()
            if dt >= cutoff_str
        }
        removed = before - len(seen)
        if removed:
            logger.info(
                f"[seen_domains] {removed} domains expired "
                f"(older than {unseen_older_than} days)"
            )

    new_leads:  list[dict] = []
    today_str:  str        = str(date.today())
    duplicates: int        = 0

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
