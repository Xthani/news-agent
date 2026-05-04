from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

import main as app_main


PROJECT_ROOT = Path(__file__).resolve().parent


def _interval_seconds() -> int:
    raw = (os.getenv("CHECK_INTERVAL_SECONDS") or "").strip()
    try:
        val = int(raw)
    except Exception:
        val = 60
    return max(10, val)


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_forever() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    interval = _interval_seconds()
    print(f"[loop] started, interval={interval}s", flush=True)

    while True:
        started = time.time()
        print(f"[loop] cycle started at {_ts()}", flush=True)
        try:
            rc = app_main.run()
            print(f"[loop] cycle finished rc={rc}", flush=True)
        except Exception as e:
            print(f"[loop] cycle failed: {type(e).__name__}: {e}", flush=True)

        elapsed = int(time.time() - started)
        sleep_for = max(1, interval - elapsed)
        print(f"[loop] sleep {sleep_for}s", flush=True)
        time.sleep(sleep_for)


if __name__ == "__main__":
    run_forever()
