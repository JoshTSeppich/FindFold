"""
Remember which domains have already gone to Apollo.

output/seen_domains.json maps each domain to the date it was first exported.
Later runs skip those domains so Apollo never bills the same enrichment twice.
--fresh ignores the file. --unseen-older-than N lets domains back in after N days.
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
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[seen_domains] could not load {path}: {e}")
        return {}


def save(seen: dict[str, str], path: Path = SEEN_DOMAINS_FILE) -> None:
    """Write to a temp file and rename, so a crash mid-write cannot corrupt the list."""
    if not seen:
        return
    clean = {k: v for k, v in seen.items() if k and k.strip()}
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(clean, indent=2, sort_keys=True))
            os.replace(tmp, path)
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
    """Drop leads whose domain was exported before.

    Returns the new leads and the updated seen map, which includes the new
    domains stamped with today. The caller saves the map once the exports
    are on disk.
    """
    if fresh:
        seen: dict[str, str] = {}
        logger.info("[seen_domains] --fresh, ignoring seen domains")
    else:
        seen = load()
        logger.info(f"[seen_domains] {len(seen)} domains seen before")

    if unseen_older_than > 0 and seen:
        cutoff = str(date.today() - timedelta(days=unseen_older_than))
        before = len(seen)
        seen = {domain: dt for domain, dt in seen.items() if dt >= cutoff}
        if before - len(seen):
            logger.info(f"[seen_domains] {before - len(seen)} domains older than {unseen_older_than} days let back in")

    new_leads: list[dict] = []
    today = str(date.today())
    skipped = 0

    for lead in leads:
        domain = (lead.get("domain") or "").strip().lower()
        if not domain:
            continue
        if domain in seen:
            skipped += 1
            logger.debug(f"[seen_domains] skip {domain}, seen {seen[domain]}")
        else:
            seen[domain] = today
            new_leads.append(lead)

    if skipped:
        logger.info(f"[seen_domains] {skipped} domains skipped, already sent to Apollo")
    logger.info(f"[seen_domains] {len(new_leads)} new domains")
    return new_leads, seen
