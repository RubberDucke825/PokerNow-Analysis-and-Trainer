from pn.ingest.positions import assign_positions


def test_heads_up_button_is_btn_and_posts_sb():
    assert assign_positions([4, 9], button_seat=4, sb_seat=4, bb_seat=9) == {4: "BTN", 9: "BB"}


def test_three_handed():
    assert assign_positions([1, 3, 7], 3, 7, 1) == {7: "SB", 1: "BB", 3: "BTN"}


def test_six_max():
    got = assign_positions([1, 2, 3, 4, 5, 6], 6, 1, 2)
    assert got == {1: "SB", 2: "BB", 3: "LJ", 4: "HJ", 5: "CO", 6: "BTN"}


def test_eight_handed_wraps_seats():
    seats = [1, 2, 3, 4, 5, 8, 9, 10]
    got = assign_positions(seats, 4, 5, 8)
    assert got == {5: "SB", 8: "BB", 9: "UTG", 10: "UTG1", 1: "LJ", 2: "HJ", 3: "CO", 4: "BTN"}


def test_nine_and_ten_handed():
    nine = assign_positions(list(range(1, 10)), 9, 1, 2)
    assert [nine[s] for s in range(3, 10)] == ["UTG", "UTG1", "UTG2", "LJ", "HJ", "CO", "BTN"]
    ten = assign_positions(list(range(1, 11)), 10, 1, 2)
    assert ten[3] == "UTG" and ten[6] == "UTG3" and ten[10] == "BTN"


def test_dead_small_blind_has_no_sb():
    # fixture hand #80: dealer seat 3, SB seat empty, seat 5 posts BB
    got = assign_positions([1, 2, 3, 5, 8, 9], 3, None, 5)
    assert "SB" not in got.values()
    assert got == {5: "BB", 8: "UTG", 9: "LJ", 1: "HJ", 2: "CO", 3: "BTN"}


def test_dead_button_last_non_blind_is_btn():
    # fixture hand #65: dead button, seat 1 SB, seat 3 BB
    assert assign_positions([1, 3, 4, 8], None, 1, 3) == {1: "SB", 3: "BB", 4: "CO", 8: "BTN"}


def test_straddle():
    got = assign_positions([1, 2, 3, 4, 5, 6], 6, 1, 2, straddle_seat=3)
    assert got[3] == "STR" and [got[s] for s in (4, 5, 6)] == ["HJ", "CO", "BTN"]


def test_missing_blind_posts_fall_back_to_seat_order():
    assert assign_positions([2, 4, 6], 6, None, None) == {2: "SB", 4: "BB", 6: "BTN"}
