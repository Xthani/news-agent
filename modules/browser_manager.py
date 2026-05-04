from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = PROJECT_ROOT / "browser_sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class BrowserManager:
    session_name: str = "default"
    headless: bool = True
    viewport_width: int = 1280
    viewport_height: int = 900
    locale: str = "ru-RU"

    _playwright: Playwright | None = None
    _context: BrowserContext | None = None

    @property
    def user_data_dir(self) -> Path:
        path = SESSIONS_DIR / self.session_name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _launch(self) -> BrowserContext:
        if self._context is not None:
            return self._context

        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=self.headless,
            args=["--no-sandbox"],
            viewport={"width": self.viewport_width, "height": self.viewport_height},
            locale=self.locale,
        )
        return self._context

    @property
    def context(self) -> BrowserContext:
        return self._launch()

    def new_page(self) -> Page:
        page = self.context.new_page()
        page.set_default_timeout(30_000)
        page.set_default_navigation_timeout(45_000)
        return page

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def __enter__(self) -> "BrowserManager":
        self._launch()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def get_sessions_dir() -> Path:
    return SESSIONS_DIR
