from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .article_store import DATA_DIR, PROCESSED_DIR, safe_filename_for_url


PENDING_DIR = DATA_DIR / "pending"


def _ensure_pending_dir() -> None:
    # CLEAN_RUN can delete data/ after module import; ensure at use-time.
    try:
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def pending_path(item_id: str) -> Path:
    _ensure_pending_dir()
    return PENDING_DIR / f"{item_id}.json"


def load_pending(item_id: str) -> dict[str, Any] | None:
    _ensure_pending_dir()
    p = pending_path(item_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_pending(item_id: str, data: dict[str, Any]) -> None:
    _ensure_pending_dir()
    pending_path(item_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def register_pending_item(original_url: str, processed: dict[str, Any]) -> str:
    """
    Called by pipeline when a new article is ready.
    Returns item_id used for callback_data.
    """
    item_id = Path(safe_filename_for_url(original_url)).stem
    pending = {
        "item_id": item_id,
        "original_url": original_url,
        "status": "preview_sent",
        "private_title_ru": processed.get("private_title_ru"),
    }
    save_pending(item_id, pending)
    return item_id


def load_processed_by_url(url: str) -> dict[str, Any] | None:
    path = PROCESSED_DIR / safe_filename_for_url(url)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

