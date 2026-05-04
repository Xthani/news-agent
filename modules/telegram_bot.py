from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .publisher import publish_to_channel
from .pending_store import load_pending, load_processed_by_url, save_pending
from .config_loader import load_style_rules


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
BOT_STATE_PATH = DATA_DIR / "bot_state.json"

PIPELINE_BUTTON_LABEL = "Получить новости"
_pipeline_lock = threading.Lock()


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _api_base(token: str) -> str:
    return f"https://api.telegram.org/bot{token}"

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


def _tg_request(
    token: str,
    method: str,
    http_method: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout_s: int = 35,
    max_retries: int = 5,
) -> dict[str, Any]:
    """
    Telegram API wrapper with retries:
    - respects 429 retry_after (if present)
    - exponential backoff on network/5xx
    """
    url = f"{_api_base(token)}/{method}"
    backoff = 0.8
    last_err: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            if http_method == "POST":
                r = requests.post(url, json=payload or {}, timeout=timeout_s)
            else:
                r = requests.get(url, params=params or {}, timeout=timeout_s)

            if r.status_code == 429:
                try:
                    data = r.json() if isinstance(r.json(), dict) else {}
                except Exception:
                    data = {}
                ra = _extract_retry_after_seconds(data)
                sleep_s = max(ra or 1, 1)
                time.sleep(sleep_s)
                continue

            if 500 <= r.status_code <= 599:
                # transient server errors
                time.sleep(backoff)
                backoff = min(backoff * 1.7, 12.0)
                continue

            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict) or not data.get("ok"):
                raise RuntimeError(f"Telegram API error: {data}")
            return data
        except Exception as e:
            last_err = e
            if attempt >= max_retries:
                break
            time.sleep(backoff)
            backoff = min(backoff * 1.7, 12.0)

    raise last_err or RuntimeError("Telegram API request failed")


def _tg_post(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _tg_request(token, method, "POST", payload=payload, timeout_s=35, max_retries=5)


def _tg_get(token: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return _tg_request(token, method, "GET", params=params, timeout_s=35, max_retries=5)


def _load_state() -> dict[str, Any]:
    if not BOT_STATE_PATH.exists():
        return {"offset": None, "awaiting_edit": None}
    try:
        return json.loads(BOT_STATE_PATH.read_text(encoding="utf-8")) or {"offset": None, "awaiting_edit": None}
    except Exception:
        return {"offset": None, "awaiting_edit": None}


def _save_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BOT_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    return {
        "inline_keyboard": [[{"text": text, "callback_data": cb} for (text, cb) in row] for row in rows],
    }


def _reply_keyboard_news_button() -> dict[str, Any]:
    return {
        "keyboard": [[{"text": PIPELINE_BUTTON_LABEL}]],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def _queue_pipeline_run(token: str, chat_id: str) -> None:
    if not _pipeline_lock.acquire(blocking=False):
        _send_message(token, chat_id, "Уже идёт сбор новостей — дождись окончания.")
        return

    _send_message(
        token,
        chat_id,
        "Запускаю сбор новостей (браузер + LLM). Обычно несколько минут — превью придут отдельными сообщениями.",
    )

    def worker() -> None:
        try:
            import main as app_main

            app_main.run()
        except Exception as e:
            _send_message(token, chat_id, f"Пайплайн упал: {type(e).__name__}: {e}")
        finally:
            _pipeline_lock.release()
            _send_message(token, chat_id, "Сбор завершён.")

    threading.Thread(target=worker, daemon=True).start()


def _send_message(token: str, chat_id: str, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _tg_post(token, "sendMessage", payload)


def _answer_callback(token: str, callback_query_id: str, text: str | None = None) -> None:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = False
    _tg_post(token, "answerCallbackQuery", payload)


def _short_url(url: str) -> str:
    u = (url or "").strip()
    return u.replace("https://", "").replace("http://", "")

def _format_channel_source_line(processed: dict[str, Any]) -> str:
    """
    Channel attribution rules:
    - news: "🗞️ Marca"
    - opinion: "✍️ <author> · 🗞️ Marca" (no date/time)
    """
    source = "Marca"
    content_type = str(processed.get("content_type") or "").strip()
    author = str(processed.get("author") or "").strip()
    if content_type == "opinion":
        if author:
            return f"✍️ {author} · 🗞️ {source}"
        return f"✍️ Мнение · 🗞️ {source}"
    return f"🗞️ {source}"


def _format_channel_draft_as_is(processed: dict[str, Any]) -> str:
    """
    "Как есть" по твоим правилам:
    - Заголовок (⚪️) — берем private_title_ru (fallback telegram_title)
    - Короткий пост (telegram_post)
    - Полный перевод для себя (private_translation) БЕЗ лейбла "Перевод статьи (RU):"
    - Внизу только иконка источника
    - Никаких ссылок/дат/картинок
    """
    title = (processed.get("private_title_ru") or processed.get("telegram_title") or "").strip()
    short_body = (processed.get("telegram_post") or "").strip()
    translation = (processed.get("private_translation") or "").strip()

    parts: list[str] = []
    if title:
        parts.append(f"⚪️ {title}")
        parts.append("")
    if short_body:
        parts.append(short_body)
        parts.append("")
    if translation and not translation.startswith("LLM_ERROR"):
        parts.append(translation)
        parts.append("")

    parts.append(_format_channel_source_line(processed))
    text = "\n".join([p for p in parts if p is not None]).strip()
    return text[:3500]


def _format_channel_draft(processed: dict[str, Any]) -> str:
    # For channel drafts we prefer the "private" RU title (it is usually more informative),
    # and falls back to telegram_title if missing.
    title = (processed.get("private_title_ru") or processed.get("telegram_title") or "").strip()
    body = (processed.get("telegram_post") or "").strip()

    if not body:
        body = "Черновик пустой."
    # keep it compact and always add attribution
    parts = []
    # If TELEGRAM_POST already contains its own headline (common in older runs / model drift),
    # do not prepend telegram_title to avoid "different versions" between preview and "as-is".
    body_lines = body.splitlines()
    first_non_empty = ""
    for ln in body_lines:
        if ln.strip():
            first_non_empty = ln.strip()
            break
    body_has_headline = first_non_empty.startswith("⚪️")

    if body_has_headline:
        parts.append(body)
    else:
        if title:
            parts.append(f"⚪️ {title}")
            parts.append("")
        parts.append(body)
    parts.append("")
    parts.append(_format_channel_source_line(processed))
    text = "\n".join(parts).strip()
    # Telegram safety trim (publish_to_channel handles raw text)
    return text[:3500]


def _ensure_channel_footer(text: str, processed: dict[str, Any]) -> str:
    """
    Make sure the final channel text always contains the source line.
    This protects against:
    - LLM refine dropping the footer
    - manual edits accidentally deleting it
    """
    t = (text or "").strip()
    if not t:
        return t
    footer = _format_channel_source_line(processed).strip()
    if not footer:
        return t
    # If footer already present (exact or without emoji spacing), keep as is.
    if footer in t:
        return t
    if "Marca" in t and ("🗞️" in t or "✍️" in t):
        return t
    return (t + "\n\n" + footer).strip()


def _format_full_preview(processed: dict[str, Any]) -> str:
    title = (processed.get("private_title_ru") or "").strip() or "—"
    url = (processed.get("original_url") or "").strip()
    translation = (processed.get("private_translation") or "").strip()
    if translation.startswith("LLM_ERROR"):
        translation = "Перевод недоступен (лимит/ошибка LLM). Запусти позже."
    text = f"Заголовок: {title}\n\nПеревод статьи (RU):\n{translation}\n\n🗞️ Marca\nОригинал: {_short_url(url)}"
    return text


## processed loading moved to modules/pending_store.py


def _handle_callback(token: str, private_chat_id: str, cb: dict[str, Any], state: dict[str, Any]) -> None:
    cb_id = str(cb.get("id") or "")
    data = str(cb.get("data") or "")
    message = cb.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")

    # Only accept actions from the configured private chat
    if chat_id != str(private_chat_id):
        _answer_callback(token, cb_id, "Не тот чат.")
        return

    if data == "news:run":
        _answer_callback(token, cb_id, "Запускаю…")
        _queue_pipeline_run(token, private_chat_id)
        return

    if ":" not in data:
        _answer_callback(token, cb_id, "Неизвестная команда.")
        return

    action, item_id = data.split(":", 1)
    pending = load_pending(item_id)
    if not pending:
        _answer_callback(token, cb_id, "Не нашёл запись (возможно, уже обработано).")
        return

    original_url = str(pending.get("original_url") or "")
    processed = load_processed_by_url(original_url) if original_url else None
    if not processed:
        _answer_callback(token, cb_id, "Не нашёл processed JSON.")
        return

    if action == "skip":
        pending["status"] = "skipped"
        save_pending(item_id, pending)
        _answer_callback(token, cb_id, "Пропущено.")
        return

    if action == "draft":
        _send_message(
            token,
            private_chat_id,
            "Как подготовить пост для канала?",
            reply_markup=_inline_keyboard(
                [
                    [("Как есть", f"draft_as_is:{item_id}"), ("Сжать сильнее", f"draft_refine:{item_id}")],
                    [("Отмена", f"skip:{item_id}")],
                ]
            ),
        )
        _answer_callback(token, cb_id, "Черновик готов.")
        return

    if action == "draft_as_is":
        # Always rebuild "as-is" from processed to avoid stale state
        # (e.g. user clicked refine first, older pending stored wrong data).
        draft_as_is = _format_channel_draft_as_is(processed)
        draft_as_is = _ensure_channel_footer(draft_as_is, processed)
        pending["draft_text_as_is"] = draft_as_is

        draft = draft_as_is
        pending["draft_text"] = draft
        pending["status"] = "draft_ready"
        save_pending(item_id, pending)
        _send_message(
            token,
            private_chat_id,
            "Черновик для канала (как есть):\n\n" + draft,
            reply_markup=_inline_keyboard(
                [
                    [("Редактировать", f"edit:{item_id}"), ("Опубликовать", f"publish:{item_id}")],
                    [("Отмена", f"skip:{item_id}")],
                ]
            ),
        )
        _answer_callback(token, cb_id, "Ок.")
        return

    if action == "draft_refine":
        # Optional extra compression by LLM (spends tokens).
        # Always refine from the AS-IS draft to avoid carrying over prior refine result.
        draft_as_is = str(pending.get("draft_text_as_is") or "").strip()
        if not draft_as_is:
            draft_as_is = _ensure_channel_footer(_format_channel_draft(processed), processed)
            pending["draft_text_as_is"] = draft_as_is

        base_body = str(processed.get("telegram_post") or "").strip()
        draft = draft_as_is
        try:
            from .ai_processor import refine_channel_post

            refined = refine_channel_post(base_body or draft, load_style_rules())
            refined_body = str(refined.get("telegram_post") or "").strip()
            if refined_body:
                processed_ref = dict(processed)
                processed_ref["telegram_post"] = refined_body
                draft = _format_channel_draft(processed_ref)
        except Exception as e:
            _send_message(token, private_chat_id, f"Не удалось сжать дополнительно: {e}\n\nОставляю как есть.")

        draft = _ensure_channel_footer(draft, processed)
        pending["draft_text_refined"] = draft
        pending["draft_text"] = draft
        pending["status"] = "draft_ready"
        save_pending(item_id, pending)
        _send_message(
            token,
            private_chat_id,
            "Черновик для канала (сжат сильнее):\n\n" + draft,
            reply_markup=_inline_keyboard(
                [
                    [("Редактировать", f"edit:{item_id}"), ("Опубликовать", f"publish:{item_id}")],
                    [("Отмена", f"skip:{item_id}")],
                ]
            ),
        )
        _answer_callback(token, cb_id, "Сжато.")
        return

    if action == "edit":
        state["awaiting_edit"] = {"item_id": item_id}
        _save_state(state)
        _answer_callback(token, cb_id, "Ок. Пришли следующий текст одним сообщением.")
        _send_message(token, private_chat_id, "Пришли отредактированный текст для канала одним сообщением.")
        return

    if action == "publish":
        draft = str(pending.get("draft_text") or "").strip()
        if not draft:
            draft = _format_channel_draft(processed)
            pending["draft_text"] = draft
            save_pending(item_id, pending)

        draft = _ensure_channel_footer(draft, processed)

        try:
            publish_to_channel(
                {
                    "telegram_post": draft,
                    "telegram_photo_file_id": str(pending.get("draft_photo_file_id") or "").strip() or None,
                }
            )
        except Exception as e:
            _answer_callback(token, cb_id, f"Ошибка публикации: {type(e).__name__}")
            _send_message(token, private_chat_id, f"Не удалось опубликовать: {e}")
            return

        pending["status"] = "published"
        save_pending(item_id, pending)
        _answer_callback(token, cb_id, "Опубликовано.")
        _send_message(token, private_chat_id, "Опубликовано в канал.")
        return

    _answer_callback(token, cb_id, "Неизвестное действие.")


def _handle_message(token: str, private_chat_id: str, msg: dict[str, Any], state: dict[str, Any]) -> None:
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if chat_id != str(private_chat_id):
        return

    text_raw = str(msg.get("text") or "").strip()
    first_token = text_raw.split(maxsplit=1)[0] if text_raw else ""

    if first_token.startswith("/start"):
        _send_message(
            token,
            private_chat_id,
            "Нажми «"
            + PIPELINE_BUTTON_LABEL
            + "» или отправь /news — запустится тот же цикл, что и у main.py "
            "(до свежих статей за раз по лимиту MAX_NEW_ARTICLES_PER_RUN в .env). "
            "Дальше работают кнопки под превью, как раньше.",
            reply_markup=_reply_keyboard_news_button(),
        )
        return

    if first_token == "/news" or first_token.startswith("/news@"):
        _queue_pipeline_run(token, private_chat_id)
        return

    if text_raw == PIPELINE_BUTTON_LABEL:
        _queue_pipeline_run(token, private_chat_id)
        return

    awaiting = state.get("awaiting_edit")
    if not awaiting:
        return

    item_id = str(awaiting.get("item_id") or "")
    text = str(msg.get("text") or msg.get("caption") or "").strip()
    photos = msg.get("photo") or []
    photo_file_id: str | None = None
    if isinstance(photos, list) and photos:
        # pick the biggest photo
        best = None
        best_score = -1
        for p in photos:
            if not isinstance(p, dict):
                continue
            fid = str(p.get("file_id") or "").strip()
            if not fid:
                continue
            score = int(p.get("file_size") or 0)
            if score > best_score:
                best_score = score
                best = fid
        photo_file_id = best

    if not item_id or (not text and not photo_file_id):
        return

    pending = load_pending(item_id)
    if not pending:
        state["awaiting_edit"] = None
        _save_state(state)
        _send_message(token, private_chat_id, "Не нашёл pending запись для редактирования.")
        return

    if text:
        pending["draft_text"] = text[:3500]
    else:
        pending["draft_text"] = str(pending.get("draft_text") or "").strip()
    if photo_file_id:
        pending["draft_photo_file_id"] = photo_file_id
    pending["status"] = "draft_ready"
    save_pending(item_id, pending)

    state["awaiting_edit"] = None
    _save_state(state)

    _send_message(
        token,
        private_chat_id,
        "Принял правки. Вот финальная версия:\n\n"
        + pending["draft_text"]
        + ("\n\n(Картинка будет приложена при публикации)" if pending.get("draft_photo_file_id") else ""),
        reply_markup=_inline_keyboard([[("Опубликовать", f"publish:{item_id}"), ("Отмена", f"skip:{item_id}")]]),
    )


@dataclass
class BotRunner:
    token: str
    private_chat_id: str

    def run_forever(self) -> None:
        state = _load_state()
        offset = state.get("offset")

        while True:
            params: dict[str, Any] = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset

            try:
                data = _tg_get(self.token, "getUpdates", params)
            except Exception as e:
                print(f"[bot] getUpdates error: {type(e).__name__}: {e}")
                time.sleep(2)
                continue

            updates = data.get("result") or []
            if not isinstance(updates, list) or not updates:
                time.sleep(0.2)
                continue

            for upd in updates:
                if not isinstance(upd, dict):
                    continue
                upd_id = int(upd.get("update_id") or 0)
                offset = upd_id + 1
                state["offset"] = offset
                _save_state(state)

                if "callback_query" in upd and isinstance(upd["callback_query"], dict):
                    _handle_callback(self.token, self.private_chat_id, upd["callback_query"], state)
                    continue

                if "message" in upd and isinstance(upd["message"], dict):
                    _handle_message(self.token, self.private_chat_id, upd["message"], state)
                    continue


## register_pending_item moved to modules/pending_store.py to avoid circular imports
