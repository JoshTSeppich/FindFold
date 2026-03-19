"""
Homepage content extractor — zero LLMs, zero paid APIs.

Pulls signal-bearing text from raw HTML using BeautifulSoup + regex.
Designed to be fast and memory-safe; caps extracted text at MAX_TEXT_CHARS.

Output fields merged into each lead dict:
  page_title        — <title> tag text
  page_description  — meta description or og:description content
  page_text         — first MAX_TEXT_CHARS of visible body text
  has_phone         — True if a US phone number pattern is detected
  has_form          — True if a <form> or text/email/tel input exists
  email             — first email address found on the page (or "")
"""

import re
import logging

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

_DROP_TAGS = frozenset({
    "script", "style", "noscript", "head",
    "iframe", "svg", "nav", "footer", "header", "aside",
})

MAX_TEXT_CHARS = 3_000

_PHONE_RE = re.compile(
    r"\b(?:\+?1[-.\s]?)?\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})\b"
)
_INPUT_RE = re.compile(
    r'<input[^>]+type=["\']?\s*(?:text|email|tel)\s*["\']?',
    re.IGNORECASE,
)
# Captures standard email addresses; avoids image filenames / version strings
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)
_EMAIL_JUNK = re.compile(
    r"@(sentry\.|example\.|test\.|wix\.|squarespace\.|wordpress\.|google\.|adobe\.)",
    re.I,
)


def extract(html: str) -> dict:
    """
    Parse homepage HTML and return a dict of signal fields.
    Safe to call with empty or malformed HTML — always returns a complete dict.
    """
    if not html or not html.strip():
        return _empty()

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.debug(f"[extractor] parse error: {e}")
        return _empty()

    # ---- Title ----
    title_tag  = soup.find("title")
    page_title = title_tag.get_text(strip=True)[:200] if title_tag else ""

    # ---- Meta description ----
    desc_tag = (
        soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        or soup.find("meta", attrs={"property": "og:description"})
        or soup.find("meta", attrs={"name": re.compile(r"og:description", re.I)})
    )
    page_description = ""
    if desc_tag and isinstance(desc_tag, Tag):
        page_description = (desc_tag.get("content") or "").strip()[:300]

    # ---- Email: mailto links first (most reliable), then raw text scan ----
    email = ""
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:"):
            candidate = href[7:].split("?")[0].strip().lower()
            if candidate and not _EMAIL_JUNK.search(candidate):
                email = candidate
                break
    if not email:
        for m in _EMAIL_RE.finditer(html):
            candidate = m.group(0).lower()
            if not _EMAIL_JUNK.search(candidate):
                email = candidate
                break

    # ---- Visible body text ----
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()

    body     = soup.find("body")
    raw_text = body.get_text(separator=" ", strip=True) if body else soup.get_text(separator=" ", strip=True)
    page_text = re.sub(r"\s{2,}", " ", raw_text)[:MAX_TEXT_CHARS]

    has_phone = bool(_PHONE_RE.search(raw_text))
    has_form  = bool(soup.find("form")) or bool(_INPUT_RE.search(html))

    return {
        "page_title":       page_title,
        "page_description": page_description,
        "page_text":        page_text,
        "has_phone":        has_phone,
        "has_form":         has_form,
        "email":            email,
    }


def _empty() -> dict:
    return {
        "page_title":       "",
        "page_description": "",
        "page_text":        "",
        "has_phone":        False,
        "has_form":         False,
        "email":            "",
    }
