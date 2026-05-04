from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from playwright.sync_api import Page
import requests
import os
import xml.etree.ElementTree as ET

from .browser_manager import BrowserManager


_MARCA_HOST_RE = re.compile(r"(^|\.)marca\.com$", re.IGNORECASE)
_MARCA_RM_ARTICLE_PATH_RE = re.compile(
    r"^/futbol/real-madrid/(?:opinion/)?\d{4}/\d{2}/\d{2}/.+\.html$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ListingCard:
    source_id: str
    source_name: str
    title: str
    link: str
    image_url: str | None
    detected_at: str
    published_at_guess: str | None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _guess_published_at_from_url(url: str) -> str | None:
    """
    Marca article URLs include date segments: /YYYY/MM/DD/.
    Use it as a stable "newest first" key when listing order is unreliable.
    """
    try:
        p = urlparse(url)
        m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", p.path or "")
        if not m:
            return None
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return datetime(y, mo, d, tzinfo=timezone.utc).isoformat()
    except Exception:
        return None


def _safe_text(page: Page, selector: str) -> str:
    try:
        loc = page.locator(selector).first
        txt = loc.inner_text().strip()
        return re.sub(r"\s+", " ", txt)
    except Exception:
        return ""


def _safe_attr(page: Page, selector: str, attr: str) -> str:
    try:
        loc = page.locator(selector).first
        val = loc.get_attribute(attr)
        return (val or "").strip()
    except Exception:
        return ""


def _extract_anchor_candidates(page: Page) -> list[dict[str, str]]:
    try:
        rows = page.eval_on_selector_all(
            "a[href]",
            """(els) => els.map(a => ({
              href: a.getAttribute('href') || '',
              text: (a.textContent || '').trim(),
              aria: a.getAttribute('aria-label') || '',
              title: a.getAttribute('title') || '',
              data: a.getAttribute('data-mrf-link') || ''
            }))""",
        )
        if isinstance(rows, list):
            out: list[dict[str, str]] = []
            for r in rows:
                if isinstance(r, dict):
                    out.append({k: str(v or "") for k, v in r.items()})
            return out
    except Exception:
        pass
    return []

def _normalize_candidate_url(url: str) -> str:
    """
    - Remove fragments (#...) so we don't treat "comments anchor" as a new article.
    - For article URLs, also drop query params (usually tracking).
    """
    try:
        p = urlparse(url)
    except Exception:
        return url

    fragment = ""
    query = p.query
    if _MARCA_RM_ARTICLE_PATH_RE.match(p.path or "") is not None:
        query = ""

    return urlunparse((p.scheme, p.netloc, p.path, p.params, query, fragment))


def _extract_hrefs_via_requests(url: str) -> list[str]:
    try:
        r = requests.get(
            url,
            headers={
                "user-agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            },
            timeout=25,
        )
        r.raise_for_status()
        html = r.text
    except Exception:
        return []

    # quick & dirty href extraction (no extra deps)
    return re.findall(r'href="([^"]+)"', html, flags=re.IGNORECASE)

def _extract_hrefs_from_local_html(path: Path) -> list[str]:
    try:
        html = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    return re.findall(r'href="([^"]+)"', html, flags=re.IGNORECASE)


def _extract_article_urls_from_marca_rss() -> list[str]:
    # RSS link is visible on the listing page and is quite stable.
    rss_url = "https://www.marca.com/rss/googlenews/futbol/real-madrid.xml"
    try:
        r = requests.get(rss_url, headers={"user-agent": "Mozilla/5.0"}, timeout=25)
        r.raise_for_status()
        xml_text = r.text
    except Exception:
        return []

    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []

    urls: list[str] = []
    for elem in root.iter():
        if elem.tag.lower().endswith("link") and elem.text:
            u = elem.text.strip()
            if u:
                urls.append(u)
    return urls


def _is_probably_article_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    if not _MARCA_HOST_RE.search(parsed.netloc):
        return False
    # Only accept real article URLs (exclude the section root /futbol/real-madrid.html)
    return _MARCA_RM_ARTICLE_PATH_RE.match(parsed.path or "") is not None


def fetch_listing_cards(site: dict[str, Any], browser: BrowserManager) -> list[ListingCard]:
    source_id = str(site.get("id") or "").strip()
    source_name = str(site.get("name") or source_id).strip()
    url = str(site.get("url") or "").strip()
    selectors = site.get("listing_selectors") or {}

    if not source_id or not url:
        return []

    cards_selector = str(selectors.get("articles") or "").strip() or "article"
    title_selector = str(selectors.get("title") or "").strip() or "h2, h3, a"
    link_selector = str(selectors.get("link") or "").strip() or "a"
    image_selector = str(selectors.get("image") or "").strip() or "img"

    page = browser.new_page()
    try:
        # Optional: use a saved HTML file (offline / stable extraction).
        local_html = (os.getenv("LISTING_LOCAL_HTML") or "").strip()
        if local_html:
            hrefs = _extract_hrefs_from_local_html(Path(local_html))
            seen: set[str] = set()
            out: list[ListingCard] = []
            for href in hrefs:
                absolute = _normalize_candidate_url(urljoin(url, href))
                if absolute in seen:
                    continue
                if not _is_probably_article_url(absolute):
                    continue
                seen.add(absolute)
                out.append(
                    ListingCard(
                        source_id=source_id,
                        source_name=source_name,
                        title="",
                        link=absolute,
                        image_url=None,
                        detected_at=_now_iso(),
                        published_at_guess=_guess_published_at_from_url(absolute),
                    )
                )
            return out

        page.goto(url, wait_until="domcontentloaded")
        # Marca renders many links via JS; be patient and allow lazy blocks.
        try:
            page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass

        seen: set[str] = set()
        out: list[ListingCard] = []

        def harvest() -> None:
            anchors = _extract_anchor_candidates(page)
            for a in anchors:
                href = (a.get("href") or "").strip()
                if not href:
                    continue
                absolute = _normalize_candidate_url(urljoin(url, href))
                if absolute in seen:
                    continue
                if not _is_probably_article_url(absolute):
                    continue
                seen.add(absolute)

                title = (
                    (a.get("text") or "").strip()
                    or (a.get("aria") or "").strip()
                    or (a.get("title") or "").strip()
                )
                title = re.sub(r"\s+", " ", title).strip()

                out.append(
                    ListingCard(
                        source_id=source_id,
                        source_name=source_name,
                        title=title,
                        link=absolute,
                        image_url=None,
                        detected_at=_now_iso(),
                        published_at_guess=_guess_published_at_from_url(absolute),
                    )
                )

        # 1) Primary approach: collect anchors; retry a few times with scroll.
        for _ in range(8):
            harvest()
            if out:
                break
            try:
                page.mouse.wheel(0, 1800)
            except Exception:
                pass
            page.wait_for_timeout(900)

        # 2) Fallback (optional): if Playwright returns nothing, try HTML via requests.
        if not out:
            try:
                total_a = page.locator("a[href]").count()
            except Exception:
                total_a = -1
            try:
                sample = page.eval_on_selector_all(
                    "a[href]",
                    "(els) => els.slice(0, 8).map(a => a.getAttribute('href') || '')",
                )
            except Exception:
                sample = []
            print(f"[listing][debug] playwright found 0 article links; total a[href]={total_a}; sample={sample}")

            hrefs = _extract_hrefs_via_requests(url)
            for href in hrefs:
                href = (href or "").strip()
                if not href:
                    continue
                absolute = _normalize_candidate_url(urljoin(url, href))
                if absolute in seen:
                    continue
                if not _is_probably_article_url(absolute):
                    continue
                seen.add(absolute)
                out.append(
                    ListingCard(
                        source_id=source_id,
                        source_name=source_name,
                        title="",
                        link=absolute,
                        image_url=None,
                        detected_at=_now_iso(),
                        published_at_guess=_guess_published_at_from_url(absolute),
                    )
                )

        # 2b) Fallback: if listing HTML is JS-empty (common), use RSS feed.
        if not out:
            rss_urls = _extract_article_urls_from_marca_rss()
            for u in rss_urls:
                absolute = _normalize_candidate_url(u)
                if absolute in seen:
                    continue
                if not _is_probably_article_url(absolute):
                    continue
                seen.add(absolute)
                out.append(
                    ListingCard(
                        source_id=source_id,
                        source_name=source_name,
                        title="",
                        link=absolute,
                        image_url=None,
                        detected_at=_now_iso(),
                        published_at_guess=_guess_published_at_from_url(absolute),
                    )
                )

        # 3) Keep old selector-based logic as a supplement (can add titles/images),
        # but only if we want to enrich; do not require it for link discovery.
        if out:
            return out

        # Legacy approach (unlikely to be needed now, but kept defensive)
        cards_loc = page.locator(cards_selector)
        count = min(cards_loc.count(), 80)
        for i in range(count):
            card = cards_loc.nth(i)
            try:
                href = card.locator(link_selector).first.get_attribute("href") or ""
            except Exception:
                href = ""
            href = href.strip()
            if not href:
                continue

            absolute = urljoin(url, href)
            if absolute in seen:
                continue
            if not _is_probably_article_url(absolute):
                continue
            seen.add(absolute)

            try:
                title = card.locator(title_selector).first.inner_text().strip()
            except Exception:
                title = ""
            title = re.sub(r"\s+", " ", title).strip()

            image_url: str | None = None
            try:
                img = card.locator(image_selector).first
                image_url = (img.get_attribute("src") or img.get_attribute("data-src") or "").strip() or None
                if image_url:
                    image_url = urljoin(url, image_url)
            except Exception:
                image_url = None

            out.append(
                ListingCard(
                    source_id=source_id,
                    source_name=source_name,
                    title=title,
                    link=absolute,
                    image_url=image_url,
                    detected_at=_now_iso(),
                    published_at_guess=_guess_published_at_from_url(absolute),
                )
            )

        return out
    finally:
        try:
            page.close()
        except Exception:
            pass
