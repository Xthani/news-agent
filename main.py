from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from modules.ai_processor import process_article
from modules.article_fetcher import fetch_article
from modules.article_store import add_new_article, load_index, save_processed_article, save_raw_article, update_status
from modules.browser_manager import BrowserManager
from modules.config_loader import load_sites, load_style_rules
from modules.listing_fetcher import fetch_listing_cards
from modules.publisher import send_private_preview


PROJECT_ROOT = Path(__file__).resolve().parent


def _env_bool(name: str, default: bool) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _log(msg: str) -> None:
    print(msg, flush=True)

def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _safe_rmtree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_file() or path.is_symlink():
        try:
            path.unlink()
        except Exception:
            pass
        return
    for p in sorted(path.rglob("*"), reverse=True):
        try:
            if p.is_file() or p.is_symlink():
                p.unlink()
            else:
                p.rmdir()
        except Exception:
            pass
    try:
        path.rmdir()
    except Exception:
        pass


def _clean_local_state() -> None:
    """
    Clean run: delete only agent-local state.
    - data/ (raw, processed, pending, bot_state, index)
    - browser_sessions/ (Playwright persistent profiles)
    """
    _safe_rmtree(PROJECT_ROOT / "data")
    _safe_rmtree(PROJECT_ROOT / "browser_sessions")


def run() -> int:
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    headless = _env_bool("HEADLESS", True)
    listing_only = _env_bool("LISTING_ONLY", False)
    max_new_articles = max(0, _env_int("MAX_NEW_ARTICLES_PER_RUN", 3))
    clean_run = _env_bool("CLEAN_RUN", False)
    if clean_run:
        _log("[startup] CLEAN_RUN=true → очищаю data/ и browser_sessions/")
        _clean_local_state()
    sites = load_sites()
    style_rules = load_style_rules()

    enabled_sites = [s for s in sites if (s.get("enabled", True) is True)]
    if not enabled_sites:
        _log("[config] no enabled sites; check config/sites.yaml")
        return 0

    _log(f"[config] enabled sources: {', '.join([str(s.get('id')) for s in enabled_sites])}")

    idx = load_index()
    known_urls = set((idx.get("articles") or {}).keys())

    with BrowserManager(session_name="marca", headless=headless) as browser:
        for site in enabled_sites:
            source_id = str(site.get("id") or "").strip()
            source_name = str(site.get("name") or source_id).strip()
            if not source_id:
                continue

            _log(f"\n[source] {source_name} ({source_id})")
            cards = fetch_listing_cards(site, browser)
            _log(f"[listing] found {len(cards)} cards")

            new_cards = [c for c in cards if c.link not in known_urls]
            _log(f"[listing] new URLs: {len(new_cards)}")

            if max_new_articles and len(new_cards) > max_new_articles:
                # Process only the newest few to save LLM quota.
                def _sort_key(c: Any) -> tuple[str, str]:
                    # Prefer stable date from URL (Marca embeds /YYYY/MM/DD/ in article URLs),
                    # then fallback to detected_at.
                    return (str(getattr(c, "published_at_guess", "") or ""), str(getattr(c, "detected_at", "") or ""))

                new_cards = sorted(new_cards, key=_sort_key, reverse=True)[:max_new_articles]
                _log(f"[listing] limit applied: processing {len(new_cards)} newest URLs")

            for c in new_cards:
                add_new_article(c)
                known_urls.add(c.link)

            if listing_only:
                _log("[listing] LISTING_ONLY=true → статьи не открываем, только сохраняем новые URL")
                continue

            for c in new_cards:
                url = c.link
                _log(f"\n[article] fetch: {url}")

                raw = fetch_article(site, url, browser)
                if raw.error:
                    _log(f"[article] failed: {raw.error}")
                    update_status(url, "failed", extra_data={"error": raw.error, "title": raw.original_title or c.title})
                    continue

                update_status(url, "parsed", extra_data={"error": None, "title": raw.original_title or c.title})
                raw_path = save_raw_article(raw)
                _log(f"[store] raw saved: {raw_path.relative_to(PROJECT_ROOT)}")

                processed = process_article(raw, style_rules)
                processed_path = save_processed_article(url, processed)
                _log(f"[store] processed saved: {processed_path.relative_to(PROJECT_ROOT)}")

                update_status(url, "preview_ready", extra_data={"error": None})

                # Don't attach full raw article text into Telegram preview (too large).
                send_private_preview(processed)
                _log("[preview] done (private)")

    _log("\n[done] pipeline finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
