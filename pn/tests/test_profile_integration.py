"""Integration: the real 298-hand PokerNow fixture through the ingest parser into stats."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from pn.profile.build import build_from_walked
from pn.profile.stats import aggregate, walk_all

LOG = Path(__file__).resolve().parents[2] / "data/fixtures/pokernow_pglSAMPLE0000000000000000.json"
HERO, HAL = "hero000001", "hal0000001"


@pytest.fixture(scope="module")
def hands() -> list[dict]:
    if not LOG.exists():
        pytest.skip("real fixture log missing")
    from pn.ingest.parse import parse_messages, sort_messages

    msgs = json.loads(LOG.read_text())
    return parse_messages(sort_messages(msgs), game_id="pglSAMPLE0000000000000000")


def test_counts_hero_and_hal(hands: list[dict]) -> None:
    raw = aggregate(walk_all(hands))
    assert len(hands) == 298
    # regression values (VPIP/PFR successes are cross-checked against ingest below)
    assert raw[HERO].hands == 136
    assert raw[HERO].count("vpip") == (130, 56)
    assert raw[HERO].count("pfr") == (130, 35)
    assert raw[HERO].count("three_bet") == (47, 5)
    assert raw[HAL].hands == 236
    assert raw[HAL].count("vpip") == (224, 114)
    assert raw[HAL].count("pfr") == (224, 16)
    assert raw[HAL].count("three_bet") == (117, 3)


def test_vpip_pfr_agree_with_ingest_flags(hands: list[dict]) -> None:
    from pn.ingest.derive import player_flags

    vpip: Counter = Counter()
    pfr: Counter = Counter()
    for h in hands:
        for pid, f in player_flags(h).items():
            vpip[pid] += f["vpip"]
            pfr[pid] += f["pfr"]
    raw = aggregate(walk_all(hands))
    for pid, r in raw.items():
        assert r.count("vpip")[1] == vpip[pid], pid
        assert r.count("pfr")[1] == pfr[pid], pid


def test_every_player_profile_builds(hands: list[dict]) -> None:
    walked = walk_all(hands)
    raw = aggregate(walked)
    assert len(walked) == 298  # dead_button / dead_sb / run_it_twice hands are kept
    for cid in raw:  # not hero-centric: every player gets a full profile
        p = build_from_walked(cid, walked, raw_all=raw)
        json.dumps(p)
        assert p["hands"] == raw[cid].hands
