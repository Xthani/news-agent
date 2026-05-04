from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict
from typing import Any, Literal

from openai import OpenAI
import requests

from .article_fetcher import RawArticle


Provider = Literal[
    "groq",
    "openai",
    "deepseek",
    "openrouter",
    "together",
    "sambanova",
    "cerebras",
    "cloudflare",
    "huggingface",
    "gemini",
    "dry_run",
]


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 3].rstrip() + "..."


def _dedupe_lines(text: str, max_lines: int = 60) -> str:
    """
    Remove exact duplicate lines/paragraphs (common LLM failure mode).
    """
    t = (text or "").strip()
    if not t:
        return t

    lines = [ln.rstrip() for ln in t.splitlines()]
    out: list[str] = []
    seen: set[str] = set()
    for ln in lines[: max_lines + 200]:
        key = ln.strip()
        if not key:
            out.append("")
            continue
        norm = re.sub(r"\s+", " ", key).lower()
        if norm in seen:
            continue
        seen.add(norm)
        out.append(ln)
        if len(out) >= max_lines:
            # still allow empty lines to keep formatting stable
            pass
    # collapse multiple empty lines
    cleaned: list[str] = []
    empty_run = 0
    for ln in out:
        if ln.strip() == "":
            empty_run += 1
            if empty_run <= 1:
                cleaned.append("")
            continue
        empty_run = 0
        cleaned.append(ln)
    return "\n".join(cleaned).strip()


def _micro_paragraphs(text: str, max_sentences_per_paragraph: int = 1) -> str:
    """
    Ensure micro-paragraph formatting: split by sentences into short paragraphs.
    If text already has blank lines, keep it.
    """
    t = (text or "").strip()
    if not t:
        return t
    if "\n\n" in t:
        return t

    # Split on sentence boundaries.
    parts = re.split(r"(?<=[.!?…])\s+", t)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= 1:
        return t

    out: list[str] = []
    buf: list[str] = []
    for p in parts:
        buf.append(p)
        if len(buf) >= max_sentences_per_paragraph:
            out.append(" ".join(buf).strip())
            buf = []
    if buf:
        out.append(" ".join(buf).strip())

    return "\n\n".join(out).strip()

def _build_backends() -> list[dict[str, Any]]:
    groq_key = (os.getenv("GROQ_API_KEY") or "").strip()
    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    deepseek_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    openrouter_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    together_key = (os.getenv("TOGETHER_API_KEY") or "").strip()
    sambanova_key = (os.getenv("SAMBANOVA_API_KEY") or "").strip()
    cerebras_key = (os.getenv("CEREBRAS_API_KEY") or "").strip()
    cf_account_id = (os.getenv("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    cf_token = (os.getenv("CLOUDFLARE_API_TOKEN") or "").strip()
    hf_token = (os.getenv("HF_TOKEN") or "").strip()
    gemini_key = (os.getenv("GEMINI_API_KEY") or "").strip()

    out: list[dict[str, Any]] = []

    if groq_key:
        out.append(
            {
                "provider": "groq",
                "model": (os.getenv("GROQ_MODEL") or "").strip() or "llama-3.3-70b-versatile",
                "client": OpenAI(api_key=groq_key, base_url="https://api.groq.com/openai/v1"),
            }
        )

    if openai_key:
        out.append(
            {
                "provider": "openai",
                "model": (os.getenv("OPENAI_MODEL") or "").strip() or "gpt-4o-mini",
                "client": OpenAI(api_key=openai_key),
            }
        )

    if deepseek_key:
        out.append(
            {
                "provider": "deepseek",
                "model": (os.getenv("DEEPSEEK_MODEL") or "").strip() or "deepseek-chat",
                "client": OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com/v1"),
            }
        )

    if openrouter_key:
        out.append(
            {
                "provider": "openrouter",
                "model": (os.getenv("OPENROUTER_MODEL") or "").strip() or "openai/gpt-4o-mini",
                "client": OpenAI(api_key=openrouter_key, base_url="https://openrouter.ai/api/v1"),
            }
        )

    if together_key:
        out.append(
            {
                "provider": "together",
                "model": (os.getenv("TOGETHER_MODEL") or "").strip() or "meta-llama/Llama-3.3-70B-Instruct-Turbo",
                "client": OpenAI(api_key=together_key, base_url="https://api.together.xyz/v1"),
            }
        )

    if sambanova_key:
        out.append(
            {
                "provider": "sambanova",
                "model": (os.getenv("SAMBANOVA_MODEL") or "").strip() or "Meta-Llama-3.1-70B-Instruct",
                # SambaNova is OpenAI-compatible
                "client": OpenAI(api_key=sambanova_key, base_url="https://api.sambanova.ai/v1"),
            }
        )

    if cerebras_key:
        out.append(
            {
                "provider": "cerebras",
                "model": (os.getenv("CEREBRAS_MODEL") or "").strip() or "gpt-oss-120b",
                # Cerebras is OpenAI-compatible
                "client": OpenAI(api_key=cerebras_key, base_url="https://api.cerebras.ai/v1"),
            }
        )

    if cf_account_id and cf_token:
        out.append(
            {
                "provider": "cloudflare",
                "model": (os.getenv("CLOUDFLARE_MODEL") or "").strip() or "@cf/meta/llama-3.1-8b-instruct",
                "account_id": cf_account_id,
                "api_key": cf_token,
            }
        )

    if hf_token:
        out.append(
            {
                "provider": "huggingface",
                "model": (os.getenv("HF_MODEL") or "").strip() or "HuggingFaceH4/zephyr-7b-beta",
                "api_key": hf_token,
            }
        )

    if gemini_key:
        out.append(
            {
                "provider": "gemini",
                "model": (os.getenv("GEMINI_MODEL") or "").strip() or "gemini-flash-latest",
                "api_key": gemini_key,
            }
        )

    return out


def _fill_template(tpl: str, article: RawArticle) -> str:
    data = asdict(article)
    original_url = (data.get("original_url") or "").strip()
    content_type = "opinion" if "/opinion/" in original_url else "news"
    return tpl.format(
        source_name=data.get("source_name") or "",
        original_url=data.get("original_url") or "",
        title=data.get("original_title") or "",
        subtitle=data.get("subtitle") or "",
        author=data.get("author") or "",
        published_at=data.get("published_at") or "",
        content_type=content_type,
        full_text=data.get("full_text") or "",
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    # Sometimes model wraps JSON in markdown fences.
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        # Try best-effort: find first {...} block
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None


def _is_rate_limit_error(e: Exception) -> bool:
    msg = str(e)
    return "RateLimitError" in msg or "429" in msg or "rate limit" in msg.lower()


def _ask_openai_compat(client: OpenAI, model: str, system: str, user: str, temperature: float) -> str:
    r = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (r.choices[0].message.content or "").strip()


def _ask_gemini(api_key: str, model: str, system: str, user: str) -> str:
    # Gemini API doesn't have chat roles in this endpoint; combine as one prompt.
    prompt = f"SYSTEM:\n{system}\n\nUSER:\n{user}"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    r = requests.post(url, headers={"Content-Type": "application/json", "X-goog-api-key": api_key}, json=payload, timeout=40)
    r.raise_for_status()
    data = r.json() if isinstance(r.json(), dict) else {}
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    content = (candidates[0].get("content") or {})
    parts = content.get("parts") or []
    texts: list[str] = []
    for p in parts:
        t = (p.get("text") or "").strip()
        if t:
            texts.append(t)
    return "\n".join(texts).strip()

def _ask_cloudflare(account_id: str, api_token: str, model: str, system: str, user: str) -> str:
    """
    Cloudflare Workers AI REST API:
    POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}
    Body: { "prompt": "..." }
    """
    prompt = f"SYSTEM:\n{system}\n\nUSER:\n{user}\n\nASSISTANT:\n"
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
        json={
            "prompt": prompt,
            # conservative defaults; model-specific knobs differ
            "max_tokens": 1200,
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        return ""
    if data.get("success") is False and data.get("errors"):
        raise RuntimeError(str(data.get("errors")))
    result = data.get("result")
    if isinstance(result, dict):
        for k in ("response", "generated_text", "text", "output"):
            v = result.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if isinstance(result, str) and result.strip():
        return result.strip()
    return ""


def _ask_huggingface(api_key: str, model: str, system: str, user: str) -> str:
    """
    Hugging Face Inference API.
    Works best with text-generation / instruct models.
    """
    prompt = f"SYSTEM:\n{system}\n\nUSER:\n{user}\n\nASSISTANT:\n"
    url = f"https://api-inference.huggingface.co/models/{model}"
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": 1200,
                "temperature": 0.2,
                "return_full_text": False,
            },
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()

    # Common shapes:
    # - [{"generated_text": "..."}]
    # - {"generated_text": "..."}
    if isinstance(data, list) and data and isinstance(data[0], dict):
        txt = (data[0].get("generated_text") or "").strip()
        return txt
    if isinstance(data, dict):
        txt = (data.get("generated_text") or "").strip()
        if txt:
            return txt
        # some models return { "error": ... } on loading
        if data.get("error"):
            raise RuntimeError(str(data.get("error")))
    return ""

def _ask_with_backends(
    backends: list[dict[str, Any]],
    system: str,
    user: str,
    temperature: float,
    max_retries: int = 1,
) -> tuple[str, str, str]:
    """
    Returns: (provider, model, text)
    Tries providers in order. If rate-limited, falls back to the next provider.
    """
    last_err: Exception | None = None
    for backend in backends:
        provider = backend["provider"]
        model = backend["model"]
        for attempt in range(max_retries + 1):
            try:
                if provider in {"groq", "openai", "deepseek", "openrouter", "together", "sambanova", "cerebras"}:
                    text = _ask_openai_compat(backend["client"], model, system, user, temperature)
                elif provider == "cloudflare":
                    text = _ask_cloudflare(
                        backend["account_id"],
                        backend["api_key"],
                        model,
                        system,
                        user,
                    )
                elif provider == "huggingface":
                    text = _ask_huggingface(backend["api_key"], model, system, user)
                elif provider == "gemini":
                    text = _ask_gemini(backend["api_key"], model, system, user)
                else:
                    raise RuntimeError(f"Unknown provider: {provider}")
                return provider, model, text
            except Exception as e:
                last_err = e
                if _is_rate_limit_error(e):
                    time.sleep(0.4 + attempt * 0.6)
                    continue
                break
        # if rate-limited (or temporary) - try next backend
        if last_err and _is_rate_limit_error(last_err):
            continue
        # non-rate limit error: try next backend as well
        continue
    raise last_err or RuntimeError("LLM request failed")

def _looks_russian(text: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", text or ""))

def _strip_title_trailing_dot(title: str) -> str:
    t = (title or "").strip()
    # Title should not end with a dot. Keep other punctuation (?!…).
    while t.endswith("."):
        t = t[:-1].rstrip()
    return t


def _translate_title_ru(client: OpenAI, model: str, title: str) -> str:
    title = (title or "").strip()
    if not title:
        return ""
    if _looks_russian(title):
        return title
    system = "Ты переводчик спортивных заголовков. Переводи на русский кратко и точно."
    user = (
        "Переведи заголовок на русский. Верни только одну строку без кавычек.\n\n"
        f"Заголовок: {title}"
    )
    try:
        out = _ask_openai_compat(client, model, system, user, temperature=0.1)
        out = re.sub(r"\s+", " ", out).strip()
        return out
    except Exception:
        return title


def _translate_article_ru(client: OpenAI, model: str, article: RawArticle) -> tuple[str, str]:
    """
    Returns: (title_ru, body_ru). Body is full translation/adaptation for private reading.
    """
    title_ru = _translate_title_ru(client, model, article.original_title)
    system = (
        "Ты профессиональный русскоязычный редактор и переводчик спортивных новостей.\n"
        "Переведи/адаптируй статью на русский для личного чтения.\n"
        "Важно: сохрани факты, имена, числа, цитаты. Ничего не выдумывай.\n"
        "Верни ответ строго в формате:\n"
        "BODY_RU:\n"
        "<текст перевода>\n"
        "Не используй JSON и не используй ```."
    )
    user = (
        f"Источник: {article.source_name}\n"
        f"Оригинал: {article.original_url}\n\n"
        f"Оригинальный заголовок: {article.original_title}\n"
        f"Подзаголовок: {article.subtitle or ''}\n\n"
        f"Текст статьи:\n{article.full_text}"
    )
    raw = _ask_openai_compat(client, model, system, user, temperature=0.3)
    m = re.search(r"BODY_RU:\s*([\s\S]+)", raw)
    body = (m.group(1) if m else raw).strip()
    return title_ru, body


def process_article(article: RawArticle, style_rules: dict[str, Any]) -> dict[str, Any]:
    backends = _build_backends()
    provider: Provider = backends[0]["provider"] if backends else "dry_run"
    model = backends[0]["model"] if backends else None
    source_label = article.source_name or "Marca"

    if provider == "dry_run" or not backends:
        preview_title = article.original_title.strip() or "Новость Real Madrid (dry-run)"
        excerpt = _truncate(article.full_text.replace("\n", " ").strip(), 520) if article.full_text else ""
        return {
            "provider": "dry_run",
            "private_title_ru": preview_title,
            "private_translation": (
                "DRY-RUN: нет LLM-ключей. Здесь будет подробный перевод/адаптация.\n\n"
                f"Заголовок: {preview_title}\n"
                f"URL: {article.original_url}\n\n"
                f"Фрагмент: {excerpt}"
            ).strip(),
            "telegram_title": _truncate(preview_title, 90),
            "telegram_post": (
                f"⚪️ {_truncate(preview_title, 110)}\n\n"
                "DRY-RUN: нет LLM-ключей, поэтому это заглушка.\n"
                f"{('Ключевые факты: ' + excerpt) if excerpt else ''}\n\n"
                f"Источник: {source_label}\n"
                f"Оригинал: {article.original_url}"
            ).strip(),
            "source": source_label,
            "original_url": article.original_url,
        }

    llm_rules = (style_rules.get("llm") or {}) if isinstance(style_rules, dict) else {}

    private_cfg = llm_rules.get("private_translation") or {}
    telegram_cfg = llm_rules.get("telegram_public_summary") or {}

    private_system = str(private_cfg.get("system") or "").strip()
    private_user_tpl = str(private_cfg.get("user_template") or "").strip()
    telegram_system = str(telegram_cfg.get("system") or "").strip()
    telegram_user_tpl = str(telegram_cfg.get("user_template") or "").strip()

    private_user = _fill_template(private_user_tpl, article) if private_user_tpl else article.full_text
    telegram_user = _fill_template(telegram_user_tpl, article) if telegram_user_tpl else article.full_text

    private_title_ru = ""
    private_text = ""
    telegram_title = ""
    telegram_post = ""

    try:
        sys_private = private_system or (
            "Ты профессиональный русскоязычный редактор и переводчик спортивных новостей.\n"
            "Переведи/адаптируй статью на русский для личного чтения.\n"
            "Важно: сохрани факты, имена, числа, цитаты. Ничего не выдумывай.\n"
            "Верни ответ строго в формате:\n"
            "TITLE_RU: <одна строка>\n"
            "BODY_RU:\n"
            "<текст перевода>\n"
            "Не используй JSON и не используй ```."
        )
        _, _, raw_private = _ask_with_backends(backends, sys_private, private_user, temperature=0.3, max_retries=1)
        m_t = re.search(r"TITLE_RU:\s*(.+)", raw_private)
        m_b = re.search(r"BODY_RU:\s*([\s\S]+)", raw_private)
        private_title_ru = (m_t.group(1).strip() if m_t else "").strip()
        private_text = (m_b.group(1) if m_b else raw_private).strip()
    except Exception as e:
        private_title_ru = article.original_title.strip()
        private_text = f"LLM_ERROR(private_translation): {type(e).__name__}: {e}"

    try:
        telegram_system_eff = telegram_system or (
            "Ты редактор публичного Telegram-канала. "
            "Сделай короткий оригинальный итог по фактам на русском. "
            "НЕ переводи статью целиком и не копируй большие фрагменты. "
            "Без кликбейта, без выдумок.\n"
            "Верни ответ строго в формате:\n"
            "TELEGRAM_TITLE: <одна строка на русском>\n"
            "TELEGRAM_POST:\n"
            "<короткий оригинальный пост на русском>\n"
            "Не используй JSON и не используй ```."
        )

        _, _, raw = _ask_with_backends(backends, telegram_system_eff, telegram_user, temperature=0.3, max_retries=2)

        m_title = re.search(r"TELEGRAM_TITLE:\s*(.+)", raw)
        m_post = re.search(r"TELEGRAM_POST:\s*([\s\S]+)", raw)
        if m_title:
            telegram_title = m_title.group(1).strip()
        if m_post:
            telegram_post = m_post.group(1).strip()
        else:
            # fallback to JSON attempt or raw
            obj = _extract_json(raw)
            if obj:
                telegram_title = str(obj.get("telegram_title") or "").strip() or telegram_title
                telegram_post = str(obj.get("telegram_post") or "").strip()
            else:
                telegram_post = raw.strip()
    except Exception as e:
        telegram_post = f"LLM_ERROR(telegram_summary): {type(e).__name__}: {e}"

    if not telegram_title:
        telegram_title = _truncate(private_title_ru or article.original_title.strip() or "Новости Real Madrid", 90)

    private_title_ru = _strip_title_trailing_dot(private_title_ru)
    telegram_title = _strip_title_trailing_dot(telegram_title)

    telegram_post = telegram_post.strip()

    # Hard guard: keep Telegram outputs in Russian.
    if telegram_title and not _looks_russian(telegram_title):
        telegram_title = private_title_ru or telegram_title
    if telegram_post and not _looks_russian(telegram_post):
        telegram_post = ""

    # Deterministic cleanup: attribution must be handled by app logic, not LLM.
    if telegram_post:
        telegram_post = _strip_attribution_lines(telegram_post)

    # Light post-processing only (NO truncation).
    if private_text and not private_text.startswith("LLM_ERROR"):
        private_text = _dedupe_lines(private_text)
        private_text = _micro_paragraphs(private_text, max_sentences_per_paragraph=2)
    if telegram_post:
        telegram_post = _dedupe_lines(telegram_post, max_lines=20)
        telegram_post = _micro_paragraphs(telegram_post, max_sentences_per_paragraph=1)

    return {
        "provider": provider,
        "model": model,
        "private_title_ru": private_title_ru,
        "private_translation": private_text,
        "telegram_title": telegram_title,
        "telegram_post": telegram_post,
        "source": source_label,
        "original_url": article.original_url,
        "author": article.author,
        "published_at": article.published_at,
        "content_type": ("opinion" if "/opinion/" in (article.original_url or "") else "news"),
        "hero_image_url": article.hero_image_url,
    }


def refine_channel_post(draft_text: str, style_rules: dict[str, Any]) -> dict[str, Any]:
    """
    Optional extra compression step for channel: rewrite an existing draft.
    Uses LLM (fallback across providers).
    """
    backends = _build_backends()
    if not backends:
        return {"telegram_post": draft_text}

    llm_rules = (style_rules.get("llm") or {}) if isinstance(style_rules, dict) else {}
    telegram_cfg = llm_rules.get("telegram_public_summary") or {}
    telegram_system = str(telegram_cfg.get("system") or "").strip()

    system = telegram_system or (
        "You are an editor of a public Telegram channel about Real Madrid. "
        "Rewrite the provided draft in Russian, making it shorter and removing tautology, "
        "without losing key facts. Return TELEGRAM_POST only."
    )
    user = (
        "Rewrite this draft. Keep facts, remove repetitions, make it as compact as possible.\n"
        "Return strictly:\n"
        "TELEGRAM_POST:\n"
        "<text>\n\n"
        "Draft:\n"
        f"{draft_text}"
    )

    _, _, raw = _ask_with_backends(backends, system, user, temperature=0.2, max_retries=1)
    m_post = re.search(r"TELEGRAM_POST:\s*([\s\S]+)", raw)
    post = (m_post.group(1) if m_post else raw).strip()
    post = _dedupe_lines(post, max_lines=20)
    post = _micro_paragraphs(post, max_sentences_per_paragraph=1)
    return {"telegram_post": post}


def _strip_attribution_lines(text: str) -> str:
    """
    LLM must not be responsible for attribution/date/urls in Telegram post.
    If it still leaks lines like "Источник: ..." / "Оригинал: ..." / raw URLs,
    remove them deterministically.
    """
    t = (text or "").strip()
    if not t:
        return ""
    out_lines: list[str] = []
    for line in t.splitlines():
        s = line.strip()
        if not s:
            out_lines.append("")
            continue
        if re.match(r"^(источник|оригинал)\b", s, flags=re.IGNORECASE):
            continue
        # common leakage: "Источник: Marca ..., https://..."
        if "http://" in s or "https://" in s:
            continue
        out_lines.append(line)
    # collapse excessive empties
    cleaned = "\n".join(out_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned
