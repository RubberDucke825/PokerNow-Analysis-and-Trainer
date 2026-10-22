"""Parser behaviour on small synthetic logs (PokerNow message format)."""

from pn.ingest.parse import parse_messages, sort_messages

A, B, C, D = (f'"{n} @ {n}id000000"' for n in ("alice", "bob", "carol", "dave"))
AID, BID, CID, DID = (f"{n}id000000" for n in ("alice", "bob", "carol", "dave"))


def msgs(lines, start=1000):
    """Newest-first list like the API returns."""
    out = [
        {"at": f"2026-10-01T00:00:{i:02d}.000Z", "created_at": str(start + i), "msg": m}
        for i, m in enumerate(lines)
    ]
    return list(reversed(out))


def hand(no, body, dealer=A, stacks=None):
    stacks = stacks or f"#1 {A} (100.00) | #2 {B} (100.00) | #3 {C} (100.00)"
    return [
        f"-- starting hand #{no} (id: h{no}) No Limit Texas Hold'em (dealer: {dealer}) --",
        f"Player stacks: {stacks}",
        *body,
        f"-- ending hand #{no} --",
    ]


def parse(lines, **kw):
    return parse_messages(msgs(lines), game_id="g", **kw)


def test_sort_messages_orders_and_dedups():
    m = msgs(["a", "b"])
    assert [x["msg"] for x in sort_messages(m + m)] == ["a", "b"]


def test_raise_call_amounts_are_increments_and_net_sums_to_zero():
    (h,) = parse(
        hand(
            1,
            [
                f"{B} posts a small blind of 0.25",
                f"{C} posts a big blind of 0.50",
                f"{A} raises to 1.50",
                f"{B} calls 1.50",
                f"{C} folds",
                "Flop:  [K♥, 10♣, A♠]",
                f"{B} checks",
                f"{A} bets 2.00",
                f"{B} raises to 6.00",
                f"{A} calls 6.00",
                "Turn: K♥, 10♣, A♠ [2♦]",
                f"{B} bets 10.00",
                f"{A} folds",
                f"Uncalled bet of 10.00 returned to {B}",
                f"{B} collected 15.50 from pot",
            ],
        )
    )
    acts = [(a["kind"], a["amount"], a["to_amount"]) for a in h["actions"]]
    assert acts[3] == ("call", 125, 150)
    assert acts[7] == ("raise", 600, 600) and acts[8] == ("call", 400, 600)
    assert h["net"] == {AID: -750, BID: 800, CID: -50}
    assert h["flags"] == [] and h["board"] == {
        "flop": ["Kh", "Tc", "As"],
        "turn": "2d",
        "river": None,
    }
    assert [p["position"] for p in h["players"]] == ["BTN", "SB", "BB"]


def test_straddle_and_ante_hand():
    stacks = f"#1 {A} (100.00) | #2 {B} (100.00) | #3 {C} (100.00) | #4 {D} (100.00)"
    (h,) = parse(
        hand(
            1,
            [
                f"{A} posts an ante of 0.10",
                f"{B} posts an ante of 0.10",
                f"{C} posts an ante of 0.10",
                f"{D} posts an ante of 0.10",
                f"{B} posts a small blind of 0.25",
                f"{C} posts a big blind of 0.50",
                f"{D} posts a straddle of 1.00",
                f"{A} folds",
                f"{B} folds",
                f"{C} calls 1.00",
                f"{D} checks",
                "Flop:  [2♣, 3♦, 4♥]",
                f"{C} bets 1.00",
                f"{D} folds",
                f"Uncalled bet of 1.00 returned to {C}",
                f"{C} collected 2.65 from pot",
            ],
            stacks=stacks,
        )
    )
    assert "straddle" in h["flags"]
    assert h["blinds"] == {"sb": 25, "bb": 50, "ante": 10, "straddle": 100}
    pos = {p["player_id"]: p["position"] for p in h["players"]}
    assert pos == {AID: "BTN", BID: "SB", CID: "BB", DID: "STR"}
    ante = h["actions"][0]
    assert (ante["kind"], ante["amount"], ante["to_amount"]) == ("post_ante", 10, 0)
    assert h["actions"][9]["amount"] == 50  # BB calls the straddle: 1.00 - 0.50 live
    assert sum(h["net"].values()) == 0 and h["net"][CID] == 155


def test_dead_missing_sb_is_not_live_but_missed_bb_is():
    (h,) = parse(
        hand(
            1,
            [
                f"{B} posts a small blind of 0.25",
                f"{C} posts a big blind of 0.50",
                f"{A} posts a missing small blind of 0.25",
                f"{A} posts a missed big blind of 0.50",
                f"{A} calls 1.00",
                f"{B} folds",
                f"{C} folds",
                f"Uncalled bet of 0.50 returned to {A}",
                f"{A} collected 1.50 from pot",
            ],
        )
    )
    a_acts = [
        (a["kind"], a["amount"], a["to_amount"]) for a in h["actions"] if a["player_id"] == AID
    ]
    assert a_acts == [("post_missing_sb", 25, 0), ("post_missed_bb", 50, 50), ("call", 50, 100)]
    assert h["net"] == {AID: 75, BID: -25, CID: -50}


def test_your_hand_assigns_hero_cards_and_infers_hero():
    lines = hand(
        1,
        [
            f"{B} posts a small blind of 0.25",
            f"{C} posts a big blind of 0.50",
            "Your hand is A♣, K♦",
            f"{A} raises to 1.50",
            f"{B} folds",
            f"{C} calls 1.50",
            "Flop:  [2♣, 3♦, 4♥]",
            "Turn: 2♣, 3♦, 4♥ [9♠]",
            "River: 2♣, 3♦, 4♥, 9♠ [J♥]",
            f"{C} checks",
            f"{A} checks",
            f"{A} shows a A♣, K♦.",
            f"{C} shows a 5♣, 5♦.",
            f"{C} collected 3.25 from pot with Pair, 5's (combination: 5♣, 5♦, 9♠, J♥, 4♥)",
        ],
    )
    (h,) = parse(lines)
    alice = next(p for p in h["players"] if p["player_id"] == AID)
    assert alice["cards"] == ["Ac", "Kd"] and alice["cards_source"] == "hero_export"
    assert h["hero_cards"] == ["Ac", "Kd"]
    (h2,) = parse(lines, hero_id=BID)
    assert next(p for p in h2["players"] if p["player_id"] == BID)["cards_source"] == "hero_export"


def test_hole_cards_recovered_from_combination_and_post_hand_shows():
    lines = hand(
        1,
        [
            f"{B} posts a small blind of 0.25",
            f"{C} posts a big blind of 0.50",
            f"{A} calls 0.50",
            f"{B} folds",
            f"{C} checks",
            "Flop:  [2♣, 3♦, 4♥]",
            "Turn: 2♣, 3♦, 4♥ [9♠]",
            "River: 2♣, 3♦, 4♥, 9♠ [J♥]",
            f"{C} checks",
            f"{A} checks",
            f"{A} collected 1.25 from pot with Pair, A's (combination: A♠, A♥, J♥, 9♠, 4♥)",
        ],
    ) + [f"{C} shows a 7♣."]  # shown after '-- ending hand' -> still this hand
    (h,) = parse(lines)
    cards = {p["player_id"]: p["cards"] for p in h["players"]}
    assert cards[AID] == ["As", "Ah"]
    sd = {s["player_id"]: s for s in h["showdowns"]}
    assert sd[AID]["shown"] is False and sd[CID]["cards"] == ["7c"] and sd[CID]["shown"]
    assert cards[CID] is None  # one card is not a full hand


def test_side_pots_main_first():
    stacks = f"#1 {A} (10.00) | #2 {B} (100.00) | #3 {C} (100.00)"
    (h,) = parse(
        hand(
            1,
            [
                f"{B} posts a small blind of 0.25",
                f"{C} posts a big blind of 0.50",
                f"{A} raises to 10.00 and go all in",
                f"{B} raises to 30.00",
                f"{C} calls 30.00",
                "Flop:  [2♣, 3♦, 4♥]",
                "Turn: 2♣, 3♦, 4♥ [9♠]",
                "River: 2♣, 3♦, 4♥, 9♠ [J♥]",
                f"{B} checks",
                f"{C} checks",
                f"{A} collected 30.00 from pot",
                f"{B} collected 40.00 from pot",
            ],
            stacks=stacks,
        )
    )
    assert [p["amount"] for p in h["pots"]] == [3000, 4000]
    assert h["pots"][1]["winners"][0]["player_id"] == BID
    assert h["net"] == {AID: 2000, BID: 1000, CID: -3000}


def test_run_it_twice_second_run_fills_shared_cards():
    (h,) = parse(
        hand(
            1,
            [
                f"{B} posts a small blind of 0.25",
                f"{C} posts a big blind of 0.50",
                f"{A} raises to 100.00 and go all in",
                f"{B} folds",
                f"{C} calls 100.00 and go all in",
                "All players in hand choose to run it twice.",
                "Flop:  [2♣, 3♦, 4♥]",
                "Turn: 2♣, 3♦, 4♥ [9♠]",
                "River: 2♣, 3♦, 4♥, 9♠ [J♥]",
                "River (second run): 2♣, 3♦, 4♥, 9♠ [K♥]",
                f"{A} collected 100.13 from pot",
                f"{C} collected 100.12 from pot on the second run",
            ],
        )
    )
    assert "run_it_twice" in h["flags"] and len(h["runs"]) == 2
    assert h["runs"][1]["board"] == {"flop": ["2c", "3d", "4h"], "turn": "9s", "river": "Kh"}
    assert h["board"]["river"] == "Jh"
    assert sum(h["net"].values()) == 0


def test_bomb_pot():
    (h,) = parse(
        hand(
            1,
            [
                f"{A} posts a bet of 1.00 (bomb pot bet)",
                f"{B} posts a bet of 1.00 (bomb pot bet)",
                f"{C} posts a bet of 1.00 (bomb pot bet)",
                "Flop:  [2♣, 3♦, 4♥]",
                f"{B} bets 1.00",
                f"{C} folds",
                f"{A} folds",
                f"Uncalled bet of 1.00 returned to {B}",
                f"{B} collected 3.00 from pot",
            ],
        )
    )
    assert "bomb_pot" in h["flags"] and h["net"][BID] == 200


def test_bounty_moves_money_outside_pot():
    (h,) = parse(
        hand(
            1,
            [
                f"{B} posts a small blind of 0.25",
                f"{C} posts a big blind of 0.50",
                f"{A} folds",
                f"{B} folds",
                f"Uncalled bet of 0.25 returned to {C}",
                f"{C} collected 0.50 from pot",
                f"{B} paid 1.00 for the 7-2 bounty to {C}",
            ],
        )
    )
    assert h["net"] == {AID: 0, BID: -125, CID: 125} and h["bounties"]


def test_missing_end_and_unknown_player_flag_incomplete():
    lines = hand(1, [f"{B} posts a small blind of 0.25"])[:-1] + hand(
        2,
        [
            f"{B} posts a small blind of 0.25",
            f"{C} posts a big blind of 0.50",
            f"{D} folds",
            "some brand-new PokerNow message",
            f"{A} folds",
            f"{B} folds",
            f"Uncalled bet of 0.25 returned to {C}",
            f"{C} collected 0.50 from pot",
        ],
    )
    h1, h2 = parse(lines)
    assert "incomplete" in h1["flags"] and any("ending" in i for i in h1["issues"])
    assert "incomplete" in h2["flags"] and any("not in Player stacks" in i for i in h2["issues"])


def test_pot_mismatch_is_flagged_with_details():
    (h,) = parse(
        hand(
            1,
            [
                f"{B} posts a small blind of 0.25",
                f"{C} posts a big blind of 0.50",
                f"{A} folds",
                f"{B} folds",
                f"{C} collected 0.50 from pot",  # uncalled 0.25 line missing
            ],
        )
    )
    assert "pot_mismatch" in h["flags"] and "contributed-returned=75" in h["issues"][0]
