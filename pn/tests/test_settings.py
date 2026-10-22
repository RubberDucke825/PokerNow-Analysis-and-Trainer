"""pn.settings: defaults, env overrides, and the commands that need a hero id."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from pn import settings
from pn.cli.main import app
from pn.profile import paths, source
from pn.solve import cache, docstore, library, texas

ROOT = Path(__file__).resolve().parents[2]
ENV = ("PN_DB", "PN_HERO", "PN_TEXAS", "PN_TEXAS_RESOURCES", "PN_SOLVE_CACHE", "PN_LIBRARY_DIR",
       "PN_CACHE_GZIP", "PN_CONFIG_DIR", "PN_WEB_PORT", "PN_WEB_HOST")  # fmt: skip
TEXAS_CMD = texas.texas_cmd  # conftest stubs it per test; keep the real lookup


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_defaults(clean_env: pytest.MonkeyPatch) -> None:
    assert settings.db() == "data/pn.sqlite" == source.default_db()
    assert settings.hero() is None
    assert settings.texas() is None and settings.texas_resources() is None
    assert settings.solve_cache() == ROOT / "data" / "solves" / "cache" == cache.cache_dir()
    assert settings.library_dir() == ROOT / "data" / "solves" / "library" == library.library_dir()
    assert settings.cache_gzip() is True and docstore.gzip_enabled() is True
    assert settings.config_dir() == ROOT / "config" == paths.config_dir()
    assert settings.web_port() == 8765 and settings.web_host() == "127.0.0.1"


def test_env_overrides(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clean_env.setenv("PN_DB", str(tmp_path / "x.sqlite"))
    clean_env.setenv("PN_HERO", "hero000001")
    clean_env.setenv("PN_TEXAS", "/opt/solver --flag")
    clean_env.setenv("PN_TEXAS_RESOURCES", str(tmp_path / "res"))
    clean_env.setenv("PN_SOLVE_CACHE", str(tmp_path / "cache"))
    clean_env.setenv("PN_LIBRARY_DIR", str(tmp_path / "lib"))
    clean_env.setenv("PN_CACHE_GZIP", "0")
    clean_env.setenv("PN_CONFIG_DIR", str(tmp_path / "cfg"))
    clean_env.setenv("PN_WEB_PORT", "9123")
    clean_env.setenv("PN_WEB_HOST", "0.0.0.0")
    assert source.default_db() == str(tmp_path / "x.sqlite")
    assert settings.hero() == "hero000001"
    assert TEXAS_CMD() == ["/opt/solver", "--flag"]
    assert texas.resources_dir(["/opt/solver"]) == tmp_path / "res"
    assert cache.cache_dir() == tmp_path / "cache"
    assert library.library_dir() == tmp_path / "lib"
    assert docstore.gzip_enabled() is False
    assert paths.config_dir() == tmp_path / "cfg"
    assert settings.web_port() == 9123 and settings.web_host() == "0.0.0.0"


def test_empty_values_fall_back(clean_env: pytest.MonkeyPatch) -> None:
    for name in ("PN_HERO", "PN_TEXAS", "PN_TEXAS_RESOURCES", "PN_CONFIG_DIR"):
        clean_env.setenv(name, "")
    assert settings.hero() is None and settings.texas() is None
    assert settings.texas_resources() is None and settings.config_dir() == ROOT / "config"


@pytest.mark.parametrize("args", [["live", "now", "pglSAMPLE0000000000000000"],
                                  ["ev", "summary", "--db", "missing.sqlite"]])  # fmt: skip
def test_hero_commands_need_pn_hero(clean_env: pytest.MonkeyPatch, args: list[str]) -> None:
    r = CliRunner().invoke(app, args)
    assert r.exit_code != 0
    assert "PN_HERO" in r.output


def test_empty_web_port_and_host_fall_back(monkeypatch) -> None:
    monkeypatch.setenv("PN_WEB_PORT", "")
    monkeypatch.setenv("PN_WEB_HOST", "")
    assert settings.web_port() == settings.DEFAULT_WEB_PORT
    assert settings.web_host() == settings.DEFAULT_WEB_HOST
