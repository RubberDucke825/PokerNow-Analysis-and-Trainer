"""Commands that read the database stop with one message when it is missing, and never leave an
empty sqlite file behind; ingest commands still create it."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from pn.cli.main import app

READS = [["hands", "list"], ["hand", "show", "h1"], ["validate", "g1"], ["profile", "all"],
         ["pos", "p1"], ["leaks", "p1"], ["review", "p1"], ["ev", "show", "h1"],
         ["ev", "summary", "p1"], ["train", "stats"], ["train", "leaks"], ["spot", "h1"]]  # fmt: skip


@pytest.mark.parametrize("args", READS, ids=" ".join)
def test_read_commands_refuse_a_missing_database(tmp_path: Path, args: list[str]) -> None:
    db = tmp_path / "none.sqlite"
    r = CliRunner().invoke(app, [*args, "--db", str(db)])
    out = " ".join(r.output.split())
    assert r.exit_code != 0
    assert "database not found" in out and "pn fetch" in out
    assert not db.exists()


def test_alias_list_still_creates_the_database(tmp_path: Path) -> None:
    db = tmp_path / "new.sqlite"
    r = CliRunner().invoke(app, ["alias", "list", "--db", str(db)])
    assert r.exit_code == 0 and db.exists()
