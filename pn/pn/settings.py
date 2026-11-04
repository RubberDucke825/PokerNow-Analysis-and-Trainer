"""Environment settings, read in one place. Each is read at call time (so tests can monkeypatch);
paths that default inside the repo resolve against the repo root.

PN_DB             SQLite database (default data/pn.sqlite, relative to the working directory)
PN_HERO           hero's PokerNow player id (no default)
PN_TEXAS          TexasSolver command, may include args (default: the vendored build)
PN_TEXAS_RESOURCES  TexasSolver resources dir (default: beside the binary, else vendored)
PN_SOLVE_CACHE    solve cache dir (default data/solves/cache)
PN_LIBRARY_DIR    preflop root documents (default data/solves/library)
PN_CACHE_GZIP     0 writes plain JSON documents (default 1: gzip)
PN_CONFIG_DIR     config dir (default config/)
PN_WEB_PORT       `pn web` port (default 8765)
PN_WEB_HOST       `pn web` bind address (default 127.0.0.1)
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DB = "data/pn.sqlite"
DEFAULT_WEB_PORT = 8765
DEFAULT_WEB_HOST = "127.0.0.1"
_ROOT = Path(__file__).resolve().parents[2]


def db() -> str:
    return os.environ.get("PN_DB", DEFAULT_DB)


def hero() -> str | None:
    return os.environ.get("PN_HERO") or None


def texas() -> str | None:
    return os.environ.get("PN_TEXAS") or None


def texas_resources() -> str | None:
    return os.environ.get("PN_TEXAS_RESOURCES") or None


def solve_cache() -> Path:
    return Path(os.environ.get("PN_SOLVE_CACHE", _ROOT / "data" / "solves" / "cache"))


def library_dir() -> Path:
    return Path(os.environ.get("PN_LIBRARY_DIR", _ROOT / "data" / "solves" / "library"))


def cache_gzip() -> bool:
    return os.environ.get("PN_CACHE_GZIP", "1") != "0"


def config_dir() -> Path:
    env = os.environ.get("PN_CONFIG_DIR")
    return Path(env) if env else _ROOT / "config"


def web_port() -> int:
    return int(os.environ.get("PN_WEB_PORT") or DEFAULT_WEB_PORT)


def web_host() -> str:
    return os.environ.get("PN_WEB_HOST") or DEFAULT_WEB_HOST
