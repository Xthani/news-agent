from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import requests

from .pending_store import register_pending_item


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()

def _extract_retry_after_seconds(resp_json: dict[str, Any] | None) -> int | None:
    try:
        if not resp_json:
            return None
        params = resp_json.get("parameters") or {}
        if isinstance(params, dict):
            ra = params.get("retry_after")
            if ra is None:
                return None
            return int(ra)
    except Exception:
        return None
    return None


def _tg_post_with_retry(url: str, payload: dict[str, Any], *, max_retries: int = 5, timeout_s: int = 25) -> None:
    backoff = 0.8
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=timeout_s)
            if r.status_code == 429:
                try:
                    data = r.json() if isinstance(r.json(), dict) else {}
                except Exception:
                    data = {}
                ra = _extract_retry_after_seconds(data)
                time_sleep = max(ra or 1, 1)
                import time

                time.sleep(time_sleep)
                continue
            if 500 <= r.status_code <= 599:
                import time

                time.sleep(backoff)
                backoff = min(backoff * 1.7, 12.0)
                continue
            if not r.ok:
                raise RuntimeError(f"Telegram API: {r.status_code} {r.text}")
            return
        except Exception as e:
            last_err = e
            if attempt >= max_retries:
                break
            import time

            time.sleep(backoff)
            backoff = min(backoff * 1.7, 12.0)
    raise last_err or RuntimeError("Telegram send failed")


def _chunk_text(text: str, chunk_size: int = 3500) -> list[str]:
    """
    Telegram sendMessage limit is ~4096 chars. Use a safer chunk size.
    Keep it simple: split by paragraphs first, then hard-split long parts.
    """
    text = (text or "").strip()
    if not text:
        return []

    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    out: list[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            out.append(buf.strip())
        buf = ""

    for p in parts:
        if not buf:
            candidate = p
        else:
            candidate = buf + "\n\n" + p

        if len(candidate) <= chunk_size:
            buf = candidate
            continue

        flush()
        if len(p) <= chunk_size:
            buf = p
            continue

        # hard split
        for i in range(0, len(p), chunk_size):
            out.append(p[i : i + chunk_size].strip())

    flush()
    return out


def _strip_code_fences(text: str) -> str:
    t = (text or "").strip()
    t = t.replace("```json", "").replace("```", "").strip()
    return t


def _short_url(url: str) -> str:
    # For display only: remove protocol and keep it compact.
    u = (url or "").strip()
    u = u.replace("https://", "").replace("http://", "")
    return u


def _format_source_line(processed_result: dict[str, Any]) -> str:
    """
    Show attribution as an icon line (private only).
    - news: "🗞️ Marca"
    - opinion: "✍️ <author> · 🗞️ Marca"
    """
    source = "Marca"
    content_type = (processed_result.get("content_type") or "").strip()
    author = (processed_result.get("author") or "").strip()
    if content_type == "opinion":
        if author:
            return f"✍️ {author} · 🗞️ {source}"
        return f"✍️ Мнение · 🗞️ {source}"
    return f"🗞️ {source}"

def _format_published_at_for_private(raw: str) -> str:
    """
    Private preview only: render a compact human-readable datetime.
    Input is expected to be ISO 8601 (we normalize it in article_fetcher),
    but keep it defensive.
    """
    s = (raw or "").strip()
    if not s:
        return ""

    # unix seconds / ms
    if s.isdigit() and len(s) in (10, 13):
        try:
            ts = int(s)
            if len(s) == 13:
                ts = int(ts / 1000)
            dt = datetime.fromtimestamp(ts).astimezone()
            return dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            return s

    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.astimezone()
        else:
            dt = dt.astimezone()
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return s


def _format_private_preview(processed_result: dict[str, Any], article: dict[str, Any] | None = None) -> str:
    url = (processed_result.get("original_url") or "").strip()
    hero_image_url = (processed_result.get("hero_image_url") or "").strip()
    title_ru = (processed_result.get("private_title_ru") or processed_result.get("telegram_title") or "").strip()
    header = f"⚪️ {title_ru or '—'}"
    author = (processed_result.get("author") or "").strip()
    published_at = (processed_result.get("published_at") or "").strip()
    content_type = (processed_result.get("content_type") or "").strip()

    private_translation = (processed_result.get("private_translation") or "").strip()
    telegram_post = _strip_code_fences((processed_result.get("telegram_post") or "").strip())

    # If LLM hit rate limits, don't spam the user with long error blobs.
    if private_translation.startswith("LLM_ERROR"):
        private_translation = "Не удалось сделать перевод: лимит/ошибка LLM. Попробуй запустить позже."
    if telegram_post.startswith("LLM_ERROR"):
        telegram_post = "Не удалось сделать короткое резюме: лимит/ошибка LLM. Попробуй запустить позже."

    parts = [
        header,
        "",
        (telegram_post or "").strip() or None,
        "",
        "Перевод статьи (RU):",
        private_translation or "(empty)",
        "",
        (f"Дата: {_format_published_at_for_private(published_at)}" if published_at else None),
        _format_source_line(processed_result),
        (f"Картинка: {_short_url(hero_image_url)}" if hero_image_url else None),
        # Keep original URL only for private moderation/debug.
        f"Оригинал: {_short_url(url)}" if url else None,
    ]

    return "\n".join([p for p in parts if p is not None]).strip()


def send_private_preview(processed_result: dict[str, Any], article: dict[str, Any] | None = None) -> None:
    token = _env("TELEGRAM_BOT_TOKEN")
    chat_id = _env("TELEGRAM_PRIVATE_CHAT_ID")

    message = _format_private_preview(processed_result, article=article)

    if not token or not chat_id:
        print("\n" + "=" * 80)
        print(message)
        print("=" * 80 + "\n")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = _chunk_text(message, chunk_size=3500)
    if not chunks:
        return

    # Optional approval flow with inline buttons.
    # Works when `bot.py` is running (it listens to callback_query).
    approval_flow = (_env("TELEGRAM_APPROVAL_FLOW") or "true").lower() in {"1", "true", "yes", "y", "on"}
    original_url = (processed_result.get("original_url") or "").strip()
    item_id = register_pending_item(original_url, processed_result) if (approval_flow and original_url) else ""

    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True if i == 0 else True,
        }
        if approval_flow and i == len(chunks) - 1 and item_id:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [
                        {"text": "Сделать пост для канала", "callback_data": f"draft:{item_id}"},
                        {"text": "Пропустить", "callback_data": f"skip:{item_id}"},
                    ]
                ]
            }
        try:
            _tg_post_with_retry(url, payload, max_retries=5, timeout_s=25)
        except Exception as e:
            print(f"[telegram] failed to send private preview: {type(e).__name__}: {e}")
            print(message)
            break


def publish_to_channel(processed_result: dict[str, Any]) -> None:
    """
    Stage 1: функция готова, но НЕ вызывается автоматически.
    """
    token = _env("TELEGRAM_BOT_TOKEN")
    channel_id = _env("TELEGRAM_CHANNEL_CHAT_ID")
    if not token or not channel_id:
        print("[telegram] missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_CHAT_ID; skip publish")
        return

    post = (processed_result.get("telegram_post") or "").strip()
    photo_file_id = (processed_result.get("telegram_photo_file_id") or "").strip()
    if not post:
        print("[telegram] empty telegram_post; skip publish")
        return

    base = f"https://api.telegram.org/bot{token}"

    if photo_file_id:
        # Telegram caption limit is 1024 chars.
        caption = post
        remainder = ""
        if len(caption) > 1024:
            caption = caption[:1021].rstrip() + "…"
            remainder = post[1024:].lstrip()

        _tg_post_with_retry(
            f"{base}/sendPhoto",
            {
                "chat_id": channel_id,
                "photo": photo_file_id,
                "caption": caption,
                "disable_web_page_preview": True,
            },
            max_retries=5,
            timeout_s=25,
        )

        if remainder:
            _tg_post_with_retry(
                f"{base}/sendMessage",
                {
                    "chat_id": channel_id,
                    "text": remainder,
                    "disable_web_page_preview": True,
                },
                max_retries=5,
                timeout_s=25,
            )
        return

    _tg_post_with_retry(
        f"{base}/sendMessage",
        {
            "chat_id": channel_id,
            "text": post,
            "disable_web_page_preview": True,
        },
        max_retries=5,
        timeout_s=25,
    )
