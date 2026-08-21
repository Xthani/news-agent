# News Agent

Container-ready Python automation agent that collects football news, extracts article content with Playwright, prepares LLM-assisted summaries, and routes drafts through a human-in-the-loop Telegram approval flow.

The current source adapter targets Real Madrid coverage from Marca. Sources, selectors, writing rules, providers, and publication settings are configuration-driven so the pipeline can be extended without coupling the core workflow to one site.

## Pipeline

```text
news listing
  -> new URL detection
  -> browser-based article extraction
  -> local raw archive
  -> LLM processing or dry-run fallback
  -> private Telegram preview
  -> edit and approve
  -> channel publication
```

Automatic public posting is disabled in the collection stage. A draft reaches a channel only through the separate Telegram approval workflow.

## Capabilities

- Playwright-based listing and article extraction;
- duplicate detection through a local article index;
- configurable source selectors and writing rules;
- raw and processed JSON artifacts for traceability;
- multiple LLM provider adapters with environment-based selection;
- dry-run operation when no provider credentials are configured;
- private Telegram previews and inline moderation controls;
- persistent browser sessions for sites that require state;
- Docker image suitable for a background worker;
- bounded processing per cycle to control API usage.

## Project structure

```text
main.py                 one collection and processing cycle
loop_main.py            recurring worker loop
bot.py                  Telegram moderation listener
modules/                browser, extraction, AI, storage, and publishing
config/sites.yaml       sources and CSS selectors
config/style_rules.yaml processing and writing rules
data/                   generated local state, ignored by Git
```

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

Run one pipeline cycle:

```bash
python main.py
```

Run it continuously:

```bash
python loop_main.py
```

Start the Telegram moderation listener in a separate process:

```bash
python bot.py
```

## Configuration

The complete list is documented in [`.env.example`](./.env.example). The most important settings are:

```env
# Choose and configure at least one supported LLM provider.
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini

# Required for private previews and approval-based publishing.
TELEGRAM_BOT_TOKEN=
TELEGRAM_PRIVATE_CHAT_ID=
TELEGRAM_CHANNEL_CHAT_ID=

HEADLESS=true
MAX_NEW_ARTICLES_PER_RUN=3
```

Secrets belong in the runtime environment or deployment dashboard and must never be committed.

### Dry-run mode

Without LLM credentials, the agent still collects and stores articles, prints a preview, and creates placeholder processed fields. This makes the extraction pipeline testable without consuming provider quota.

### Source maintenance

Website markup changes over time. Update listing and article selectors in [`config/sites.yaml`](./config/sites.yaml) when a source changes; the extraction code uses fallback selectors so one missing field does not have to stop the full cycle.

## Docker

```bash
docker build -t news-agent .
docker run --rm --env-file .env news-agent
```

The default container command starts the Telegram bot. For a recurring collection worker, override the command with `python -u loop_main.py` or configure the corresponding process in the hosting platform.

## Data and privacy

Generated articles, pending drafts, bot state, browser profiles, environment files, and credentials are excluded through `.gitignore`. The repository keeps only an empty `data/.gitkeep` placeholder.

When publishing summaries, retain source attribution and a link to the original article. Do not republish full copyrighted article text.
