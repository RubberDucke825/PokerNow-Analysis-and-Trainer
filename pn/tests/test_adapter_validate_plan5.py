"""Plan 5 spot-config keys in the Python validator: [leaf_model] (table and inline), realization
tables, hand_abstraction, [sizes.preflop] preset / three_bet_by_pos (contracts/tree_config.md)."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from pn.adapter.validate import SpotValidationError, leaf_model, to_toml, validate_config
from pn.profile.paths import repo_root

MWSOLVER = repo_root() / "target" / "release" / "mwsolver"
REALIZATION = "config/realization.toml"


def _cfg(**extra: object) -> dict:
    cfg = {
        "game": {"street": "preflop", "board": [], "pot": 150, "sb": 50, "bb": 100,
                 "mode": "depth_limited", "hand_abstraction": "class", "max_iterations": 10,
                 "target_exploitability": 0.5, "time_limit_sec": 10, "threads": 1},
        "players": [
            {"name": "btn", "position": "BTN", "stack": 10000, "range": "22+,AKs", "hero": True},
            {"name": "sb", "position": "SB", "stack": 9950, "range": "22+,AKs"},
            {"name": "bb", "position": "BB", "stack": 9900, "range": "22+,AKs"},
        ],
        "sizes": {"preflop": {"preset": "gtowizard", "three_bet_by_pos": {"SB": 1100, "BB": 1200}}},
    }  # fmt: skip
    return {**cfg, **extra}


def test_leaf_model_table_and_inline_forms() -> None:
    top = _cfg(leaf_model={"kind": "equity_realization", "realization": REALIZATION})
    validate_config(top)
    assert leaf_model(top) == {"kind": "equity_realization", "realization": REALIZATION}
    inline = _cfg()
    inline["game"] = {**inline["game"], "leaf_model": {"kind": "equity_realization",
                                                       "realization": REALIZATION}}  # fmt: skip
    validate_config(inline)
    assert leaf_model(_cfg()) == {"kind": "equity_realization"}
    both = {**inline, "leaf_model": {"kind": "equity_realization"}}
    with pytest.raises(SpotValidationError, match="not both"):
        validate_config(both)
    with pytest.raises(SpotValidationError, match="kind"):
        validate_config(_cfg(leaf_model={"kind": "other"}))


def test_realization_file_is_loaded_and_checked(tmp_path: Path) -> None:
    missing = _cfg(leaf_model={"kind": "equity_realization", "realization": str(tmp_path / "x")})
    with pytest.raises(SpotValidationError, match="realization"):
        validate_config(missing)
    bad = tmp_path / "r.toml"
    for text, msg in (("[base]\nip = 0.0\n", "> 0"), ("[base]\nmultiway_penalty = -1\n", ">= 0"),
                      ("[hand]\nkicker = 1\n", "unknown keys"), ("[other]\n", "unknown sections")):  # fmt: skip
        bad.write_text(text)
        with pytest.raises(SpotValidationError, match=msg):
            validate_config(_cfg(leaf_model={"kind": "equity_realization", "realization": str(bad)}))


def test_hand_abstraction_and_preflop_presets() -> None:
    flop = _cfg()
    flop["game"] = {**flop["game"], "street": "flop", "board": ["Kd", "7h", "2c"]}
    with pytest.raises(SpotValidationError, match="requires street"):
        validate_config(flop)
    with pytest.raises(SpotValidationError, match="hand_abstraction"):
        validate_config(_cfg(game={**_cfg()["game"], "hand_abstraction": "bucket"}))
    with pytest.raises(SpotValidationError, match="preset"):
        validate_config(_cfg(sizes={"preflop": {"preset": "pio"}}))
    with pytest.raises(SpotValidationError, match="unknown position"):
        validate_config(_cfg(sizes={"preflop": {"three_bet_by_pos": {"XX": 1100}}}))
    with pytest.raises(SpotValidationError, match="> 100"):
        validate_config(_cfg(sizes={"preflop": {"three_bet_by_pos": {"SB": 90}}}))


def test_toml_round_trip_with_leaf_model(tmp_path: Path) -> None:
    cfg = _cfg(leaf_model={"kind": "equity_realization", "realization": REALIZATION})
    text = to_toml(cfg)
    assert "[leaf_model]" in text
    assert tomllib.loads(text) == json.loads(json.dumps(cfg))
    if MWSOLVER.exists():
        path = tmp_path / "spot.toml"
        path.write_text(text)
        r = subprocess.run([str(MWSOLVER), "tree-size", str(path)], cwd=repo_root(),
                           capture_output=True, text=True, check=False)  # fmt: skip
        assert r.returncode == 0, r.stdout + r.stderr
