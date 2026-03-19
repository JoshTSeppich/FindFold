"""
FoxWorks Lead Pipeline — CLI entry point.

Usage:
    python main.py --keywords "plumbing,HVAC,roofing,med spa" \
                   --location "Utah" \
                   --limit 300

    # Multiple cities (parallel scraping):
    python main.py --keywords "HVAC,roofing,med spa" \
                   --cities "Salt Lake City Utah,Provo Utah,Ogden Utah" \
                   --limit 100

    # Skip Google Maps (faster, no Playwright needed):
    python main.py --keywords "plumbing,HVAC" --location "Utah" --no-maps

    # Clean run — ignore cross-run dedup:
    python main.py --keywords "plumbing,HVAC" --location "Utah" --fresh

    # Re-process domains seen more than 90 days ago:
    python main.py --keywords "plumbing,HVAC" --location "Utah" \
                   --unseen-older-than 90

Outputs written to ./output/:
    raw_leads.csv       — everything scraped, unfiltered
    filtered_leads.csv  — ICP-scored leads above threshold
    apollo_ready.csv    — minimal 2-column CSV for Apollo bulk enrichment
    outreach_ready.csv  — full scoring data for outreach sequences
"""

import asyncio
import csv
import logging
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from rich import box

load_dotenv()

import config
from lead_pipeline.scraper  import scrape_google_maps, scrape_duckduckgo
from lead_pipeline.filter   import deduplicate, filter_leads, rescore_ambiguous, filter_new, save_seen
from lead_pipeline.scanner  import fetch_many, extract
from lead_pipeline.export   import export_apollo, export_outreach

# ---------------------------------------------------------------------------
# Console + logging
# ---------------------------------------------------------------------------

console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(console=console, show_path=False, markup=True)],
)
logger = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

_RAW_FIELDS = [
    "company_name", "website", "location", "category", "source",
    "phone", "rating", "review_count",
]

_FILTERED_FIELDS = [
    "company_name", "domain", "location", "category",
    "icp_score", "claude_score", "reason_tags",
    "phone", "email", "rating", "review_count",
    "page_title", "page_description",
]


def _save_csv(rows: list[dict], path: Path, fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Rich summary table (top-20 leads)
# ---------------------------------------------------------------------------

def _print_table(leads: list[dict]) -> None:
    top = sorted(leads, key=lambda x: x.get("icp_score", 0), reverse=True)[:20]

    table = Table(
        title="Top ICP Leads",
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Company",  max_width=32, no_wrap=True)
    table.add_column("Domain",   max_width=26, no_wrap=True)
    table.add_column("Score",    justify="right", style="bold green", width=6)
    table.add_column("Claude",   justify="right", style="bold yellow", width=7)
    table.add_column("Tags",     max_width=38)

    for lead in top:
        claude_score = lead.get("claude_score")
        claude_str   = f"{claude_score:.2f}" if claude_score is not None else "—"
        table.add_row(
            lead.get("company_name", "")[:32],
            lead.get("domain",       "")[:26],
            f"{lead.get('icp_score', 0):.2f}",
            claude_str,
            lead.get("reason_tags",  ""),
        )

    console.print()
    console.print(table)


# ---------------------------------------------------------------------------
# Scraping — supports multiple cities
# ---------------------------------------------------------------------------

async def _scrape_city(
    keywords: list[str],
    location: str,
    limit: int,
    use_maps: bool,
) -> list[dict]:
    """Scrape one city. Falls back to Bing if Maps fails."""
    if use_maps:
        try:
            return await scrape_google_maps(keywords, location, limit)
        except Exception as e:
            logger.warning(f"[{location}] Maps failed ({e}). Falling back to Bing.")
    return await scrape_duckduckgo(keywords, location, limit)


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

async def run_pipeline(
    keywords:           list[str],
    locations:          list[str],
    limit:              int,
    use_maps:           bool,
    fresh:              bool,
    unseen_older_than:  int,
) -> None:

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ── Stage 1: Scrape ───────────────────────────────────────────────────
    console.rule("[bold blue]Stage 1 · Scraping")

    per_city_limit = max(1, limit // len(locations))
    src_label = "Bing only" if not use_maps else "Google Maps (Bing fallback)"
    console.print(
        f"  Source   : {src_label}\n"
        f"  Cities   : {len(locations)}\n"
        f"  Per-city : {per_city_limit} raw leads"
    )

    # Cities run sequentially to avoid Google Maps rate-limiting from
    # multiple parallel Playwright browser instances on the same machine.
    # Bing/no-maps mode can be parallelised safely if needed.
    raw_leads: list[dict] = []
    for loc in locations:
        batch = await _scrape_city(keywords, loc, per_city_limit, use_maps)
        raw_leads.extend(batch)

    if not raw_leads:
        console.print("[bold red]No raw leads scraped. Check your keywords/location.")
        sys.exit(1)

    _save_csv(raw_leads, config.RAW_OUTPUT, _RAW_FIELDS)
    console.print(f"  [green]✓[/] {len(raw_leads)} raw leads → [dim]{config.RAW_OUTPUT}[/]")

    # ── Stage 2: Deduplicate ──────────────────────────────────────────────
    console.rule("[bold blue]Stage 2 · Deduplication")

    deduped = deduplicate(raw_leads)
    console.print(f"  [green]✓[/] {len(raw_leads)} → {len(deduped)} after dedup")

    # ── Stage 3: Website scanning ─────────────────────────────────────────
    console.rule("[bold blue]Stage 3 · Website Scanning")

    scannable = [
        lead["website"]
        for lead in deduped
        if (lead.get("website") or "").strip()
    ]
    console.print(
        f"  Scanning {len(scannable)} sites "
        f"(concurrency={config.CONCURRENT_REQUESTS}) …"
    )

    html_map = await fetch_many(scannable, concurrency=config.CONCURRENT_REQUESTS)

    scanned = 0
    for lead in deduped:
        url = (lead.get("website") or "").strip()
        html = html_map.get(url)
        if html:
            lead.update(extract(html))
            scanned += 1

    console.print(f"  [green]✓[/] {scanned}/{len(scannable)} pages scanned")

    # ── Stage 4: ICP scoring + filtering ─────────────────────────────────
    console.rule("[bold blue]Stage 4 · ICP Scoring & Filtering")

    # Use the shared geographic term across all locations for scoring
    # e.g. ["Salt Lake City Utah", "Provo Utah"] → "Utah"
    if len(locations) == 1:
        score_location = locations[0]
    else:
        words_sets = [set(loc.split()) for loc in locations]
        common = words_sets[0].intersection(*words_sets[1:])
        score_location = " ".join(w for w in locations[0].split() if w in common) or locations[0]

    filtered = filter_leads(deduped, score_location, keywords)
    console.print(
        f"  [green]✓[/] {len(filtered)} keyword-qualified leads "
        f"(≥{config.ICP_THRESHOLD})"
    )

    # ── Stage 4b: Claude ambiguous-zone rescoring ─────────────────────────
    console.rule("[bold blue]Stage 4b · Claude Rescoring")

    ambiguous_count = sum(
        1 for l in filtered
        if config.CLAUDE_AMBIGUOUS_MIN <= l.get("icp_score", 0) <= config.CLAUDE_AMBIGUOUS_MAX
    )
    if ambiguous_count:
        console.print(f"  Rescoring {ambiguous_count} ambiguous leads with Claude …")
        filtered = await rescore_ambiguous(filtered)
        console.print(f"  [green]✓[/] {len(filtered)} leads after Claude rescoring")
    else:
        console.print("  No ambiguous leads — Claude rescoring skipped")

    # ── Stage 4c: Cross-run domain dedup ─────────────────────────────────
    console.rule("[bold blue]Stage 4c · Cross-Run Deduplication")

    filtered, seen_dict = filter_new(
        filtered,
        fresh=fresh,
        unseen_older_than=unseen_older_than,
    )
    console.print(f"  [green]✓[/] {len(filtered)} new domains (not previously sent to Apollo)")

    _save_csv(filtered, config.FILTERED_OUTPUT, _FILTERED_FIELDS)
    console.print(f"  [dim]→ {config.FILTERED_OUTPUT}[/]")

    # ── Stage 5: Export ───────────────────────────────────────────────────
    console.rule("[bold blue]Stage 5 · Export")

    apollo_n   = export_apollo(filtered,   config.APOLLO_OUTPUT)
    outreach_n = export_outreach(filtered, config.OUTREACH_OUTPUT)

    # Save seen domains ONLY after both exports succeed.
    # This prevents leads from being permanently skipped if an export fails.
    if apollo_n > 0 and outreach_n > 0:
        save_seen(seen_dict)
        console.print(f"  [green]✓[/] Seen domains persisted ({len(seen_dict)} total)")
    elif filtered:
        console.print(
            "[yellow]  ⚠ Export produced 0 rows — seen domains NOT saved "
            "(leads will be re-processed next run)[/]"
        )

    console.print(f"  [green]✓[/] Apollo:   {apollo_n} rows → [dim]{config.APOLLO_OUTPUT}[/]")
    console.print(f"  [green]✓[/] Outreach: {outreach_n} rows → [dim]{config.OUTREACH_OUTPUT}[/]")

    # ── Summary ───────────────────────────────────────────────────────────
    console.rule("[bold green]Pipeline Complete")

    _print_table(filtered)

    console.print()
    console.print(f"  Raw scraped:    [bold]{len(raw_leads)}[/]")
    console.print(f"  After dedup:    [bold]{len(deduped)}[/]")
    console.print(f"  Pages scanned:  [bold]{scanned}[/]")
    console.print(f"  ICP qualified:  [bold cyan]{len(filtered)}[/]")

    if filtered:
        ratio = len(raw_leads) / len(filtered)
        console.print(f"  Filter ratio:   [bold]{ratio:.1f}x[/] (raw → qualified)")
    console.print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option(
    "--keywords", required=True,
    help='Comma-separated ICP keywords. E.g. "plumbing,HVAC,roofing,med spa"',
)
@click.option(
    "--location", default="",
    help='Single target market. E.g. "Salt Lake City Utah". Ignored if --cities is set.',
)
@click.option(
    "--cities", default="",
    help='Comma-separated cities to scrape. E.g. "Salt Lake City Utah,Provo Utah"',
)
@click.option(
    "--limit", default=300, show_default=True,
    help="Max raw leads to scrape (split evenly across cities).",
)
@click.option(
    "--no-maps", is_flag=True, default=False,
    help="Skip Google Maps; use Bing only (faster, no Playwright needed).",
)
@click.option(
    "--fresh", is_flag=True, default=False,
    help="Ignore seen-domains list — re-process all leads (clean run).",
)
@click.option(
    "--unseen-older-than", "unseen_older_than", default=0, show_default=True,
    help="Re-process domains first seen more than N days ago (0 = never expire).",
)
@click.option(
    "--debug", is_flag=True, default=False,
    help="Enable DEBUG-level logging.",
)
def main(
    keywords: str,
    location: str,
    cities: str,
    limit: int,
    no_maps: bool,
    fresh: bool,
    unseen_older_than: int,
    debug: bool,
) -> None:
    """FoxWorks Lead Pipeline — scrape, score, and export ICP leads for Apollo."""

    if debug:
        logging.getLogger().setLevel(logging.DEBUG)

    keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    if not keyword_list:
        console.print("[red]Error: at least one keyword is required.")
        sys.exit(1)

    # --cities takes precedence over --location
    if cities:
        location_list = [c.strip() for c in cities.split(",") if c.strip()]
    elif location:
        location_list = [location.strip()]
    else:
        console.print("[red]Error: provide --location or --cities.")
        sys.exit(1)

    console.print()
    console.print("[bold cyan]FoxWorks Lead Pipeline[/]")
    console.print(f"  Keywords : {keyword_list}")
    console.print(f"  Cities   : {location_list}")
    console.print(f"  Limit    : {limit} raw leads total")
    console.print(
        f"  Source   : {'Bing only' if no_maps else 'Google Maps (Bing fallback)'}"
    )
    console.print(f"  Fresh    : {'yes (ignoring seen domains)' if fresh else 'no'}")
    if unseen_older_than:
        console.print(f"  Expiry   : re-process domains older than {unseen_older_than} days")
    console.print(f"  Output   : {config.OUTPUT_DIR}/")
    console.print()

    asyncio.run(run_pipeline(
        keywords          = keyword_list,
        locations         = location_list,
        limit             = limit,
        use_maps          = not no_maps,
        fresh             = fresh,
        unseen_older_than = unseen_older_than,
    ))


if __name__ == "__main__":
    main()
