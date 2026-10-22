# Normalized Hand History (JSON; one object per hand; stored in SQLite as JSON column + indexed fields)

```json
{
  "hand_id": "znhkbnydNcfa",            // PokerNow id from "-- starting hand #N (id: X) --"
  "hand_no": 298,
  "source": { "kind": "pokernow_log|pokernow_csv", "game_id": "pglSAMPLE0000000000000000", "fetched_at": "ISO" },
  "started_at": "2026-10-01T05:40:40.000Z",
  "variant": "NLHE",
  "blinds": { "sb": 25, "bb": 50, "ante": 0, "straddle": 0 },     // cents
  "button_seat": 3,
  "players": [
    { "seat": 1, "player_id": "hal0000001", "name": "hal", "stack_start": 14750, "position": "BTN", "cards": ["Kh","9c"] | null, "cards_source": "showdown|hero_export|null" }
  ],
  "actions": [
    { "street": "preflop", "seq": 0, "player_id": "...", "kind": "post_sb|post_bb|post_ante|post_straddle|post_missing_sb|fold|check|call|bet|raise", "amount": 50, "to_amount": 150, "allin": false, "at": "ISO" }
  ],
  // amount = chips added this action (cents); to_amount = total committed this street after action (for raise/bet). Both always filled.
  "board": { "flop": ["Kd","Ah","5h"], "turn": "5s", "river": "9h" },
  "runs": [ { "board": {...} } ],          // run-it-twice: full boards per run; `board` = first run
  "showdowns": [ { "player_id": "...", "cards": ["Kh","9c"], "shown": true } ],
  "pots": [ { "amount": 750, "winners": [ {"player_id":"...", "amount": 750, "hand_desc": "Two Pair, K's & 9's"} ] } ],
  "uncalled_returned": [ { "player_id": "...", "amount": 100 } ],
  "net": { "hal0000001": 525, "dan0000001": -525 },     // cents, must sum to 0 (rake 0)
  "flags": ["run_it_twice", "bomb_pot", "straddle", "incomplete", "pot_mismatch"]
}
```
Position assignment: from button seat and seated players in the hand, assign `SB, BB, UTG..., CO, BTN` per contracts/README.md. Heads-up: BTN=SB. Dead small blind → no SB position.

SQLite tables (pn/ingest/schema.sql): `games(game_id, url, first_hand_at, last_hand_at, fetched_at)`, `hands(hand_id PK, game_id, hand_no, started_at, n_players, flags, json)`, `hand_players(hand_id, player_id, seat, position, stack_start, net, cards, vpip, pfr, saw_flop, went_to_sd, won_sd)`, `actions(hand_id, seq, street, player_id, kind, amount, to_amount, allin, pot_before, facing_bet_pct, n_active)`, `players(player_id PK, canonical_id, display_name, first_seen, last_seen)`, `aliases(player_id, canonical_id, note)`.
`canonical_id` groups ids for the same human; defaults to player_id. `pn alias merge <id1> <id2> --name "Full Name"`.
