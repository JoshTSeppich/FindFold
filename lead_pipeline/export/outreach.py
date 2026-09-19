"""
Write the full CSV for outreach tools: scores, tags, and every contact field we found.

Rows are sorted best score first so the top of the file is the first batch.
"""

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FIELDNAMES = [
    "company_name", "domain", "icp_score", "claude_score",
    "reason_tags", "phone", "email", "rating", "review_count",
    "location", "category",
]


def export(leads: list[dict], output_path: Path) -> int:
    """Write outreach_ready.csv and return the number of rows."""
    if not leads:
        logger.warning("[outreach] no leads to export")
        return 0

    rows: list[dict] = []
    seen: set[str] = set()

    for lead in leads:
        domain = (lead.get("domain") or "").strip().lower()
        if not domain or domain in seen:
            continue
        seen.add(domain)

        claude_score = lead.get("claude_score")
        rows.append({
            "company_name": (lead.get("company_name") or "").strip(),
            "domain": domain,
            "icp_score": round(float(lead.get("icp_score", 0.0)), 3),
            "claude_score": round(float(claude_score), 3) if claude_score is not None else "",
            "reason_tags": (lead.get("reason_tags") or "").strip(),
            "phone": (lead.get("phone") or "").strip(),
            "email": (lead.get("email") or "").strip(),
            "rating": (lead.get("rating") or "").strip(),
            "review_count": (lead.get("review_count") or "").strip(),
            "location": (lead.get("location") or "").strip(),
            "category": (lead.get("category") or "").strip(),
        })

    rows.sort(key=lambda r: r["icp_score"], reverse=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"[outreach] {len(rows)} rows written to {output_path}")
    return len(rows)
