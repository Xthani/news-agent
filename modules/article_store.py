from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .article_fetcher import RawArticle
from .listing_fetcher import ListingCard


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
INDEX_PATH = DATA_DIR / "articles.json"

RAW_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def safe_filename_for_url(url: str, suffix: str = ".json") -> str:
    return f"{_url_hash(url)}{suffix}"


def load_index() -> dict[str, Any]:
    if not INDEX_PATH.exists():
        return {"version": 1, "articles": {}}
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": 1, "articles": {}}
        if "articles" not in data or not isinstance(data.get("articles"), dict):
            data["articles"] = {}
        if "version" not in data:
            data["version"] = 1
        return data
    except Exception:
        return {"version": 1, "articles": {}}


def save_index(index: dict[str, Any]) -> None:
    # CLEAN_RUN can delete data/ after this module was imported.
    # Ensure directories exist right before writing.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    tmp.replace(INDEX_PATH)


def is_known_url(url: str, index: dict[str, Any] | None = None) -> bool:
    idx = index or load_index()
    return url in (idx.get("articles") or {})


def add_new_article(card: ListingCard) -> None:
    idx = load_index()
    articles: dict[str, Any] = idx["articles"]
    if card.link in articles:
        return
    articles[card.link] = {
        "url": card.link,
        "source_id": card.source_id,
        "title": card.title,
        "status": "new",
        "detected_at": card.detected_at,
        "fetched_at": None,
        "processed_at": None,
        "error": None,
    }
    save_index(idx)


def update_status(url: str, status: str, extra_data: dict[str, Any] | None = None) -> None:
    idx = load_index()
    articles: dict[str, Any] = idx["articles"]
    rec = articles.get(url) or {"url": url}
    rec["status"] = status

    if status in {"parsed", "failed"}:
        rec["fetched_at"] = rec.get("fetched_at") or _now_iso()
    if status in {"processed", "preview_ready", "published"}:
        rec["processed_at"] = rec.get("processed_at") or _now_iso()

    if extra_data:
        for k, v in extra_data.items():
            rec[k] = v

    articles[url] = rec
    save_index(idx)


def save_raw_article(article: RawArticle) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / safe_filename_for_url(article.original_url)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(article), f, ensure_ascii=False, indent=2)
    return path


def save_processed_article(url: str, processed_result: dict[str, Any]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = PROCESSED_DIR / safe_filename_for_url(url)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(processed_result, f, ensure_ascii=False, indent=2)
    return path
