"""
Command-line entry point for FindFold.

Usage:
    python main.py --keywords "plumbing,HVAC,roofing,med spa" --location "Utah" --limit 300

    # Several cities. Each city gets an even share of --limit.
    python main.py --keywords "HVAC,roofing" --cities "Salt Lake City Utah,Provo Utah" --limit 100

    # Skip Google Maps and use Bing only.
    python main.py --keywords "plumbing,HVAC" --location "Utah" --no-maps

    # Ignore the seen-domains list and process everything again.
    python main.py --keywords "plumbing,HVAC" --location "Utah" --fresh

    # Process again any domain first seen more than 90 days ago.
    python main.py --keywords "plumbing,HVAC" --location "Utah" --unseen-older-than 90

Files written to output/:
    raw_leads.csv        everything scraped, before any filtering
    filtered_leads.csv   leads that passed scoring, with all fields
    apollo_ready.csv     company_name and domain only, for Apollo bulk enrichment
    outreach_ready.csv   scores, contact fields, and tags for outreach tools
"""

import asyncio
import csv
import logging
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

load_dotenv()

import config
from lead_pipeline.export import export_apollo, export_outreach
from lead_pipeline.filter import deduplicate, filter_leads, filter_new, rescore_ambiguous, save_seen
from lead_pipeline.scanner import extract, fetch_many
from lead_pipeline.scraper import scrape_bing, scrape_google_maps

console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(console=console, show_path=False, markup=True)],
)
logger = logging.getLogger("pipeline")

RAW_FIELDS = [
    "company_name", "website", "location", "category", "source",
    "phone", "rating", "review_count",
]

FILTERED_FIELDS = [
    "company_name", "domain", "location", "category",
    "icp_score", "claude_score", "reason_tags",
    "phone", "email", "rating", "review_count",
    "page_title", "page_description",
]


def save_csv(rows: list[dict], path: Path, fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def print_top_leads(leads: list[dict]) -> None:
    """Print the twenty best leads as a table."""
    top = sorted(leads, key=lambda x: x.get("icp_score", 0), reverse=True)[:20]

    table = Table(title="Top leads", box=box.SIMPLE_HEAD, header_style="bold cyan")
    table.add_column("Company", max_width=32, no_wrap=True)
    table.add_column("Domain", max_width=26, no_wrap=True)
    table.add_column("Score", justify="right", style="bold green", width=6)
    table.add_column("Claude", justify="right", style="bold yellow", width=7)
    table.add_column("Tags", max_width=38)

    for lead in top:
        claude_score = lead.get("claude_score")
        table.add_row(
            lead.get("company_name", "")[:32],
            lead.get("domain", "")[:26],
            f"{lead.get('icp_score', 0):.2f}",
            f"{claude_score:.2f}" if claude_score is not None else "",
            lead.get("reason_tags", ""),
        )

    console.print()
    console.print(table)


async def scrape_city(keywords: list[str], location: str, limit: int, use_maps: bool) -> list[dict]:
    """Scrape one city. If Google Maps fails, fall back to Bing."""
    if use_maps:
        try:
            return await scrape_google_maps(keywords, location, limit)
        except Exception as e:
            logger.warning(f"[{location}] Maps failed ({e}). Falling back to Bing.")
    return await scrape_bing(keywords, location, limit)


def shared_location(locations: list[str]) -> str:
    """Return the words all locations share, so scoring matches the region rather than one city.

    ["Salt Lake City Utah", "Provo Utah"] gives "Utah".
    """
    if len(locations) == 1:
        return locations[0]
    word_sets = [set(loc.split()) for loc in locations]
    common = word_sets[0].intersection(*word_sets[1:])
    return " ".join(w for w in locations[0].split() if w in common) or locations[0]


async def run_pipeline(
    keywords: list[str],
    locations: list[str],
    limit: int,
    use_maps: bool,
    fresh: bool,
    unseen_older_than: int,
) -> None:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Stage 1: scrape.
    console.rule("[bold blue]Stage 1: scrape")

    per_city_limit = max(1, limit // len(locations))
    source = "Google Maps, Bing fallback" if use_maps else "Bing only"
    console.print(f"  Source: {source}\n  Cities: {len(locations)}\n  Per city: {per_city_limit} raw leads")

    # Cities run one at a time. Several Playwright browsers hitting Google Maps
    # from one machine get rate limited.
    raw_leads: list[dict] = []
    for loc in locations:
        raw_leads.extend(await scrape_city(keywords, loc, per_city_limit, use_maps))

    if not raw_leads:
        console.print("[bold red]No leads scraped. Check the keywords and location.")
        sys.exit(1)

    save_csv(raw_leads, config.RAW_OUTPUT, RAW_FIELDS)
    console.print(f"  {len(raw_leads)} raw leads written to {config.RAW_OUTPUT}")

    # Stage 2: remove duplicates within this run.
    console.rule("[bold blue]Stage 2: dedupe")

    deduped = deduplicate(raw_leads)
    console.print(f"  {len(raw_leads)} leads, {len(deduped)} after dedupe")

    # Stage 3: fetch each homepage and pull out the text we score on.
    console.rule("[bold blue]Stage 3: scan websites")

    urls = [lead["website"] for lead in deduped if (lead.get("website") or "").strip()]
    console.print(f"  Fetching {len(urls)} sites, {config.CONCURRENT_REQUESTS} at a time")

    html_by_url = await fetch_many(urls, concurrency=config.CONCURRENT_REQUESTS)

    scanned = 0
    for lead in deduped:
        html = html_by_url.get((lead.get("website") or "").strip())
        if html:
            lead.update(extract(html))
            scanned += 1

    console.print(f"  {scanned} of {len(urls)} pages scanned")

    # Stage 4: keyword scoring.
    # With a Claude key present I keep everything down to the ambiguous floor,
    # because Claude gets the final say on that band. Without a key the keyword
    # threshold is final.
    console.rule("[bold blue]Stage 4: keyword scoring")

    use_claude = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    threshold = min(config.ICP_THRESHOLD, config.CLAUDE_AMBIGUOUS_MIN) if use_claude else config.ICP_THRESHOLD

    filtered = filter_leads(deduped, shared_location(locations), keywords, threshold=threshold)
    console.print(f"  {len(filtered)} leads scored at or above {threshold}")

    # Stage 5: Claude rescoring for the ambiguous band only.
    console.rule("[bold blue]Stage 5: Claude rescoring")

    ambiguous = sum(
        1 for lead in filtered
        if config.CLAUDE_AMBIGUOUS_MIN <= lead.get("icp_score", 0) <= config.CLAUDE_AMBIGUOUS_MAX
    )
    if ambiguous and use_claude:
        console.print(f"  Sending {ambiguous} ambiguous leads to Claude")
        filtered = await rescore_ambiguous(filtered)
        console.print(f"  {len(filtered)} leads after Claude")
    elif ambiguous:
        console.print("  No ANTHROPIC_API_KEY set, keyword scores are final")
    else:
        console.print("  No ambiguous leads, nothing to rescore")

    # Stage 6: drop domains already sent to Apollo in an earlier run.
    console.rule("[bold blue]Stage 6: cross-run dedupe")

    filtered, seen = filter_new(filtered, fresh=fresh, unseen_older_than=unseen_older_than)
    console.print(f"  {len(filtered)} domains not sent to Apollo before")

    save_csv(filtered, config.FILTERED_OUTPUT, FILTERED_FIELDS)
    console.print(f"  Written to {config.FILTERED_OUTPUT}")

    # Stage 7: export.
    console.rule("[bold blue]Stage 7: export")

    apollo_rows = export_apollo(filtered, config.APOLLO_OUTPUT)
    outreach_rows = export_outreach(filtered, config.OUTREACH_OUTPUT)

    # Only record domains as seen once both exports are on disk. If an export
    # fails, the leads come back next run instead of being lost.
    if apollo_rows > 0 and outreach_rows > 0:
        save_seen(seen)
        console.print(f"  Seen domains saved, {len(seen)} total")
    elif filtered:
        console.print("[yellow]  An export wrote 0 rows, so seen domains were not saved. These leads will run again next time.")

    console.print(f"  Apollo: {apollo_rows} rows in {config.APOLLO_OUTPUT}")
    console.print(f"  Outreach: {outreach_rows} rows in {config.OUTREACH_OUTPUT}")

    console.rule("[bold green]Done")

    print_top_leads(filtered)

    console.print()
    console.print(f"  Raw scraped: [bold]{len(raw_leads)}[/]")
    console.print(f"  After dedupe: [bold]{len(deduped)}[/]")
    console.print(f"  Pages scanned: [bold]{scanned}[/]")
    console.print(f"  Qualified: [bold cyan]{len(filtered)}[/]")
    if filtered:
        console.print(f"  Raw to qualified: [bold]{len(raw_leads) / len(filtered):.1f}x[/]")
    console.print()


@click.command()
@click.option("--keywords", required=True, help='Comma-separated search terms, e.g. "plumbing,HVAC,roofing".')
@click.option("--location", default="", help='One market, e.g. "Salt Lake City Utah". Ignored when --cities is set.')
@click.option("--cities", default="", help='Comma-separated cities, e.g. "Salt Lake City Utah,Provo Utah".')
@click.option("--limit", default=300, show_default=True, help="Max raw leads to scrape, split evenly across cities.")
@click.option("--no-maps", is_flag=True, default=False, help="Skip Google Maps and use Bing only.")
@click.option("--fresh", is_flag=True, default=False, help="Ignore the seen-domains list and process every lead again.")
@click.option("--unseen-older-than", "unseen_older_than", default=0, show_default=True, help="Process again any domain first seen more than N days ago. 0 means never.")
@click.option("--debug", is_flag=True, default=False, help="Log at DEBUG level.")
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
    """Scrape local businesses, score them against the Foxworks customer profile, and export CSVs for Apollo."""
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)

    keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    if not keyword_list:
        console.print("[red]At least one keyword is required.")
        sys.exit(1)

    if cities:
        location_list = [c.strip() for c in cities.split(",") if c.strip()]
    elif location:
        location_list = [location.strip()]
    else:
        console.print("[red]Give --location or --cities.")
        sys.exit(1)

    console.print()
    console.print("[bold cyan]FindFold[/]")
    console.print(f"  Keywords: {keyword_list}")
    console.print(f"  Cities: {location_list}")
    console.print(f"  Limit: {limit} raw leads total")
    console.print(f"  Source: {'Bing only' if no_maps else 'Google Maps, Bing fallback'}")
    console.print(f"  Fresh: {'yes, ignoring seen domains' if fresh else 'no'}")
    if unseen_older_than:
        console.print(f"  Expiry: process again domains older than {unseen_older_than} days")
    console.print(f"  Output: {config.OUTPUT_DIR}/")
    console.print()

    asyncio.run(run_pipeline(
        keywords=keyword_list,
        locations=location_list,
        limit=limit,
        use_maps=not no_maps,
        fresh=fresh,
        unseen_older_than=unseen_older_than,
    ))


if __name__ == "__main__":
    main()
