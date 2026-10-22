# Tree / Spot Config (TOML) — written by `pn spot` / `pn solve new`, checked by `pn.adapter.validate`, solved by the texas backend

Plan 7 (2026-10-10): TexasSolver (contracts/texas_backend.md) is the only engine. It solves heads-up flop, turn and
river spots only; preflop spots validate but are refused (`preflop_unsolvable`), as are 3+ players.

```toml
[game]
street = "flop"               # starting street of the solve: preflop|flop|turn|river (only flop|turn|river are solved)
board = ["Kd","Ah","5h"]      # 0,3,4,5 cards
pot = 1500                    # cents, pot at start of `street` (dead money included)
sb = 25
bb = 50                       # cents; required for preflop spots and for bb-denominated reporting
ante = 0
straddle = 0
last_aggressor = "BTN"        # optional position; with donk = false, OOP gets no flop bet when IP was the aggressor
max_iterations = 1000         # texas: set_max_iteration
target_exploitability = 0.5   # % of pot; texas: set_accuracy (stop when reached)
time_limit_sec = 300          # sizes the watchdog only (time_limit_sec x 1.5 + 30 s); solves stop by iterations/accuracy
threads = 8

[[players]]                   # in order of action on `street` (postflop: first to act first). Texas: exactly 2 (OOP, IP).
name = "hero"
position = "BB"
stack = 9800                  # cents, remaining behind at start of street
range = "22+,A2s+,K9s+,..."   # range text (see range_format.md)
hero = true

[[players]]
name = "hal @ hal0000001"
position = "BTN"
stack = 14700
range = "..."

[sizes]                       # betting abstraction
flop_bets = [33, 100]         # 1-2 sizes from {25,33,50,100}; "auto" -> config/sizing_table.toml by texture
turn_bets = [75]              # exactly 1 of {75,150}
river_bets = [150]            # exactly 1 of {75,150}
raise = [100]                 # raise size as % of pot AFTER calling (single size); allin always when legal
max_bets_per_street = 3       # bet + 2 raises; texas: set_raise_limit (local patch)
allin_threshold = 0.67        # a bet/raise committing more than this fraction of the stack becomes allin
donk = true                   # allow OOP leads (bet when not last aggressor)
```

How texas reads each key, and where its all-in rule differs, is in contracts/texas_backend.md.

## Preflop keys (validated, not solved)

Preflop spots (`pn solve new` without a board, hand spots on `street = "preflop"`) are still built and validated, so the
validator still accepts these keys. No backend solves them since Plan 7.

```toml
[game]
mode = "depth_limited"          # "full" | "depth_limited"; preflop requires depth_limited; full needs <= 3 players
hand_abstraction = "combo"      # "combo" | "class" (class only with street = "preflop")
mc_samples = 20000
leaf_model = "equity_realization"   # or a [leaf_model] table {kind = "equity_realization", realization = "config/realization.toml"}; not both

[sizes.preflop]
preset = "gtowizard"            # open = [250], three_bet_ip = [300], three_bet_oop = [350], four_bet = [220], five_bet = ["allin"]
open = [300]                    # raise-to in % of BB; 1-3 entries, each > 100
three_bet_ip = [300]            # % of the open's raise-to
three_bet_oop = [350]
three_bet_by_pos = { SB = 1100, BB = 1200 }   # raise-to in % of BB for a 3-bet by that position
squeeze = { base = 300, per_caller = 100 }
four_bet = [220]                # % of the 3-bet raise-to
five_bet = ["allin"]            # % of the 4-bet raise-to or "allin"
max_bets = 5                    # >= 2
limp = "all"                    # "all" | "sb_only" | "none"
cold_call_3bet = true
# max_callers = 2               # optional integer >= 1
```

`[players.lock]` survives only as the fold lock: no user or profile locks. `pn.solve.build` adds it to a BB that folded
before the decision (the tree requires a listed BB preflop). `pn.adapter.validate` accepts these player keys only: name,
position, stack, range, hero, plus `lock` when it equals FOLD_LOCK on a preflop spot. Any other lock fails as an unknown key.
```toml
[players.lock]                  # the fold lock, the only one allowed
"preflop:*:*" = { fold = 1.0, order = "uniform" }   # FOLD_LOCK: folds, checks when fold is illegal
```

Preflop spots need `sb`, `bb`, a listed BB, and a pot covering the listed players' blinds. `straddle > 0` exactly when an
`STR` player is listed. `config/realization.toml`, when referenced, must have only `[base]` (ip, oop, sb, bb > 0;
multiway_penalty >= 0) and `[hand]` (pair, suited, connected, high_card, broadway2) with finite numbers.
