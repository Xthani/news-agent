# Образ Playwright уже содержит Chromium и системные зависимости.
# Версия тега должна совпадать с пакетом playwright в requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HEADLESS=true

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/browser_sessions

CMD ["python", "-u", "bot.py"]
