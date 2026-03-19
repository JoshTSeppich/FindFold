"""
Homepage content extractor — zero LLMs, zero paid APIs.

Pulls signal-bearing text from raw HTML using BeautifulSoup + regex.
Designed to be fast and memory-safe; caps extracted text at MAX_TEXT_CHARS.

Output fields (merged into each lead dict by main.py):
  page_title        — <title> tag text
  page_description  — meta description or og:description content
  page_text         — first MAX_TEXT_CHARS of visible body text
  has_phone         — True if a US-style phone number pattern is detected
  has_form          — True if a <form> or text-type input is detected
"""

import re
import logging
from typing import Optional

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

# Tags whose content is pure noise for our scoring purposes
_DROP_TAGS = frozenset({
    "script", "style", "noscript", "head",
    "iframe", "svg", "nav", "footer",
    "header", "aside",
})

# Visible body text is capped to save memory; 3 KB is plenty for keyword matching
MAX_TEXT_CHARS = 3_000

# Loose US phone pattern — also catches (801) 555-1234 and 801.555.1234
_PHONE_RE = re.compile(
    r"\b(?:\+?1[-.\s]?)?\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})\b"
)

# Form detection: actual <form> OR a visible text/email/tel input
_INPUT_RE = re.compile(
    r'<input[^>]+type=["\']?\s*(?:text|email|tel)\s*["\']?',
    re.IGNORECASE,
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

    # ---- Meta description (standard or Open Graph) ----
    desc_tag = (
        soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        or soup.find("meta", attrs={"property": "og:description"})
        or soup.find("meta", attrs={"name": re.compile(r"og:description", re.I)})
    )
    page_description = ""
    if desc_tag and isinstance(desc_tag, Tag):
        page_description = (desc_tag.get("content") or "").strip()[:300]

    # ---- Visible body text ----
    # Remove noise tags in-place before extracting text
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()

    body = soup.find("body")
    if body:
        raw_text = body.get_text(separator=" ", strip=True)
    else:
        raw_text = soup.get_text(separator=" ", strip=True)

    # Collapse whitespace runs to single spaces, then cap
    page_text = re.sub(r"\s{2,}", " ", raw_text)[:MAX_TEXT_CHARS]

    # ---- Phone detection ----
    has_phone = bool(_PHONE_RE.search(raw_text))

    # ---- Form detection ----
    # Check against original html (soup has already been mutated above)
    has_form = bool(soup.find("form")) or bool(_INPUT_RE.search(html))

    return {
        "page_title":       page_title,
        "page_description": page_description,
        "page_text":        page_text,
        "has_phone":        has_phone,
        "has_form":         has_form,
    }


def _empty() -> dict:
    return {
        "page_title":       "",
        "page_description": "",
        "page_text":        "",
        "has_phone":        False,
        "has_form":         False,
    }
