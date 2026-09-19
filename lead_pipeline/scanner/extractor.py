"""
Pull the fields we score on out of a homepage.

BeautifulSoup and a few regexes, nothing paid. Visible text is capped at
MAX_TEXT_CHARS so one huge page cannot slow the run. Every call returns the
same six keys, even for empty or broken HTML.
"""

import logging
import re

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

DROP_TAGS = frozenset({
    "script", "style", "noscript", "head",
    "iframe", "svg", "nav", "footer", "header", "aside",
})

MAX_TEXT_CHARS = 3_000

PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})\b")

# A contact form has an email or tel input. A search box does not.
CONTACT_INPUT_RE = re.compile(r'<input[^>]+type=["\']?\s*(?:email|tel)\s*["\']?', re.IGNORECASE)

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# Addresses at these hosts belong to tools the site uses, not the business.
EMAIL_JUNK_RE = re.compile(
    r"@(?:sentry\.|example\.|test\.|wix\.|squarespace\.|wordpress\.|"
    r"google\.|adobe\.|cloudflare\.|mailchimp\.|hubspot\.|"
    r"zendesk\.|intercom\.|freshdesk\.|drift\.)",
    re.I,
)

NOREPLY_RE = re.compile(
    r"^(?:no-?reply|bounce|mailer-daemon|postmaster|alerts?|notifications?|"
    r"do-not-reply|donotreply|noreply|system|auto|daemon)@",
    re.I,
)

EMPTY = {
    "page_title": "",
    "page_description": "",
    "page_text": "",
    "has_phone": False,
    "has_form": False,
    "email": "",
}


def is_junk_email(candidate: str) -> bool:
    return bool(EMAIL_JUNK_RE.search(candidate)) or bool(NOREPLY_RE.match(candidate))


def extract(html: str) -> dict:
    """Return page_title, page_description, page_text, has_phone, has_form, and email."""
    if not html or not html.strip():
        return dict(EMPTY)

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as e:
            logger.debug(f"[extractor] parse error: {e}")
            return dict(EMPTY)

    title_tag = soup.find("title")
    page_title = title_tag.get_text(strip=True)[:200] if title_tag else ""

    desc_tag = (
        soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        or soup.find("meta", attrs={"property": "og:description"})
    )
    page_description = ""
    if desc_tag and isinstance(desc_tag, Tag):
        page_description = (desc_tag.get("content") or "").strip()[:300]

    # mailto links are the most reliable source. Fall back to scanning the raw HTML.
    email = ""
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            candidate = href[7:].split("?")[0].strip().lower()
            if candidate and not is_junk_email(candidate):
                email = candidate
                break
    if not email:
        for m in EMAIL_RE.finditer(html):
            candidate = m.group(0).lower()
            if not is_junk_email(candidate):
                email = candidate
                break

    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()

    body = soup.find("body")
    raw_text = (body or soup).get_text(separator=" ", strip=True)
    page_text = re.sub(r"\s{2,}", " ", raw_text)[:MAX_TEXT_CHARS]

    return {
        "page_title": page_title,
        "page_description": page_description,
        "page_text": page_text,
        "has_phone": bool(PHONE_RE.search(raw_text)),
        "has_form": bool(CONTACT_INPUT_RE.search(html)),
        "email": email,
    }
