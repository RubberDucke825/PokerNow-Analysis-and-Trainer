"""Profile assembly: contract shape, SQLite source, CLI, priors rebuild."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pn.cli.main import app
from pn.profile import source
from pn.profile.build import build_profile
from pn.profile.priors import load_priors, rebuild
from pn.profile.stats import ALL_STATS, aggregate, walk_all

FIX = Path(__file__).parent / "fixtures"
HANDS = json.loads((FIX / "hands_core.json").read_text()) + json.loads(
    (FIX / "hands_special.json").read_text()
)
STAT_KEYS = {"post_mean", "n", "k", "ci90", "prior_mean"}
TOP_KEYS = {"canonical_id", "display_name", "aliases", "hands", "showdowns_seen", "updated_at",
            "stats", "by_position", "by_players_in_pot", "fold_vs_size", "bet_size_hist",
            "preflop_range_est", "lines", "vs", "archetype", "tips", "leaks", "by_depth",
            "preflop_size_hist"}  # fmt: skip
STAT_KEYS = STAT_KEYS | {"gto", "dev_pop", "dev_gto"}


def _is_stat(obj: dict) -> bool:
    return STAT_KEYS <= set(obj) and len(obj["ci90"]) == 2


@pytest.fixture(scope="module")
def frank() -> dict:
    return build_profile("pF", HANDS, names={"pF": "frank"})


def test_contract_shape(frank: dict) -> None:
    assert TOP_KEYS <= set(frank)
    assert frank["display_name"] == "frank" and frank["aliases"] == ["pF"]
    assert set(frank["stats"]) == set(ALL_STATS)
    assert all(_is_stat(s) for s in frank["stats"].values())
    assert all(_is_stat(s) for pos in frank["by_position"].values() for s in pos.values())
    assert set(frank["by_players_in_pot"]) == {"hu", "multiway"}
    assert set(frank["fold_vs_size"]["flop"]) == {"25", "33", "50", "100"}
    assert set(frank["fold_vs_size"]["turn"]) == {"75", "150"}
    assert set(frank["bet_size_hist"]["flop"]) == {"0-30", "30-45", "45-70", "70-120", "120+"}
    assert frank["archetype"]["label"] in {"calling_station", "aggressive", "punching_bag", "nit",
                                           "tag", "lag", "unknown"}  # fmt: skip
    for line in frank["lines"]:
        assert {"line", "n", "shown", "bluff_ratio"} <= set(line)
    json.dumps(frank)  # serializable


def test_ci_contains_mean_and_counts(frank: dict) -> None:
    for name, s in frank["stats"].items():
        assert s["ci90"][0] <= s["post_mean"] <= s["ci90"][1], name
    # core 23 (walk excluded) + s01, s02, s04, s07, s09
    assert frank["stats"]["vpip"]["n"] == 23 + 5


def test_lines_with_strength(frank: dict) -> None:
    by_line = {ln["line"]: ln for ln in frank["lines"]}
    tb = by_line["pre:call|flop:check-call|turn:check-call|river:check-call"]
    assert tb["shown"][0]["cards"] == "QsQd" and tb["shown"][0]["strength_class"] == "second_pair"
    assert tb["bluff_ratio"] == 0.0
    rr = by_line["pre:call|flop:check|turn:check|river:bet-call"]
    assert rr["shown"][0]["strength_class"] == "set"


def test_bluff_ratio_counts_air() -> None:
    dave = build_profile("pD", HANDS)
    ln = next(x for x in dave["lines"] if x["line"].startswith("pre:raise|flop:bet|turn:bet|river"))
    assert ln["shown"][0]["strength_class"] == "air" and ln["bluff_ratio"] == 1.0


def test_unknown_player_gets_priors() -> None:
    p = build_profile("nobody", HANDS)
    assert p["hands"] == 0 and p["archetype"]["label"] == "unknown"
    assert p["stats"]["vpip"]["post_mean"] == pytest.approx(load_priors()["stats"]["vpip"]["mean"])


def _make_db(path: Path) -> None:
    conn = source.open_db(path, create=True)
    with conn:
        conn.execute("INSERT INTO games(game_id) VALUES ('fixture')")
        for i, h in enumerate(HANDS):
            conn.execute(
                "INSERT INTO hands(hand_id, game_id, hand_no, started_at, n_players, flags, json)"
                " VALUES (?,?,?,?,?,?,?)",
                (h["hand_id"], "fixture", i, h["started_at"], len(h["players"]),
                 ",".join(h["flags"]), json.dumps(h)),
            )  # fmt: skip
        for pid, name in {"pA": "alice", "pB": "bob", "pC": "carol", "pD": "dave", "pE": "erin",
                          "pF": "frank", "pG": "gina", "pH": "hank"}.items():  # fmt: skip
            conn.execute("INSERT INTO players(player_id, canonical_id, display_name) VALUES (?,?,?)",
                         (pid, pid, name))  # fmt: skip
        # alice also played as pZ (alias)
        conn.execute("INSERT INTO aliases(player_id, canonical_id, note) VALUES ('pZ','pA','t')")
    conn.close()


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "pn.sqlite"
    _make_db(p)
    return p


def test_source_roundtrip(db: Path) -> None:
    conn = source.open_db(db)
    hands = source.load_hands(conn)
    amap = source.alias_map(conn)
    names = source.display_names(conn)
    assert len(hands) == len(HANDS)
    assert amap["pZ"] == "pA" and names["pA"] == "alice"
    assert source.resolve_target("ALICE", amap, names) == "pA"
    assert source.resolve_target("pZ", amap, names) == "pA"
    with pytest.raises(source.PlayerNotFound):
        source.resolve_target("zed", amap, names)


def test_missing_db_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        source.open_db(tmp_path / "nope.sqlite")


def test_cli_profile_tips_vs(db: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    out = tmp_path / "frank.json"
    r = runner.invoke(app, ["profile", "frank", "--db", str(db), "--json", str(out)])
    assert r.exit_code == 0, r.output
    assert json.loads(out.read_text())["canonical_id"] == "pF"
    r = runner.invoke(app, ["profile", "all", "--db", str(db), "--json", str(tmp_path / "all")])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "all" / "pA.json").exists()
    r = runner.invoke(app, ["tips", "dave", "--db", str(db)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["vs", "frank", "dave", "--db", str(db)])
    assert r.exit_code == 0 and "fold_to_their_bet" in r.output
    r = runner.invoke(app, ["profile", "zed", "--db", str(db)])
    assert r.exit_code == 2
    r = runner.invoke(app, ["leaks", "frank", "--db", str(db)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["review", "frank", "--db", str(db), "--json", str(tmp_path / "r.json")])
    assert r.exit_code == 0, r.output
    for row in json.loads((tmp_path / "r.json").read_text()):
        assert row["verdict"] in {"justified", "leak", "unknown"} and row["reason"]
    r = runner.invoke(app, ["compare", "frank", "dave", "alice", "--db", str(db)])
    assert r.exit_code == 0 and "population" in r.output and "GTO" in r.output


def test_cli_priors_rebuild(db: Path, tmp_path: Path) -> None:
    out = tmp_path / "priors.json"
    r = CliRunner().invoke(app, ["priors", "rebuild", "--db", str(db), "--min-hands", "1",
                                 "--out", str(out)])  # fmt: skip
    assert r.exit_code == 0, r.output
    new = json.loads(out.read_text())
    assert new["_meta"]["source"] == "rebuilt" and new["_meta"]["n_players"] == 8
    assert set(new["stats"]) >= set(ALL_STATS)


def test_rebuild_uses_pooled_means() -> None:
    raw = aggregate(walk_all(HANDS))
    new = rebuild(raw, load_priors(), min_hands=1)
    pooled_n = sum(r.count("vpip")[0] for r in raw.values())
    pooled_s = sum(r.count("vpip")[1] for r in raw.values())
    # 8 players -> empirical-Bayes fit; its mean is close to the pooled rate
    assert new["stats"]["vpip"]["mean"] == pytest.approx(pooled_s / pooled_n, abs=0.05)
    assert new["_meta"]["fit_report"]["vpip"].startswith("eb(")


def test_size_hist_and_depth_blocks() -> None:
    carol = build_profile("pC", HANDS)
    h = carol["preflop_size_hist"]["three_bet"]
    # carol's 3-bets: c03 3.0x, c09 4.4x (1 caller), c23 3.0x; all in position
    assert h["all"]["2.5-3.5x"] == 2 and h["all"]["3.5-4.5x"] == 1 and h["all"]["n"] == 3
    assert h["ip"]["n"] == 3 and h["oop"]["n"] == 0 and h["oop"]["mean_multiple"] is None
    assert h["callers_1plus"]["3.5-4.5x"] == 1
    bob = build_profile("pB", HANDS)
    assert bob["by_depth"][">250bb"]["three_bet"]["n"] == 1
    assert _is_stat(bob["by_depth"][">250bb"]["three_bet"])


def test_rebuilt_action_priors_sum_to_one() -> None:
    new = rebuild(aggregate(walk_all(HANDS)), load_priors(), min_hands=1)
    for key, entry in new["actions"].items():
        total = sum(v for a, v in entry.items() if a != "k")
        assert total == pytest.approx(1.0, abs=1e-3), key
