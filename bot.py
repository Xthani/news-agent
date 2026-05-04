from __future__ import annotations

import os

from dotenv import load_dotenv

from modules.telegram_bot import BotRunner


def main() -> None:
    load_dotenv(".env", override=False)

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    private_chat_id = (os.getenv("TELEGRAM_PRIVATE_CHAT_ID") or "").strip()
    if not token or not private_chat_id:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_PRIVATE_CHAT_ID in .env")

    print("[bot] started. waiting for button clicks / edits…")
    BotRunner(token=token, private_chat_id=private_chat_id).run_forever()


if __name__ == "__main__":
    main()

