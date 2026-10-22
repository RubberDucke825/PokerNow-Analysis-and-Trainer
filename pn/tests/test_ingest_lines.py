import pytest

from pn.ingest.lines import Player, classify, parse_cards, to_cents

P = '"hal @ hal0000001"'
HAL = Player("hal", "hal0000001")


@pytest.mark.parametrize(
    ("text", "cents"), [("0.25", 25), ("426.06", 42606), ("20", 2000), ("0.1", 10)]
)
def test_to_cents_is_exact(text, cents):
    assert to_cents(text) == cents


def test_cards_normalize_ten_and_suits():
    assert parse_cards("K♥, 10♣, A♠ [2♦]") == ("Kh", "Tc", "As", "2d")


def test_hand_start_with_dealer():
    ev = classify(
        "-- starting hand #298 (id: znhkbnyd2cfa)  No Limit Texas Hold'em "
        '(dealer: "dan @ dan0000001") --'
    )
    assert ev.kind == "hand_start"
    assert ev.data["hand_no"] == 298 and ev.data["hand_id"] == "znhkbnyd2cfa"
    assert ev.player == Player("dan", "dan0000001")
    assert ev.data["variant"] == "No Limit Texas Hold'em" and not ev.data["dead_button"]


def test_hand_start_dead_button():
    ev = classify(
        "-- starting hand #207 (id: ammsqui3yvjq)  No Limit Texas Hold'em (dead button) --"
    )
    assert ev.player is None and ev.data["dead_button"]


def test_player_stacks():
    ev = classify(
        'Player stacks: #4 "dan @ dan0000001" (429.81) | #9 "hal @ hal0000001" (143.75)'
    )
    assert ev.data["seats"] == ((4, Player("dan", "dan0000001"), 42981), (9, HAL, 14375))


@pytest.mark.parametrize(
    ("line", "kind", "amount", "allin"),
    [
        (f"{P} posts a small blind of 0.25", "post_sb", 25, False),
        (f"{P} posts a big blind of 0.50", "post_bb", 50, False),
        (f"{P} posts a big blind of 0.30 and go all in", "post_bb", 30, True),
        (f"{P} posts a missing small blind of 0.25", "post_missing_sb", 25, False),
        (f"{P} posts a missed big blind of 0.50", "post_missed_bb", 50, False),
        (f"{P} posts a straddle of 1.00", "post_straddle", 100, False),
        (f"{P} posts an ante of 0.10", "post_ante", 10, False),
        (f"{P} posts a bet of 1.00 (bomb pot bet)", "post_ante", 100, False),
        (f"{P} calls 1.00 (bomb pot bet)", "post_ante", 100, False),
        (f"{P} folds", "fold", 0, False),
        (f"{P} checks", "check", 0, False),
        (f"{P} calls 2.25", "call", 225, False),
        (f"{P} calls 89.50 and go all in", "call", 8950, True),
        (f"{P} bets 85.25 and go all in", "bet", 8525, True),
        (f"{P} raises to 1.50", "raise", 150, False),
        (f"{P} raises to 20.25 and go all in", "raise", 2025, True),
    ],
)
def test_actions_and_posts(line, kind, amount, allin):
    ev = classify(line)
    assert (ev.kind, ev.player, ev.amount, ev.allin) == (kind, HAL, amount, allin)


def test_bomb_pot_bet_marked():
    assert classify(f"{P} posts a bet of 1.00 (bomb pot bet)").data["bomb_pot"]


@pytest.mark.parametrize(
    ("line", "street", "run", "cards"),
    [
        ("Flop:  [K♥, 10♣, A♠]", "flop", 1, ("Kh", "Tc", "As")),
        ("Turn: K♠, K♦, 9♣ [2♥]", "turn", 1, ("Ks", "Kd", "9c", "2h")),
        ("River: K♥, K♦, 4♣, 2♥ [5♦]", "river", 1, ("Kh", "Kd", "4c", "2h", "5d")),
        ("Flop (second run):  [8♠, 9♠, 2♦]", "flop", 2, ("8s", "9s", "2d")),
        ("River (second run): 10♥, 9♥, A♠, 3♠ [3♦]", "river", 2, ("Th", "9h", "As", "3s", "3d")),
    ],
)
def test_board_lines(line, street, run, cards):
    ev = classify(line)
    assert (ev.kind, ev.data["street"], ev.data["run"], ev.cards) == ("board", street, run, cards)


def test_shows_two_and_one_card():
    assert classify(f"{P} shows a Q♦, A♠.").cards == ("Qd", "As")
    assert classify(f"{P} shows a K♠.").cards == ("Ks",)


def test_collected_plain_and_with_combination():
    plain = classify(f"{P} collected 1.00 from pot")
    assert (plain.kind, plain.amount, plain.cards, plain.data["hand_desc"]) == (
        "collected",
        100,
        (),
        None,
    )
    ev = classify(
        f"{P} collected 37.00 from pot with Three of a Kind, 8's (combination: J♦, K♥, 8♥, 8♠, 8♣)"
    )
    assert (
        ev.amount == 3700 and ev.data["hand_desc"] == "Three of a Kind, 8's" and ev.data["run"] == 1
    )
    assert ev.cards == ("Jd", "Kh", "8h", "8s", "8c")


def test_collected_second_run():
    ev = classify(
        f"{P} collected 50.10 from pot with Flush, A High on the second run  "
        "(combination: 5♠, 8♠, 9♠, Q♠, A♠)"
    )
    assert ev.data["run"] == 2 and ev.data["hand_desc"] == "Flush, A High" and ev.amount == 5010


def test_uncalled_and_your_hand():
    ev = classify(f"Uncalled bet of 1.00 returned to {P}")
    assert (ev.kind, ev.player, ev.amount) == ("uncalled", HAL, 100)
    ev = classify("Your hand is 10♥, A♣")
    assert (ev.kind, ev.cards) == ("your_hand", ("Th", "Ac"))


def test_run_it_twice_lines():
    assert classify("All players in hand choose to run it twice.").data["agreed"]
    assert not classify("Some players choose to not run it twice.").data["agreed"]
    assert classify('"lou @ lou0000001" chooses to  run it twice.').kind == "rit"


def test_config_id_change_bounty_dead_sb():
    ev = classify("The game's big blind was changed from 0.20 to 0.50.")
    assert (ev.kind, ev.data["what"], ev.amount) == ("config", "bb", 50)
    assert classify("The game's ante was changed from 0.00 to 0.10.").amount == 10
    ev = classify(
        'The player "x @ old1234567" changed the ID from old1234567 to new1234567 because authenticated login.'
    )
    assert (ev.kind, ev.data["old"], ev.data["new"]) == ("id_change", "old1234567", "new1234567")
    ev = classify(f'{P} paid 0.50 for the 7-2 bounty to "dan @ dan0000001"')
    assert (ev.kind, ev.amount, ev.data["to"].player_id) == ("bounty", 50, "dan0000001")
    assert classify("Dead Small Blind").kind == "dead_sb"


def test_session_lines_and_unknown():
    assert (
        classify('The player "Ari @ ari0000001" joined the game with a stack of 50.00.').data[
            "what"
        ]
        == "joined"
    )
    assert (
        classify(
            'The admin updated the player "Mia @ mia0000001" stack from 25.25 to 50.25.'
        ).amount
        == 5025
    )
    assert classify("Game Config Changes\n* Max Time Bank: 90s » 180s").kind == "info"
    assert classify("something PokerNow added next year") is None
