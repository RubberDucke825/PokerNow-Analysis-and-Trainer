"""The user's logged-in CSV export of the same game as the public JSON log."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pn.cli.main import app
from pn.ingest import db
from pn.ingest.csv_import import game_id_from_filename, read_csv_log
from pn.ingest.lines import classify
from pn.ingest.parse import parse_messages

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures"
GAME = "pglSAMPLE0000000000000000"
CSV = FIX / f"poker_now_log_{GAME}.csv"
JSON = FIX / f"pokernow_{GAME}.json"
HERO = "hero000001"


@pytest.fixture(scope="module")
def csv_msgs():
    return read_csv_log(CSV)


@pytest.fixture(scope="module")
def csv_hands(csv_msgs):
    return parse_messages(csv_msgs, game_id=GAME, source_kind="pokernow_csv")


@pytest.fixture(scope="module")
def json_hands():
    return parse_messages(json.loads(JSON.read_text()), game_id=GAME)


def test_csv_rows_are_json_log_plus_hero_lines(csv_msgs):
    raw = json.loads(JSON.read_text())
    assert len(csv_msgs) == 5148 and game_id_from_filename(CSV) == GAME
    extra = {(m["created_at"], m["msg"]) for m in csv_msgs} - {
        (m["created_at"], m["msg"]) for m in raw
    }
    assert len(extra) == 136 and all(msg.startswith("Your hand is ") for _, msg in extra)


def test_csv_gives_same_hands_as_json(csv_hands, json_hands):
    assert len(csv_hands) == 298
    for c, j in zip(csv_hands, json_hands, strict=True):
        assert (c["hand_id"], c["hand_no"]) == (j["hand_id"], j["hand_no"])
        for key in (
            "net",
            "board",
            "runs",
            "actions",
            "pots",
            "blinds",
            "flags",
            "uncalled_returned",
        ):
            assert c[key] == j[key], (c["hand_no"], key)
        assert c["source"]["kind"] == "pokernow_csv"


def test_hero_cards_on_136_hands_inferred_without_flag(csv_hands, json_hands):
    hero_rows = [
        (h, p) for h in csv_hands for p in h["players"] if p["cards_source"] == "hero_export"
    ]
    assert len(hero_rows) == 136
    assert {p["player_id"] for _, p in hero_rows} == {HERO}
    assert all(h["hero_cards"] == p["cards"] and len(p["cards"]) == 2 for h, p in hero_rows)
    dealt = sum(1 for h in csv_hands if any(p["player_id"] == HERO for p in h["players"]))
    assert dealt == 136
    # wherever the hero also showed down, the export agrees with the shown cards
    shown = {
        h["hand_id"]: p["cards"]
        for h in json_hands
        for p in h["players"]
        if p["player_id"] == HERO and p["cards"]
    }
    assert shown
    for h, p in hero_rows:
        if h["hand_id"] in shown:
            assert set(shown[h["hand_id"]]) == set(p["cards"])


def test_explicit_hero_matches_inference(csv_msgs, csv_hands):
    explicit = parse_messages(
        csv_msgs, game_id=GAME, hero_id=HERO, fetched_at=csv_hands[0]["source"]["fetched_at"]
    )
    assert [h["players"] for h in explicit] == [h["players"] for h in csv_hands]


def test_config_and_special_lines_handled(csv_msgs, csv_hands):
    configs = [m["msg"] for m in csv_msgs if m["msg"].startswith("Game Config Changes")]
    assert configs and any("\n" in c for c in configs)
    assert all(classify(c).kind == "info" for c in configs)
    assert all(classify(m["msg"]) is not None for m in csv_msgs)
    blind_changes = [m["msg"] for m in csv_msgs if m["msg"].startswith("The game's ")]
    assert len(blind_changes) == 3 and all(classify(b).kind == "config" for b in blind_changes)
    assert csv_hands[0]["blinds"]["sb"] == 25 and csv_hands[0]["blinds"]["bb"] == 50
    second_run = [
        w for h in csv_hands for pot in h["pots"] for w in pot["winners"] if w.get("run") == 2
    ]
    assert len(second_run) == 5 and all(
        w["hand_desc"] and "second" not in w["hand_desc"] for w in second_run
    )
    h86 = next(h for h in csv_hands if h["hand_no"] == 86)
    assert "post_missed_bb" in [a["kind"] for a in h86["actions"]]


def test_csv_import_merges_hero_cards_into_loaded_game(tmp_path, csv_hands, json_hands):
    path = tmp_path / "pn.sqlite"
    db.load(json_hands, path)
    assert db.load(csv_hands, path) == {
        "inserted": 0,
        "skipped": 162,
        "replaced": 0,
        "enriched": 136,
    }
    with db.opened(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM hands").fetchone()[0] == 298
        n_cards = conn.execute(
            "SELECT COUNT(*) FROM hand_players WHERE player_id = ? AND cards IS NOT NULL", (HERO,)
        ).fetchone()[0]
        assert n_cards == 136
        stored = db.get_hand(conn, csv_hands[0]["hand_id"])
    hero = next(p for p in stored["players"] if p["player_id"] == HERO)
    assert hero["cards_source"] == "hero_export" and stored["source"]["kind"] == "pokernow_log"
    assert db.load(csv_hands, path)["enriched"] == 0  # idempotent


def test_cli_import_csv_after_fetch(tmp_path):
    runner, dbp = CliRunner(), str(tmp_path / "pn.sqlite")
    env = {"COLUMNS": "200"}
    r = runner.invoke(
        app, ["fetch", str(JSON), "--db", dbp, "--ledger", str(FIX / f"ledger_{GAME}.csv")], env=env
    )
    assert r.exit_code == 0, r.output
    r = runner.invoke(
        app,
        ["import-csv", str(CSV), "--db", dbp, "--ledger", str(FIX / f"ledger_{GAME}.csv")],
        env=env,
    )
    assert r.exit_code == 0, r.output
    assert (
        "0 new, 162 already stored, 0 replaced, 136 enriched" in r.output
        and "flagged: 0" in r.output
    )
