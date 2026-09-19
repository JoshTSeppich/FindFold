"""
Scrape Google Maps results from the list view.

I read every card in the sidebar with one page.evaluate call instead of
clicking into each one. Clicking costs about three seconds per card, which is
25 minutes for 500 leads. The list view gives me everything in milliseconds.
The cost is that the website link is only shown inline for roughly two thirds
of results. A lead with no website is kept here and dropped by the scorer.
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

# Reads every card on the page. The class names are Google's and change
# without notice, so this is the first place to look when results dry up.
EXTRACT_JS = """
() => Array.from(document.querySelectorAll('div.Nv2PK')).map(card => {
    const nameEl   = card.querySelector('div.qBF1Pd, span.fontHeadlineSmall');
    const siteEl   = card.querySelector('a[data-value="Website"], a[data-item-id="authority"]');
    const catEls   = card.querySelectorAll('div.W4Etuc, span.uEubGf');
    const addrEl   = card.querySelector('div.UaQhfb, div.Io6YTe');
    const ratingEl = card.querySelector('span.MW4etd');
    const reviewEl = card.querySelector('span.UY7F9');

    let phone = '';
    card.querySelectorAll('[aria-label]').forEach(el => {
        const lbl = el.getAttribute('aria-label') || '';
        if (/phone|call/i.test(lbl) && /\\d{3}/.test(lbl))
            phone = lbl.replace(/[^0-9+\\-().\\s]/g, '').trim();
    });
    if (!phone) {
        const m = (card.innerText || '').match(/\\(?\\d{3}\\)?[\\s.\\-]\\d{3}[\\s.\\-]\\d{4}/);
        if (m) phone = m[0].trim();
    }

    return {
        name:         nameEl ? nameEl.innerText.trim() : '',
        website:      siteEl ? (siteEl.href || siteEl.getAttribute('href') || '') : '',
        category:     catEls.length ? catEls[0].innerText.trim() : '',
        address:      addrEl ? addrEl.innerText.trim() : '',
        phone:        phone,
        rating:       ratingEl ? ratingEl.innerText.trim() : '',
        review_count: reviewEl ? reviewEl.innerText.replace(/[()]/g, '').trim() : '',
    };
}).filter(r => r.name);
"""


def build_queries(keywords: list[str], location: str) -> list[str]:
    queries = []
    for kw in keywords:
        queries.append(f"{kw} {location}")
        queries.append(f"{kw} free estimate {location}")
        queries.append(f"{kw} near me {location}")
    return queries


def clean_url(href: str) -> str:
    """Unwrap Google's redirect and drop the tracking query string."""
    if not href:
        return ""
    m = re.search(r"[?&]q=(https?://[^&]+)", href)
    if m:
        href = m.group(1)
    if "?" in href and "google" not in href:
        href = href.split("?")[0]
    return href.strip().rstrip("/")


async def dismiss_consent(page: Page) -> None:
    for sel in (
        'button:has-text("Accept all")',
        'button:has-text("Accept")',
        'button[aria-label="Accept all"]',
        'button:has-text("Reject all")',
    ):
        try:
            await page.click(sel, timeout=2000)
            return
        except Exception:
            pass


async def scroll_to_load(page: Page, times: int = 15) -> None:
    """Scroll the results feed so Google loads more cards."""
    try:
        feed = page.locator('div[role="feed"]')
        for _ in range(times):
            await feed.evaluate("el => el.scrollTop += 900")
            await page.wait_for_timeout(400)
    except Exception:
        pass


async def scrape_query(page: Page, query: str, limit: int) -> list[dict]:
    url = f"https://www.google.com/maps/search/{quote(query)}"
    logger.info(f"[google_maps] {url}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(1500)
    except Exception as e:
        logger.warning(f"[google_maps] load failed: {e}")
        return []

    await dismiss_consent(page)
    await scroll_to_load(page)

    raw = await page.evaluate(EXTRACT_JS)
    logger.info(f"[google_maps] {len(raw)} cards for '{query}'")

    results = []
    for item in raw:
        if len(results) >= limit:
            break
        name = item.get("name", "").strip()
        if not name:
            continue
        results.append({
            "company_name": name,
            "website": clean_url(item.get("website", "")),
            "location": item.get("address", "").strip(),
            "category": item.get("category", "").strip(),
            "phone": item.get("phone", "").strip(),
            "rating": item.get("rating", "").strip(),
            "review_count": item.get("review_count", "").strip(),
            "source": "google_maps",
        })
    return results


async def scrape(keywords: list[str], location: str, limit: int) -> list[dict]:
    """Run every keyword and location query and return up to `limit` leads.

    Drives the installed Chrome headless. Leads are deduped by name here so
    the same business from three query variants counts once.
    """
    results: list[dict] = []
    seen: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="chrome")
        context = await browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 900})
        await context.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
        page = await context.new_page()

        for query in build_queries(keywords, location):
            if len(results) >= limit:
                break
            for lead in await scrape_query(page, query, limit - len(results)):
                key = lead["company_name"].lower()
                if key not in seen:
                    seen.add(key)
                    results.append(lead)
            await asyncio.sleep(1.0)

        await browser.close()

    logger.info(f"[google_maps] {len(results)} leads scraped")
    return results
