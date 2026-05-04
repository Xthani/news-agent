# Real Madrid News Agent (Marca)

Этот проект — **новый отдельный агент** для автоматизации новостей про **Real Madrid**.
Он создан рядом с вашим старым проектом (например, `programaze`), но **не изменяет его** и не зависит от него.

## Что делает (Stage 1)

- Открывает страницу листинга Marca Real Madrid: `https://www.marca.com/futbol/real-madrid.html`
- Находит свежие карточки новостей, извлекает ссылки и метаданные
- Пропускает уже известные URL (локальный индекс `data/articles.json`)
- Открывает каждую новую статью и вытягивает полный текст (параграфы)
- Генерирует 2 русских вывода:
  - **Private**: подробный перевод/адаптация для личного чтения
  - **Public draft**: короткий оригинальный Telegram-итог (НЕ полный перевод) с указанием источника и URL
- Сохраняет “сырьё” и “обработку” локально в JSON
- Отправляет превью в личный Telegram **если** добавлены креды, иначе печатает в консоль
- **Не публикует** в публичный канал автоматически

Если LLM-ключей нет — работает **dry-run**: всё парсится, файлы сохраняются, вместо LLM — заглушки.

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Запуск (Stage 1)

Из папки `real_madrid_news_agent/`:

```bash
python main.py
```

По умолчанию `HEADLESS=true`. Можно поменять через `.env`.
Чтобы не сжигать лимиты LLM, можно ограничить количество новых статей на запуск:

- `MAX_NEW_ARTICLES_PER_RUN=2`

## Stage 2: модерация и публикация через кнопки в Telegram

1) Укажи переменные:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_PRIVATE_CHAT_ID` (твой личный chat id)
- `TELEGRAM_CHANNEL_CHAT_ID` (канал, где бот админ)

2) Запусти “слушатель” кнопок (отдельный процесс):

```bash
python bot.py
```

3) В другом окне терминала запускай обычный пайплайн:

```bash
python main.py
```

В личку придёт полный перевод + кнопки:
- **Сделать пост для канала** → бот покажет короткий черновик
- **Редактировать** → отправь правки одним сообщением
- **Опубликовать** → бот запостит в канал
Состояние хранится локально в `data/pending/`.

## Конфиги

- `config/sites.yaml` — источники и CSS-селекторы (можно подкрутить, если Marca изменит верстку)
- `config/style_rules.yaml` — промпты/правила для LLM-выводов

## Какие файлы генерируются

- `data/articles.json` — индекс (URL → статус и метаданные)
- `data/raw/<hash>.json` — распарсенная статья (заголовок/автор/дата/текст)
- `data/processed/<hash>.json` — результат AI обработки (private + telegram draft)
- `browser_sessions/` — persistent profile Playwright (куки/локалсторадж)

## Dry-run режим

Если нет `GROQ_API_KEY`, `OPENAI_API_KEY` и `GEMINI_API_KEY`, агент:
- всё равно парсит листинг и статьи
- сохраняет JSON в `data/raw` и `data/processed`
- печатает превью в консоль
- создаёт заглушки `private_translation` и `telegram_post`

## Stage 2: какие креды добавить

### LLM (один из вариантов)

- Groq:
  - `GROQ_API_KEY`
  - `GROQ_MODEL` (например, `llama-3.3-70b-versatile`)
- OpenAI:
  - `OPENAI_API_KEY`
  - `OPENAI_MODEL` (например, `gpt-4o-mini`)
- DeepSeek:
  - `DEEPSEEK_API_KEY`
  - `DEEPSEEK_MODEL` (например, `deepseek-chat`)
- Hugging Face:
  - `HF_TOKEN`
  - `HF_MODEL` (например, `HuggingFaceH4/zephyr-7b-beta`)
- Gemini:
  - `GEMINI_API_KEY`
  - `GEMINI_MODEL` (например, `gemini-flash-latest`)
- Together:
  - `TOGETHER_API_KEY`
  - `TOGETHER_MODEL` (например, `meta-llama/Llama-3.3-70B-Instruct-Turbo`)
- OpenRouter:
  - `OPENROUTER_API_KEY`
  - `OPENROUTER_MODEL` (например, `openai/gpt-4o-mini`)
- SambaNova:
  - `SAMBANOVA_API_KEY`
  - `SAMBANOVA_MODEL` (например, `Meta-Llama-3.1-70B-Instruct`)
- Cerebras:
  - `CEREBRAS_API_KEY`
  - `CEREBRAS_MODEL` (например, `gpt-oss-120b`)
- Cloudflare Workers AI:
  - `CLOUDFLARE_ACCOUNT_ID`
  - `CLOUDFLARE_API_TOKEN`
  - `CLOUDFLARE_MODEL` (например, `@cf/meta/llama-3.1-8b-instruct`)

### Telegram (для личного превью)

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_PRIVATE_CHAT_ID`

Публикация в канал подготовлена функцией `publish_to_channel()`, но **не вызывается автоматически** в Stage 1.

## Если Marca перестала парситься

Селекторы в `config/sites.yaml` могут устареть. Правило простое:
- обновляете `listing_selectors.*` (для карточек)
- обновляете `article_selectors.*` (для статьи и параграфов)

Код написан защитно: если часть селекторов не сработает, пайплайн не должен падать целиком.
