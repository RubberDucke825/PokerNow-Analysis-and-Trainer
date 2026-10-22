import json
from pathlib import Path

import pytest

from pn.ingest import db
from pn.ingest.derive import annotate_actions, player_flags
from pn.ingest.parse import parse_messages

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures"
GAME = "pglSAMPLE0000000000000000"


@pytest.fixture(scope="module")
def hands():
    return parse_messages(json.loads((FIX / f"pokernow_{GAME}.json").read_text()), game_id=GAME)


def test_load_is_idempotent(tmp_path, hands):
    path = tmp_path / "pn.sqlite"
    assert db.load(hands, path) == {"inserted": 298, "skipped": 0, "replaced": 0, "enriched": 0}
    assert db.load(hands, path) == {"inserted": 0, "skipped": 298, "replaced": 0, "enriched": 0}
    with db.opened(path) as conn:
        counts = [
            conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("games", "hands", "hand_players", "actions", "players")
        ]
        assert counts == [
            1,
            298,
            1224,
            sum(len(h["actions"]) for h in hands),
            8,
        ]  # Dan dan0000002 never dealt in
        assert conn.execute("SELECT SUM(net) FROM hand_players").fetchone()[0] == 0
        game = conn.execute("SELECT * FROM games").fetchone()
        assert (
            game["first_hand_at"] == hands[0]["started_at"]
            and game["last_hand_at"] == hands[-1]["started_at"]
        )


def test_incomplete_hand_is_replaced_by_complete_version(tmp_path, hands):
    path = tmp_path / "pn.sqlite"
    partial = {**hands[-1], "flags": ["incomplete"], "actions": hands[-1]["actions"][:2]}
    db.load([partial], path)
    assert db.load([hands[-1]], path)["replaced"] == 1
    with db.opened(path) as conn:
        stored = db.get_hand(conn, hands[-1]["hand_id"])
        n_actions = conn.execute(
            "SELECT COUNT(*) FROM actions WHERE hand_id = ?", (stored["hand_id"],)
        ).fetchone()[0]
    assert stored["flags"] == [] and n_actions == len(hands[-1]["actions"])


def test_alias_merge_and_names(tmp_path, hands):
    path = tmp_path / "pn.sqlite"
    db.load(hands, path)
    with db.opened(path) as conn:
        canon = db.merge(conn, ["hero000001", "newid12345"], name="Hero Example")
        assert canon == "hero000001"
        assert db.resolve(conn, "newid12345") == "hero000001"
        assert db.group_ids(conn, "newid12345") == ["hero000001", "newid12345"]
        # a later game with yet another id and the same full name joins the same person
        out = db.assign_names(conn, {"Hero Example": ["third12345"]})
        assert out == {"Hero Example": "hero000001"}
        assert db.resolve(conn, "third12345") == "hero000001"
        row = conn.execute(
            "SELECT COUNT(*) FROM hand_players_canon WHERE canonical_id = 'hero000001'"
        ).fetchone()
        assert row[0] == 136
        names = {g["display_name"] for g in db.alias_groups(conn)}
        assert "Hero Example" in names and "dan" in names
    db.load(hands, path)  # reload must not clobber the user-given name
    with db.opened(path) as conn:
        assert (
            conn.execute(
                "SELECT display_name FROM players WHERE player_id='hero000001'"
            ).fetchone()[0]
            == "Hero Example"
        )


def test_resolve_player_refs_by_username(tmp_path, hands):
    path = tmp_path / "pn.sqlite"
    db.load(hands, path)
    gid = hands[0]["source"]["game_id"]
    with db.opened(path) as conn:
        # id passes through; username (case-insensitive) resolves within the game
        assert db.resolve_player_refs(conn, ["dan0000001", "Dan", "hal"], gid) == [
            "dan0000001",
            "dan0000001",
            "hal0000001",
        ]
        with pytest.raises(ValueError, match="no player id or username"):
            db.resolve_player_refs(conn, ["nobody"], gid)
        # a second id with the same username is ambiguous outside the game scope
        conn.execute(
            "INSERT INTO players(player_id, canonical_id, nickname) VALUES ('dup000000', 'dup000000', 'dan')"
        )
        with pytest.raises(ValueError, match="several player ids"):
            db.resolve_player_refs(conn, ["dan"])
        assert db.resolve_player_refs(conn, ["dan"], gid) == ["dan0000001"]  # game scope wins
        # usernames change but the person accumulates: name the new id under the same person
        canon = db.assign_names(conn, {"Dan Smith": db.resolve_player_refs(conn, ["dan"], gid)})
        assert canon == {"Dan Smith": "dan0000001"}
        assert db.assign_names(conn, {"Dan Smith": ["dup000000"]}) == {
            "Dan Smith": "dan0000001"
        }
        assert db.resolve(conn, "dup000000") == "dan0000001"


def test_player_flags_on_fixture_hand(hands):
    h55 = next(h for h in hands if h["hand_no"] == 55)
    f = player_flags(h55)
    kim, lou, hero, mia = "kim0000001", "lou0000001", "hero000001", "mia0000001"
    assert f[kim] == {
        "vpip": True,
        "pfr": True,
        "saw_flop": True,
        "went_to_sd": True,
        "won_sd": False,
    }
    assert f[lou]["won_sd"] and f[lou]["pfr"]
    assert not f[hero]["vpip"] and not f[hero]["saw_flop"]
    assert not any(f[mia].values())


def test_annotated_actions_pot_and_facing_size(hands):
    h55 = next(h for h in hands if h["hand_no"] == 55)
    acts = annotate_actions(h55)
    assert [a["pot_before"] for a in acts[:4]] == [0, 25, 75, 75]
    assert acts[3]["facing_bet_pct"] is None  # facing only the blinds
    assert acts[4]["facing_bet_pct"] == 100  # 1.75 open into 0.75 = pot-sized raise
    flop_call = acts[8]
    assert (flop_call["kind"], flop_call["facing_bet_pct"], flop_call["n_active"]) == (
        "call",
        48,
        2,
    )
    assert acts[0]["n_active"] == 4 and acts[6]["n_active"] == 2
