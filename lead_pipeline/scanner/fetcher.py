"""
Fetch homepages, many at a time, with retries and a disk cache.

Pages are cached under .cache/ keyed by the MD5 of the URL and expire after
CACHE_TTL_DAYS. The first retry is immediate and later ones back off. Many
small business sites have broken certificates, so a certificate error gets
one more try with verification off. A 4xx is treated as final.
"""

import asyncio
import hashlib
import logging
import ssl
import time
from pathlib import Path
from typing import Optional

import aiohttp

from config import (
    CACHE_DIR,
    CACHE_TTL_DAYS,
    CONCURRENT_REQUESTS,
    MAX_RETRIES,
    REQUEST_TIMEOUT,
    RETRY_DELAY,
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


def cache_path(url: str) -> Path:
    return CACHE_DIR / f"{hashlib.md5(url.encode()).hexdigest()}.html"


def read_cache(url: str) -> Optional[str]:
    p = cache_path(url)
    if not p.exists():
        return None
    age_days = (time.time() - p.stat().st_mtime) / 86400
    if age_days > CACHE_TTL_DAYS:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        return None
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None


def write_cache(url: str, html: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        cache_path(url).write_text(html, encoding="utf-8", errors="ignore")
    except Exception:
        pass


async def fetch_html(session: aiohttp.ClientSession, url: str, use_cache: bool = True) -> Optional[str]:
    """Return the page HTML, or None when every attempt fails."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if use_cache:
        cached = read_cache(url)
        if cached:
            logger.debug(f"[scanner] cache hit: {url}")
            return cached

    ssl_ctx: object = ssl.create_default_context()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.get(
                url,
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT, sock_connect=10, sock_read=REQUEST_TIMEOUT),
                allow_redirects=True,
                ssl=ssl_ctx,
            ) as resp:
                if resp.status >= 400:
                    logger.debug(f"[scanner] HTTP {resp.status}: {url}")
                    return None
                html = await resp.text(errors="ignore")
                if use_cache:
                    write_cache(url, html)
                return html

        except aiohttp.ClientConnectorCertificateError:
            if ssl_ctx is not False:
                logger.debug(f"[scanner] certificate error, retrying without verification: {url}")
                ssl_ctx = False
                continue
        except asyncio.TimeoutError:
            logger.debug(f"[scanner] timeout, attempt {attempt}: {url}")
        except aiohttp.ClientError as e:
            logger.debug(f"[scanner] client error, attempt {attempt}: {url}: {e}")
        except Exception as e:
            logger.debug(f"[scanner] error, attempt {attempt}: {url}: {e}")

        if 1 < attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY * (2 ** (attempt - 2)))

    logger.debug(f"[scanner] gave up: {url}")
    return None


async def fetch_many(
    urls: list[str],
    concurrency: int = CONCURRENT_REQUESTS,
    use_cache: bool = True,
) -> dict[str, Optional[str]]:
    """Fetch every URL, at most `concurrency` at once. Returns url to html, None on failure."""
    results: dict[str, Optional[str]] = {}
    semaphore = asyncio.Semaphore(concurrency)

    connector = aiohttp.TCPConnector(limit=concurrency)
    async with aiohttp.ClientSession(connector=connector) as session:

        async def one(url: str) -> None:
            async with semaphore:
                results[url] = await fetch_html(session, url, use_cache=use_cache)

        tasks = [asyncio.create_task(one(url)) for url in urls]
        done = 0
        for coro in asyncio.as_completed(tasks):
            await coro
            done += 1
            if done % 20 == 0 or done == len(tasks):
                logger.info(f"[scanner] {done}/{len(tasks)} pages fetched")

    return results
