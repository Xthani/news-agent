from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import Page

from .browser_manager import BrowserManager


@dataclass(frozen=True)
class RawArticle:
    source_id: str
    source_name: str
    original_url: str
    original_title: str
    subtitle: str | None
    author: str | None
    published_at: str | None
    hero_image_url: str | None
    full_text: str
    fetched_at: str
    error: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _normalize_published_at(raw: str | None) -> str | None:
    """
    Try to normalize article datetime into ISO 8601.
    Marca sometimes provides:
    - ISO strings (with Z or offset)
    - plain text dates
    - unix timestamps (seconds/ms)
    Keep it defensive: if parsing fails, return the original trimmed value.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None

    # Unix epoch timestamps: 10-digit seconds or 13-digit milliseconds
    if re.fullmatch(r"\d{10}", s):
        try:
            return datetime.fromtimestamp(int(s), tz=timezone.utc).isoformat()
        except Exception:
            return s
    if re.fullmatch(r"\d{13}", s):
        try:
            return datetime.fromtimestamp(int(s) / 1000.0, tz=timezone.utc).isoformat()
        except Exception:
            return s

    # ISO-ish: allow trailing Z
    iso_candidate = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(iso_candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        pass

    # Common human formats (keep it minimal, no extra deps):
    # - "28/04/2026 12:34"
    # - "28/04/2026"
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", s)
    if m:
        try:
            day = int(m.group(1))
            month = int(m.group(2))
            year = int(m.group(3))
            hh = int(m.group(4) or 0)
            mm = int(m.group(5) or 0)
            return datetime(year, month, day, hh, mm, tzinfo=timezone.utc).isoformat()
        except Exception:
            return s

    return s


def _collapse_ws(s: str) -> str:
    s = s.replace("\u00a0", " ")
    return re.sub(r"[ \t]+", " ", s).strip()


def _safe_text_on(page: Page, selector: str) -> str:
    try:
        loc = page.locator(selector).first
        return _collapse_ws(loc.inner_text())
    except Exception:
        return ""


def _safe_attr_on(page: Page, selector: str, attr: str) -> str:
    try:
        loc = page.locator(selector).first
        return (loc.get_attribute(attr) or "").strip()
    except Exception:
        return ""


def _extract_paragraphs(page: Page, selector: str) -> list[str]:
    try:
        loc = page.locator(selector)
        n = min(loc.count(), 400)
    except Exception:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for i in range(n):
        try:
            txt = _collapse_ws(loc.nth(i).inner_text())
        except Exception:
            continue
        if not txt:
            continue
        # heuristics to avoid UI noise
        if len(txt) < 25:
            continue
        if txt.lower().startswith(("suscríbete", "compartir", "síguenos", "publicidad")):
            continue
        if txt in seen:
            continue
        seen.add(txt)
        out.append(txt)
    return out


def fetch_article(site: dict[str, Any], url: str, browser: BrowserManager) -> RawArticle:
    source_id = str(site.get("id") or "").strip()
    source_name = str(site.get("name") or source_id).strip()
    selectors = site.get("article_selectors") or {}

    title_sel = str(selectors.get("title") or "h1")
    subtitle_sel = str(selectors.get("subtitle") or "h2")
    author_sel = str(selectors.get("author") or "[rel='author']")
    date_sel = str(selectors.get("date") or "time")
    image_sel = str(selectors.get("image") or "figure img")
    paras_sel = str(selectors.get("paragraphs") or "article p")

    page = browser.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)

        original_title = _safe_text_on(page, title_sel)
        subtitle = _safe_text_on(page, subtitle_sel) or None
        author = _safe_text_on(page, author_sel) or None

        published_at = None
        try:
            time_loc = page.locator(date_sel).first
            published_at = (
                (time_loc.get_attribute("datetime") or "").strip()
                or _collapse_ws(time_loc.inner_text())
                or None
            )
        except Exception:
            published_at = None
        published_at = _normalize_published_at(published_at)

        hero_image_url = _safe_attr_on(page, image_sel, "src") or _safe_attr_on(page, image_sel, "data-src") or ""
        hero_image_url = hero_image_url.strip() or None
        if hero_image_url:
            hero_image_url = urljoin(url, hero_image_url)

        paragraphs = _extract_paragraphs(page, paras_sel)
        full_text = "\n\n".join(paragraphs).strip()

        if not full_text:
            return RawArticle(
                source_id=source_id,
                source_name=source_name,
                original_url=url,
                original_title=original_title,
                subtitle=subtitle,
                author=author,
                published_at=published_at,
                hero_image_url=hero_image_url,
                full_text="",
                fetched_at=_now_iso(),
                error="EMPTY_ARTICLE_TEXT",
            )

        return RawArticle(
            source_id=source_id,
            source_name=source_name,
            original_url=url,
            original_title=original_title,
            subtitle=subtitle,
            author=author,
            published_at=published_at,
            hero_image_url=hero_image_url,
            full_text=full_text,
            fetched_at=_now_iso(),
            error=None,
        )
    except Exception as e:
        return RawArticle(
            source_id=source_id,
            source_name=source_name,
            original_url=url,
            original_title="",
            subtitle=None,
            author=None,
            published_at=None,
            hero_image_url=None,
            full_text="",
            fetched_at=_now_iso(),
            error=f"FETCH_FAILED: {type(e).__name__}: {e}",
        )
    finally:
        try:
            page.close()
        except Exception:
            pass
