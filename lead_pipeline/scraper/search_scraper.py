"""
Bing organic search scraper using Playwright.

Used when --no-maps is passed (no Google Maps needed).

Key implementation detail: Bing wraps every outbound href in a bing.com/ck/a
redirect. The real URL lives in the <cite> element (e.g. "site.com › path").
We extract the domain from there and construct a clean https:// URL.
"""

import asyncio
import logging
import re
from urllib.parse import quote

from playwright.async_api import async_playwright, Page

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

_SKIP_RE = re.compile(
    r"(yelp\.com|angi\.com|angieslist|thumbtack|houzz|homeadvisor|"
    r"yellowpages|bbb\.org|manta\.com|expertise\.com|bark\.com|porch\.com|"
    r"groupon|facebook|instagram|linkedin|twitter|x\.com|reddit|youtube|"
    r"tiktok|tripadvisor|trustpilot|homeowners|plumbersofamerica|"
    r"todayshomeowner|fixr\.com|improvenet|networx|microsoft|msn\.com|"
    r"bing\.com)",
    re.I,
)


def _build_queries(keywords: list[str], location: str) -> list[str]:
    """
    Generate varied queries per keyword to maximise result coverage.
    Avoids over-quoting which kills Bing result volume.
    """
    queries = []
    for kw in keywords:
        queries.append(f"{kw} {location}")
        queries.append(f"{kw} company {location}")
        queries.append(f"{kw} services {location} free estimate")
    return queries


def _url_from_cite(cite_text: str) -> str:
    """
    Turn 'https://example.com › path › page' into 'https://example.com'.
    Bing cite text may or may not include the scheme.
    """
    if not cite_text:
        return ""
    # Grab everything before the first › separator
    base = cite_text.split("›")[0].strip()
    # Strip trailing slash
    base = base.rstrip("/")
    if not base:
        return ""
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    return base


def _is_junk(url: str) -> bool:
    return bool(_SKIP_RE.search(url))


async def _search_one(page: Page, query: str) -> list[dict]:
    url = f"https://www.bing.com/search?q={quote(query)}&count=20&setlang=en-US"
    logger.info(f"[bing] {query}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(1500)
    except Exception as e:
        logger.warning(f"[bing] load failed: {e}")
        return []

    # Use evaluate() — direct DOM access is more reliable than Playwright locators
    # for Bing's dynamically rendered results.
    data = await page.evaluate("""() =>
        Array.from(document.querySelectorAll('li.b_algo')).map(el => ({
            title:   (el.querySelector('h2 a')       || {}).innerText || '',
            cite:    (el.querySelector('cite')        || {}).innerText || '',
            snippet: (el.querySelector('.b_caption p')|| {}).innerText || '',
        }))
    """)

    results = []
    for item in data:
        title   = (item.get("title") or "").strip()
        cite    = (item.get("cite")  or "").strip()
        snippet = (item.get("snippet") or "").strip()

        website = _url_from_cite(cite)
        if not website or _is_junk(website):
            continue

        results.append({
            "company_name": title,
            "website":      website,
            "location":     "",
            "category":     "",
            "source":       "bing",
            "_snippet":     snippet,
        })

    logger.info(f"[bing] got {len(results)} usable results for: {query}")
    return results


async def scrape(keywords: list[str], location: str, limit: int) -> list[dict]:
    """
    Scrape Bing organic results for each keyword/location combination.

    Returns list of lead dicts with keys:
    company_name, website, location, category, source
    """
    results: list[dict] = []
    seen: set[str] = set()
    queries = _build_queries(keywords, location)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="chrome")
        page    = await browser.new_page(user_agent=_USER_AGENT)

        for query in queries:
            if len(results) >= limit:
                break

            batch = await _search_one(page, query)
            for lead in batch:
                key = lead["website"].lower().rstrip("/")
                if key not in seen:
                    seen.add(key)
                    results.append(lead)
                    if len(results) >= limit:
                        break

            await asyncio.sleep(1.0)

        await browser.close()

    logger.info(f"[bing] total scraped: {len(results)}")
    return results
