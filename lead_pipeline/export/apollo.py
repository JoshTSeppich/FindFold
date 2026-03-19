"""
Apollo enrichment CSV exporter.

Produces the minimal two-column CSV that Apollo's bulk upload expects:
    company_name, domain

One row per unique domain, alphabetically sorted, clean formatting.
No extra columns — Apollo rejects files with unexpected headers.
"""

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_FIELDNAMES = ["company_name", "domain"]


def export(leads: list[dict], output_path: Path) -> int:
    """
    Write apollo_ready.csv.

    Args:
        leads:       filtered lead dicts (must have 'company_name' and 'domain')
        output_path: destination Path

    Returns:
        Number of rows written.
    """
    if not leads:
        logger.warning("[apollo] no leads to export")
        return 0

    rows: list[dict] = []
    seen:  set[str]  = set()

    for lead in leads:
        domain = (lead.get("domain") or "").strip().lower()
        name   = (lead.get("company_name") or "").strip()

        if not domain or domain in seen:
            continue
        seen.add(domain)

        rows.append({"company_name": name, "domain": domain})

    # Alphabetical by company name for human readability
    rows.sort(key=lambda r: r["company_name"].lower())

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"[apollo] {len(rows)} rows → {output_path}")
    return len(rows)
