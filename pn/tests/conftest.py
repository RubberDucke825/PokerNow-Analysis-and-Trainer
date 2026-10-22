"""Suite-wide fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))


@pytest.fixture(autouse=True)
def _texas_not_available_for_staleness(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cached non-texas heads-up documents count as stale only when TexasSolver is available
    (pn.solve.cache.is_stale). Keep that independent of whether console_solver is built;
    tests of the rule patch it back on."""
    monkeypatch.setattr("pn.solve.cache.texas_available", lambda: False)


@pytest.fixture(autouse=True)
def _no_real_texas(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test starts a real console_solver by accident (a flop solve takes minutes): the binary
    lookup finds nothing unless a test patches pn.solve.texas.texas_cmd back."""
    monkeypatch.setattr("pn.solve.texas.texas_cmd", lambda: None)


@pytest.fixture
def fake_texas(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """console_solver is fixtures/fake_texas.py; returns the log it appends "texas" to per run."""
    log = tmp_path / "texas_runs.log"
    fake = [sys.executable, str(Path(__file__).parent / "fixtures" / "fake_texas.py")]
    monkeypatch.setattr("pn.solve.texas.texas_cmd", lambda: fake)
    monkeypatch.setenv("FAKE_LOG", str(log))
    return log


@pytest.fixture(scope="session")
def flop_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A cache root holding a fake dcfr flop library (fixtures/flop_seed.py) for every 6-max
    100bb heads-up matchup the trainer's preflop engine reaches; copy it with
    flop_seed.copy_into before building a Trainer on that root."""
    import flop_seed

    root = tmp_path_factory.mktemp("flop_library")
    flop_seed.seed(root)
    return root
