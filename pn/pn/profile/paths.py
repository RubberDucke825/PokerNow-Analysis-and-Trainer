"""Config locations (override the config dir with PN_CONFIG_DIR)."""

from __future__ import annotations

from pathlib import Path

from pn import settings


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def config_dir() -> Path:
    return settings.config_dir()
