"""
Scrape Bing web results. Used with --no-maps or when Google Maps fails.

Bing wraps every result link in a bing.com/ck/a redirect, so the href is
useless. The real domain is in the <cite> element as "site.com > path", and
I rebuild a clean https URL from that.
"""

import asyncio
import logging
import re
from urllib.parse import quote

from playwright.async_api import Page, async_playwright

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

# Directories, social sites, and Bing itself. Never a lead.
SKIP_RE = re.compile(
    r"(yelp\.com|angi\.com|angieslist|thumbtack|houzz|homeadvisor|"
    r"yellowpages|bbb\.org|manta\.com|expertise\.com|bark\.com|porch\.com|"
    r"groupon|facebook|instagram|linkedin|twitter|x\.com|reddit|youtube|"
    r"tiktok|tripadvisor|trustpilot|homeowners|plumbersofamerica|"
    r"todayshomeowner|fixr\.com|improvenet|networx|microsoft|msn\.com|"
    r"bing\.com)",
    re.I,
)

# Reads each result's title, cite, and snippet. Direct DOM access is more
# reliable than locators on Bing's rendered results.
EXTRACT_JS = """() =>
    Array.from(document.querySelectorAll('li.b_algo')).map(el => ({
        title:   (el.querySelector('h2 a') || {}).innerText || '',
        cite:    (el.querySelector('cite') || {}).innerText || '',
        snippet: (el.querySelector('.b_caption p') || {}).innerText || '',
    }))
"""


def build_queries(keywords: list[str], location: str) -> list[str]:
    """Three plain phrasings per keyword. Quoting terms cuts Bing's result count too far."""
    queries = []
    for kw in keywords:
        queries.append(f"{kw} {location}")
        queries.append(f"{kw} company {location}")
        queries.append(f"{kw} services {location} free estimate")
    return queries


def url_from_cite(cite_text: str) -> str:
    """Turn "https://example.com › path › page" into "https://example.com"."""
    if not cite_text:
        return ""
    base = cite_text.split("›")[0].strip().rstrip("/")
    if not base:
        return ""
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    return base


async def search_one(page: Page, query: str) -> list[dict]:
    url = f"https://www.bing.com/search?q={quote(query)}&count=20&setlang=en-US"
    logger.info(f"[bing] {query}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(1500)
    except Exception as e:
        logger.warning(f"[bing] load failed: {e}")
        return []

    results = []
    for item in await page.evaluate(EXTRACT_JS):
        website = url_from_cite((item.get("cite") or "").strip())
        if not website or SKIP_RE.search(website):
            continue
        results.append({
            "company_name": (item.get("title") or "").strip(),
            "website": website,
            "location": "",
            "category": "",
            "source": "bing",
            "_snippet": (item.get("snippet") or "").strip(),
        })

    logger.info(f"[bing] {len(results)} usable results for: {query}")
    return results


async def scrape(keywords: list[str], location: str, limit: int) -> list[dict]:
    """Run every keyword and location query and return up to `limit` leads, deduped by URL."""
    results: list[dict] = []
    seen: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="chrome")
        page = await browser.new_page(user_agent=USER_AGENT)

        for query in build_queries(keywords, location):
            if len(results) >= limit:
                break
            for lead in await search_one(page, query):
                key = lead["website"].lower().rstrip("/")
                if key not in seen:
                    seen.add(key)
                    results.append(lead)
                    if len(results) >= limit:
                        break
            await asyncio.sleep(1.0)

        await browser.close()

    logger.info(f"[bing] {len(results)} leads scraped")
    return results
