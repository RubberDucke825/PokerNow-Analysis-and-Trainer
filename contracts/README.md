# Interface Contracts (source of truth for parallel implementors)

Files:
```
hand_history.md     parsed PokerNow hand (ingest -> SQLite)
profile.md          player profile JSON (`pn profile`)
range_format.md     range text and the dense 1326-weight form
tree_config.md      spot config TOML (`pn spot`, `pn solve new`) -> solve service
solve_service.md    `pn.solve`: GTO chart ranges, backend selection, cache, subgames, decision_ev table, solve web API
texas_backend.md    TexasSolver (the only postflop engine): command file, output mapping, measurements
strategy_output.md  solver document JSON (what `SolveCache` stores and every consumer reads)
trainer.md          trainer, leaks, drills, hand review and library fill
```
Changing a contract requires updating all consumers.

Conventions:
- Python: 3.13, `uv`, `pytest`, `ruff`. Type hints everywhere. Immutability preferred (dataclasses frozen=True). No pandas in core parsing; pandas OK in profile/report code.
- Money: all chip amounts in **cents as i64/int** internally (PokerNow shows 0.25/0.50; ledger uses cents). Blinds in config also in cents.
- Cards: string form `As`, `Td`, `2c` (rank in `23456789TJQKA`, suit in `shdc`). Integer form: `rank*4+suit`, rank 0..12 (2..A), suit 0..3 (s,h,d,c). PokerNow uses `♠♥♦♣` and `10`; parser normalizes.
- Combos: `AhKd` (two cards, higher rank first; if equal rank, suit order s<h<d<c first). Dense index 0..1325 from `pn.ranges.combo_index(c1, c2)` / `combo_str_index(combo)`.
- Positions (seat order by action preflop): `UTG, UTG1, UTG2, LJ, HJ, CO, BTN, SB, BB` (8-max uses UTG,UTG1,LJ,HJ,CO,BTN,SB,BB; 6-max uses LJ,HJ,CO,BTN,SB,BB). Straddle: `STR` acts last preflop.
- Streets: `preflop|flop|turn|river`.
- Bet sizes: percent of pot as integer (25,33,50,75,100,150). `allin` as literal.
- All JSON: snake_case keys.
