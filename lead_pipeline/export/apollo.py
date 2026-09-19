"""
Write the CSV Apollo's bulk upload takes: company_name and domain, nothing else.

Apollo rejects files with extra columns. Rows are sorted best score first so
a partial upload still gets the strongest leads.
"""

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FIELDNAMES = ["company_name", "domain"]


def export(leads: list[dict], output_path: Path) -> int:
    """Write apollo_ready.csv and return the number of rows."""
    if not leads:
        logger.warning("[apollo] no leads to export")
        return 0

    rows: list[dict] = []
    seen: set[str] = set()

    for lead in sorted(leads, key=lambda l: l.get("icp_score", 0), reverse=True):
        domain = (lead.get("domain") or "").strip().lower()
        if not domain or domain in seen:
            continue
        seen.add(domain)
        rows.append({"company_name": (lead.get("company_name") or "").strip(), "domain": domain})

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"[apollo] {len(rows)} rows written to {output_path}")
    return len(rows)
