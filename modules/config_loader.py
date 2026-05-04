from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


def load_yaml(relative_name: str) -> dict[str, Any]:
    path = CONFIG_DIR / relative_name
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_sites() -> list[dict[str, Any]]:
    data = load_yaml("sites.yaml")
    sites = data.get("sites", [])
    return sites if isinstance(sites, list) else []


def load_style_rules() -> dict[str, Any]:
    data = load_yaml("style_rules.yaml")
    return data if isinstance(data, dict) else {}
