"""
Google Maps scraper — list view mode (no per-card clicking).

Strategy:
  1. Navigate to maps.google.com/search/{query}
  2. Scroll the results sidebar to lazy-load all cards
  3. Extract ALL card data in one JS evaluate() call — no clicking, no waiting
  4. Repeat for each keyword/location query combination

Why no clicking: clicking each card + waiting for the detail panel + going back
costs ~3 seconds per card. For 500 leads that's 25+ minutes. List view
extraction takes milliseconds regardless of result count.

Trade-off: website URLs are not always visible in the list view. When missing,
the lead is kept with an empty website field — the filter stage will drop it
(hard rule: no domain → dropped). In practice Google Maps shows a website
link inline for ~60-70% of results, which is sufficient volume.
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

# JS that reads every visible card in one pass — no page interaction needed
_EXTRACT_JS = """
() => Array.from(document.querySelectorAll('div.Nv2PK')).map(card => {
    const nameEl     = card.querySelector('div.qBF1Pd, span.fontHeadlineSmall');
    const siteEl     = card.querySelector('a[data-value="Website"], a[data-item-id="authority"]');
    const catEls     = card.querySelectorAll('div.W4Etuc, span.uEubGf');
    const addrEl     = card.querySelector('div.UaQhfb, div.Io6YTe');
    const ratingEl   = card.querySelector('span.MW4etd');
    const reviewEl   = card.querySelector('span.UY7F9');

    // Phone: scan aria-labels for phone hints, fallback to text pattern
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

    let site = siteEl ? (siteEl.href || siteEl.getAttribute('href') || '') : '';

    return {
        name:         nameEl   ? nameEl.innerText.trim()         : '',
        website:      site,
        category:     catEls.length ? catEls[0].innerText.trim() : '',
        address:      addrEl   ? addrEl.innerText.trim()         : '',
        phone:        phone,
        rating:       ratingEl ? ratingEl.innerText.trim()       : '',
        review_count: reviewEl ? reviewEl.innerText.replace(/[()]/g,'').trim() : '',
    };
}).filter(r => r.name);
"""


def _build_queries(keywords: list[str], location: str) -> list[str]:
    queries = []
    for kw in keywords:
        queries.append(f"{kw} {location}")
        queries.append(f"{kw} free estimate {location}")
        queries.append(f"{kw} near me {location}")
    return queries


def _clean_url(href: str) -> str:
    """Unwrap Google redirect URLs and strip tracking params."""
    if not href:
        return ""
    # Google wraps outbound links: /url?q=https://example.com&...
    m = re.search(r"[?&]q=(https?://[^&]+)", href)
    if m:
        href = m.group(1)
    # Drop query strings Google appends to business websites
    if "?" in href and "google" not in href:
        href = href.split("?")[0]
    return href.strip().rstrip("/")


async def _dismiss_consent(page: Page) -> None:
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


async def _scroll_to_load(page: Page, times: int = 15) -> None:
    """Scroll the results feed to trigger lazy-loading."""
    try:
        feed = page.locator('div[role="feed"]')
        for _ in range(times):
            await feed.evaluate("el => el.scrollTop += 900")
            await page.wait_for_timeout(400)
    except Exception:
        pass


async def _scrape_query(page: Page, query: str, limit: int) -> list[dict]:
    url = f"https://www.google.com/maps/search/{quote(query)}"
    logger.info(f"[google_maps] {url}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(1500)
    except Exception as e:
        logger.warning(f"[google_maps] load failed: {e}")
        return []

    await _dismiss_consent(page)
    await _scroll_to_load(page, times=15)

    # Single JS call reads all cards — no per-card Playwright interaction
    raw = await page.evaluate(_EXTRACT_JS)
    logger.info(f"[google_maps] {len(raw)} cards in list view for '{query}'")

    results = []
    for item in raw:
        if len(results) >= limit:
            break
        name    = item.get("name", "").strip()
        website = _clean_url(item.get("website", ""))
        if not name:
            continue
        results.append({
            "company_name": name,
            "website":      website,
            "location":     item.get("address", "").strip(),
            "category":     item.get("category", "").strip(),
            "phone":        item.get("phone", "").strip(),
            "rating":       item.get("rating", "").strip(),
            "review_count": item.get("review_count", "").strip(),
            "source":       "google_maps",
        })

    return results


async def scrape(keywords: list[str], location: str, limit: int) -> list[dict]:
    """
    Scrape Google Maps list view for each keyword/location combination.

    Args:
        keywords: list of search terms, e.g. ["plumbing", "HVAC"]
        location: target city/state, e.g. "Utah"
        limit:    max total raw leads to return

    Returns:
        List of lead dicts: company_name, website, location, category, source
    """
    results: list[dict] = []
    seen: set[str] = set()
    queries = _build_queries(keywords, location)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, channel="chrome")
        context = await browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )
        await context.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
        page = await context.new_page()

        for query in queries:
            if len(results) >= limit:
                break

            batch = await _scrape_query(page, query, limit - len(results))
            for lead in batch:
                key = lead["company_name"].lower()
                if key not in seen:
                    seen.add(key)
                    results.append(lead)

            await asyncio.sleep(1.0)

        await browser.close()

    logger.info(f"[google_maps] total scraped: {len(results)}")
    return results
