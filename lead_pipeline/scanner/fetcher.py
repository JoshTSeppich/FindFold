"""
Async HTTP fetcher with retry logic and disk-based caching.

Design:
  - aiohttp for async HTTP (fast, non-blocking)
  - asyncio.Semaphore to cap concurrent connections
  - Simple MD5-keyed file cache to avoid re-fetching across runs
  - Exponential backoff on transient failures
  - SSL verification disabled for small business sites
    (many have expired or misconfigured certificates)
"""

import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

import aiohttp

from config import (
    REQUEST_TIMEOUT,
    MAX_RETRIES,
    RETRY_DELAY,
    CACHE_DIR,
    CONCURRENT_REQUESTS,
    CACHE_TTL_DAYS,
)

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection":      "keep-alive",
}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(url: str) -> Path:
    h = hashlib.md5(url.encode()).hexdigest()
    return CACHE_DIR / f"{h}.html"


def _read_cache(url: str) -> Optional[str]:
    p = _cache_path(url)
    if not p.exists():
        return None
    # Expire stale cache entries
    age_days = (time.time() - p.stat().st_mtime) / 86400
    if age_days > CACHE_TTL_DAYS:
        p.unlink(missing_ok=True)
        return None
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None


def _write_cache(url: str, html: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _cache_path(url).write_text(html, encoding="utf-8", errors="ignore")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Single-URL fetch
# ---------------------------------------------------------------------------

async def fetch_html(
    session: aiohttp.ClientSession,
    url: str,
    use_cache: bool = True,
) -> Optional[str]:
    """
    Fetch the homepage HTML for `url`.

    Returns the HTML string, or None if all attempts fail.
    Caches successful responses to disk.
    """
    # Normalise scheme
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if use_cache:
        cached = _read_cache(url)
        if cached:
            logger.debug(f"[scanner] cache hit: {url}")
            return cached

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.get(
                url,
                headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                allow_redirects=True,
                ssl=False,
            ) as resp:
                if resp.status >= 400:
                    logger.debug(f"[scanner] HTTP {resp.status}: {url}")
                    return None

                html = await resp.text(errors="ignore")

                if use_cache:
                    _write_cache(url, html)

                return html

        except asyncio.TimeoutError:
            logger.debug(f"[scanner] timeout (attempt {attempt}): {url}")
        except aiohttp.ClientConnectorError as e:
            logger.debug(f"[scanner] connect error (attempt {attempt}): {url} — {e}")
        except aiohttp.ClientError as e:
            logger.debug(f"[scanner] client error (attempt {attempt}): {url} — {e}")
        except Exception as e:
            logger.debug(f"[scanner] unexpected (attempt {attempt}): {url} — {e}")

        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY * attempt)

    logger.debug(f"[scanner] gave up: {url}")
    return None


# ---------------------------------------------------------------------------
# Batch fetch (concurrent)
# ---------------------------------------------------------------------------

async def fetch_many(
    urls: list[str],
    concurrency: int = CONCURRENT_REQUESTS,
    use_cache: bool = True,
) -> dict[str, Optional[str]]:
    """
    Fetch multiple URLs concurrently up to `concurrency` at a time.

    Returns:
        dict mapping original url → html string (or None on failure)
    """
    results: dict[str, Optional[str]] = {}
    semaphore = asyncio.Semaphore(concurrency)

    connector = aiohttp.TCPConnector(ssl=False, limit=concurrency + 5)
    async with aiohttp.ClientSession(connector=connector) as session:

        async def _one(url: str) -> None:
            async with semaphore:
                results[url] = await fetch_html(session, url, use_cache=use_cache)

        tasks = [asyncio.create_task(_one(url)) for url in urls]
        completed = 0
        total     = len(tasks)

        for coro in asyncio.as_completed(tasks):
            await coro
            completed += 1
            if completed % 20 == 0 or completed == total:
                logger.info(f"[scanner] {completed}/{total} pages fetched")

    return results
