"""
Outreach prep CSV exporter.

Produces a richer CSV for use in outreach tools (Instantly, Lemlist, etc.):
    company_name, domain, icp_score, reason_tags

Sorted by icp_score descending so the highest-confidence leads appear
first — easy to prioritise or slice the top-N for a first batch.
"""

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_FIELDNAMES = ["company_name", "domain", "icp_score", "reason_tags"]


def export(leads: list[dict], output_path: Path) -> int:
    """
    Write outreach_ready.csv.

    Args:
        leads:       filtered lead dicts (must have the four outreach fields)
        output_path: destination Path

    Returns:
        Number of rows written.
    """
    if not leads:
        logger.warning("[outreach] no leads to export")
        return 0

    rows: list[dict] = []
    seen:  set[str]  = set()

    for lead in leads:
        domain = (lead.get("domain") or "").strip().lower()
        name   = (lead.get("company_name") or "").strip()

        if not domain or domain in seen:
            continue
        seen.add(domain)

        rows.append({
            "company_name": name,
            "domain":       domain,
            "icp_score":    round(float(lead.get("icp_score", 0.0)), 3),
            "reason_tags":  (lead.get("reason_tags") or "").strip(),
        })

    # Best leads first
    rows.sort(key=lambda r: r["icp_score"], reverse=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"[outreach] {len(rows)} rows → {output_path}")
    return len(rows)
