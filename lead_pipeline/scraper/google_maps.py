"""
Google Maps scraper — uses Playwright to automate a headless Chromium browser.

Strategy:
  1. Navigate to maps.google.com/search/{query}
  2. Scroll the results sidebar to load more cards
  3. Click each card → detail panel opens → extract website, category, address
  4. Repeat for every keyword/location query combination

No API key required. Requires: playwright + chromium (see README).
"""

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import quote

from playwright.async_api import (
    async_playwright,
    Page,
    TimeoutError as PWTimeout,
)

logger = logging.getLogger(__name__)

# Realistic desktop user-agent to reduce bot detection
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------

def _build_queries(keywords: list[str], location: str) -> list[str]:
    queries = []
    for kw in keywords:
        queries.append(f"{kw} {location}")
        queries.append(f"{kw} free estimate {location}")
        queries.append(f"{kw} near me {location}")
    return queries


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------

async def _dismiss_consent(page: Page) -> None:
    """Click through cookie/location consent dialogs if they appear."""
    candidates = [
        'button:has-text("Accept all")',
        'button:has-text("Accept")',
        'button[aria-label="Accept all"]',
        'button:has-text("I agree")',
        'button:has-text("Reject all")',  # also dismisses
    ]
    for sel in candidates:
        try:
            await page.click(sel, timeout=2500)
            logger.debug("Dismissed consent dialog")
            return
        except Exception:
            pass


async def _scroll_sidebar(page: Page, times: int = 8) -> None:
    """Scroll the results feed to trigger lazy-loading of more cards."""
    try:
        feed = page.locator('div[role="feed"]')
        for _ in range(times):
            await feed.evaluate("el => el.scrollTop += 900")
            await page.wait_for_timeout(600)
    except Exception:
        pass


def _clean_website(href: str) -> str:
    """Strip Google redirect wrappers and tracking params from website URLs."""
    if not href:
        return ""
    # Google wraps external links: /url?q=https://example.com&...
    match = re.search(r"[?&]q=(https?://[^&]+)", href)
    if match:
        href = match.group(1)
    # Strip trailing slash and query string added by Google
    if "?" in href and not href.startswith("https://www.google"):
        href = href.split("?")[0]
    return href.strip()


# ---------------------------------------------------------------------------
# Per-card extraction (opens detail panel)
# ---------------------------------------------------------------------------

async def _extract_card(page: Page, card) -> Optional[dict]:
    """
    Click one result card, wait for the detail panel to load,
    and extract company_name, website, category, location.
    Returns None if critical data is missing.
    """
    # ---- Name ----
    name = ""
    for name_sel in ("div.qBF1Pd", "span.fontHeadlineSmall", "a.hfpxzc"):
        try:
            el = card.locator(name_sel).first
            if await el.count() > 0:
                name = (await el.inner_text(timeout=2000)).strip()
                if name:
                    break
        except Exception:
            pass

    if not name:
        return None

    # ---- Click to open detail panel ----
    try:
        await card.click(timeout=5000)
        await page.wait_for_timeout(1200)
    except Exception:
        return None

    # ---- Website ----
    website = ""
    for site_sel in (
        'a[data-item-id="authority"]',
        'a[data-value="Website"]',
        'a[jsaction*="pane.rating.moreReviews"]',  # sometimes reused
        'a[aria-label*="website" i]',
    ):
        try:
            el = page.locator(site_sel).first
            if await el.count() > 0:
                href = await el.get_attribute("href", timeout=2000)
                website = _clean_website(href or "")
                if website:
                    break
        except Exception:
            pass

    # ---- Category ----
    category = ""
    for cat_sel in (
        'button[jsaction*="category"]',
        'button[data-item-id*="category"]',
        'span.YkTocd',
    ):
        try:
            el = page.locator(cat_sel).first
            if await el.count() > 0:
                category = (await el.inner_text(timeout=1500)).strip()
                if category:
                    break
        except Exception:
            pass

    # ---- Address ----
    location = ""
    try:
        addr_el = page.locator('button[data-item-id="address"]').first
        if await addr_el.count() > 0:
            location = (await addr_el.inner_text(timeout=1500)).strip()
    except Exception:
        pass

    return {
        "company_name": name,
        "website": website,
        "location": location,
        "category": category,
        "source": "google_maps",
    }


async def _back_to_list(page: Page) -> None:
    """Navigate back to the results list from a detail panel."""
    for sel in ('button[aria-label="Back"]', 'button[jsaction*="back"]'):
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                await btn.click(timeout=2500)
                await page.wait_for_timeout(400)
                return
        except Exception:
            pass
    # Fallback: browser back
    try:
        await page.go_back(timeout=4000)
        await page.wait_for_timeout(800)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Per-query scrape
# ---------------------------------------------------------------------------

async def _scrape_query(page: Page, query: str, limit: int) -> list[dict]:
    url = f"https://www.google.com/maps/search/{quote(query)}"
    logger.info(f"[google_maps] {url}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(1800)
    except Exception as e:
        logger.warning(f"[google_maps] Failed to load: {e}")
        return []

    await _dismiss_consent(page)
    await page.wait_for_timeout(500)
    await _scroll_sidebar(page, times=6)

    cards = await page.locator("div.Nv2PK").all()
    logger.info(f"[google_maps] Found {len(cards)} cards for '{query}'")

    results: list[dict] = []
    seen: set[str] = set()

    for card in cards:
        if len(results) >= limit:
            break

        info = await _extract_card(page, card)
        if not info:
            continue

        key = info["company_name"].lower()
        if key in seen:
            await _back_to_list(page)
            continue
        seen.add(key)

        results.append(info)
        logger.debug(f"  + {info['company_name']} — {info['website']}")

        await _back_to_list(page)

    return results


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def scrape(keywords: list[str], location: str, limit: int) -> list[dict]:
    """
    Scrape Google Maps for each keyword/location combination.

    Args:
        keywords: list of search terms, e.g. ["plumbing", "HVAC"]
        location: target city/state, e.g. "Utah"
        limit:    max total raw leads to return

    Returns:
        List of lead dicts with keys:
        company_name, website, location, category, source
    """
    results: list[dict] = []
    seen_names: set[str] = set()
    queries = _build_queries(keywords, location)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
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
                if key not in seen_names:
                    seen_names.add(key)
                    results.append(lead)

            # Polite pause between queries
            await asyncio.sleep(1.5)

        await browser.close()

    logger.info(f"[google_maps] Total scraped: {len(results)}")
    return results
