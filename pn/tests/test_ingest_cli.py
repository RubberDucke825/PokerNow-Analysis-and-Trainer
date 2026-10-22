from pathlib import Path

from typer.testing import CliRunner

from pn.cli.main import app

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures"
GAME = "pglSAMPLE0000000000000000"
runner = CliRunner()


def run(*args):
    result = runner.invoke(app, [str(a) for a in args], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    return result.output


def test_cli_end_to_end(tmp_path):
    dbp = tmp_path / "pn.sqlite"
    out = run(
        "fetch",
        FIX / f"pokernow_{GAME}.json",
        "--db",
        dbp,
        "--ledger",
        FIX / f"ledger_{GAME}.csv",
        "--name",
        "Hero Example=hero000001",
    )
    assert "298 hands" in out and "flagged: 0" in out and "0 not explained" in out
    assert "Hero Example" in out
    out = run(
        "fetch", FIX / f"pokernow_{GAME}.json", "--db", dbp, "--ledger", FIX / f"ledger_{GAME}.csv"
    )
    assert "0 new, 298 already stored" in out
    out = run("validate", GAME, "--db", dbp, "--ledger", FIX / f"ledger_{GAME}.csv")
    assert "Ledger cross-check" in out and "flagged: 0" in out
    out = run("hands", "list", "--player", "hero000001", "--limit", "5", "--db", dbp)
    assert out.count(GAME) == 0 and "pos" in out
    out = run("hand", "show", "u1wutdljwawe", "--db", dbp)
    assert "RUN 2" in out and "kim raises to 43.75 (all-in)" in out
    out = run("alias", "merge", "hero000001", "otherid123", "--name", "Hero Example", "--db", dbp)
    assert "canonical hero000001" in out
    out = run("alias", "list", "--db", dbp)
    assert "otherid123" in out
    # usernames instead of ids, at ingest and after the fact
    out = run(
        "fetch",
        FIX / f"pokernow_{GAME}.json",
        "--db",
        dbp,
        "--no-ledger",
        "--name",
        "Dan Smith=dan",
    )
    assert "Dan Smith" in out
    out = run("alias", "unnamed", "--db", dbp)
    assert "hal0000001" in out and "hal" in out and "dan0000001" not in out  # dan is named
    out = run("alias", "assign", "Hal Lee", "HAL", "--db", dbp)
    assert "canonical hal0000001" in out
    out = run("alias", "list", "--db", dbp)
    assert "Dan Smith" in out and "Hal Lee" in out
    out = run("alias", "unnamed", "--db", dbp, "--game", GAME, "--json")
    assert "hal0000001" not in out and "mia0000001" in out  # Mia still unnamed
    result = runner.invoke(app, ["alias", "assign", "Nobody", "zzz-unknown", "--db", dbp])
    assert result.exit_code != 0 and "no player id or username" in result.output


def test_bad_name_option(tmp_path):
    result = runner.invoke(
        app,
        [
            "fetch",
            str(FIX / f"pokernow_{GAME}.json"),
            "--db",
            str(tmp_path / "x.sqlite"),
            "--name",
            "no-equals-sign",
        ],
    )
    assert result.exit_code != 0
