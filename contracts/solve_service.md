# Solve Service (Python `pn.solve`) — range sources, backends, cache, subgames, EV table

ADDED 2026-10-01 for the trainer / EV-review / solve-from-root work (tasks/todo.md "Plan 2").

## Range source (Plan 7: GTO only)

`RangeSource = "gto"`: the only source (trainer, review, strategy). There are no profile-, population- or
custom-blended ranges; a seat's explicit `range` text (or `overrides`) still replaces its chart range.
`[players.lock]` survives only as the fold lock for folded seats (below); no user or profile locks.

| seat | range |
|---|---|
| postflop seat with a preflop line | chart range for the line: PokerCoaching charts (`config/preflop_charts/pokercoaching_100bb.txt`), then GTO Wizard frequencies/charts, then the hand-written charts (`pn.profile.charts.chart_range`); a built-in default range when no chart has the spot (`pn.adapter.spot_ranges.line_range`) |
| preflop seat at the root | any two |

Preflop spots are built but never solved (see Backend selection).

## Backend selection

**Plan 7 (2026-10-10):** `BACKENDS = ("texas",)` (TexasSolver,
contracts/texas_backend.md). `choose_backend`: heads-up flop/turn/river -> `texas`; preflop ->
`SolveError("preflop_unsolvable")`; 3+ players -> `SolveError("multiway_unsolvable")`. Locks never get this far: `pn.adapter.validate` rejects every
player key except name, position, stack, range and hero, and allows `lock` only as the fold lock on a preflop spot
(`pn.solve.texas.check_spot` keeps a `locks_unsupported` guard). There is no other backend and no fallback: a missing or failing binary
raises `SolveError`. `run_backend(spot, backend=None, *,
should_stop=None, progress=None, timeout=None)`; `SolveCache.solve(..., progress=None, should_stop=None)`
forwards both to the runner when given. `should_stop()` true -> process killed, `Preempted`, nothing cached;
`progress(dict)` gets `{iterations, exploitability_pct_pot, elapsed_sec}` per measured iteration (exceptions
logged and ignored). `cache.is_stale`: a heads-up postflop document whose `meta.backend != "texas"` is stale once
`texas_available()`. Presets stop by iterations/accuracy: trainer 300 it / 0.5% pot, review 600 / 0.3% (cap 2),
library 400 / 0.3% (cap 2, the first flop size only; `pn/pn/solve/request.py:50-51`); `time_limit_sec` only sizes the watchdog. Documents have no `ev`/`action_ev`/`equity`
(`meta.ev_available = false`).

## Python API (`pn/pn/solve/`)

```python
@dataclass(frozen=True)
class Seat:
    player_id: str | None      # None = anonymous GTO seat
    position: str              # contracts/README.md position names
    stack: int                 # cents at start of `street`
    hero: bool = False

@dataclass(frozen=True)
class SpotRequest:
    seats: tuple[Seat, ...]    # in action order on `street`
    street: str
    board: tuple[str, ...]
    pot: int
    sb: int; bb: int; ante: int = 0; straddle: int = 0
    source: RangeSource = "gto"
    overrides: Mapping[str, str] = {}        # seat key (player_id, or position for anonymous seats) -> range text
    last_aggressor: str | None = None
    budget: Budget = Budget()                # max_iterations, time_limit_sec, target_exploitability

def build_spot(req: SpotRequest, profiles: ProfileSource | None) -> dict          # tree_config dict (validated)
def build_spot_from_hand(hand, street, hero_id, *, source, ...) -> dict  # wrapper over build_spot (existing behaviour preserved)
def spot_key(spot: dict) -> str            # sha256 of canonical TOML (ranges, board, stacks, sizes, budget); the cache key
class SolveCache:                          # data/solves/cache/<key>.json (+ .toml); LRU in memory
    def get(key) -> dict | None
    def solve(spot: dict, backend: str | None = None) -> dict   # runs the backend subprocess; single global lock (one solve at a time)
def subgame(parent: dict, node_id: str, next_cards: tuple[str, ...], req_hint: SpotRequest) -> dict  # next-street SpotRequest/tree_config from reach_by_player at node_id, dead cards removed, stacks/pot from the node
def action_ev_loss(doc: dict, node_id: str, combo: str, chosen_token: str) -> tuple[float, str]      # (loss_cents, best_token) from action_ev; EvUnavailable for texas documents (no action_ev)
```

CLI: `pn spot <hand_id> --street flop [--hero ID] [--range KEY=TEXT] [--out f.toml]`, `pn solve new --seats BTN,BB --depth 100 [--board ... --line srp|3bp] [--range POS=TEXT]`, `pn solve run <spot.toml> [--preset ...] [--progress/--no-progress]` (prints the key, backend and `meta.exploitability_pct_pot`; progress lines on stderr, by default only when stderr is a terminal, through the `progress` hook below), `pn library fill|status` (the trainer's heads-up flop library, contracts/trainer.md 2026-10-07 "library-first heads-up flops").
Library distribution: `tools/library_pack.py pack|unpack|sample` moves the flop library between caches as sha256-checked tar volumes with a `MANIFEST.json`; a 60-board sample is committed as `data/fixtures/library_sample.tar.gz` (contracts/trainer.md "Library distribution"). Solver-core golden tests (`pn/tests/test_solver_golden.py`) pin the command file and cache keys of the README spot per preset.

Library fills go through `SolveCache` keyed by `spot_key`; there are no preflop library roots.

## EV review table (SQLite, `pn/pn/ingest/schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS decision_ev (
  hand_id TEXT NOT NULL, seq INTEGER NOT NULL, source TEXT NOT NULL,   -- "gto" (older rows may carry other sources)
  street TEXT NOT NULL, player_id TEXT NOT NULL,
  chosen TEXT NOT NULL, best TEXT NOT NULL,                             -- action tokens (snapped to tree)
  ev_chosen INTEGER, ev_best INTEGER, loss INTEGER,                     -- cents; NULL for texas documents (no EVs)
  gto_freq_chosen REAL,                                                 -- preflop only: chart (PokerCoaching / GTO Wizard) frequency of the chosen action
  backend TEXT, solve_key TEXT,                                         -- postflop: texas; preflop: pokercoaching | gtowizard
  exploitability REAL,                                                  -- meta.exploitability_pct_pot of the solve
  computed_at TEXT NOT NULL,
  note TEXT,                                                            -- why the row is not graded (multiway_unsolvable, no_preflop_frequencies, solve_failed: ..., ...), or combo_added_to_range (graded off-model)
  spot TEXT,                                                            -- preflop: spots.py situ kind or "other"; postflop: first|facing_bet|facing_raise
  PRIMARY KEY (hand_id, seq, source)
);
```
Plan 7 adds `grade`, `freq_chosen`, `best_freq` (contracts/trainer.md "Plan 7 additions"). Only decisions of players with known hole cards are rows. `pn ev run [player]` fills it; `/api/hands/{id}/ev` and `/api/review/{hero}/ev` read it.

## As implemented by stream S (2026-10-01), additions to the API above

- `Seat` also has `name`, `line`, `vs` (preflop line context for chart/GTO Wizard lookups) and `range` (explicit text that bypasses the source). `SpotRequest` also has `table`, `mode`, `sizes`, `hero_cards`.
- `build()` returns the config dict plus per-seat range origins (used for TOML comments and warnings); `build_spot()` returns the dict only.
- Python validator requires exactly one hero. Anonymous spots default hero to the first seat.
- `SolveCache(root, runner, memory_docs=4)`; the cache key ignores the backend; `meta.backend` is filled in only when the binary omits it. A missing or failing backend binary raises `SolveError` (no fallback, 2026-10-07; older documents may still carry `meta.backend_fallback`).
- `subgame(parent, node_id, next_cards, hint, profiles=None)`; `subgame_request(...)` returns the `SpotRequest`. Works on leaf terminals and chance nodes; derives reach from the parent action node when a leaf lacks `reach_by_player`.
- `action_ev_loss` raises `EvUnavailable` (a `ValueError`) when `action_ev` is missing (every texas document). `pn.live.assist.ev_loss_for` wraps it.
- CLI: `pn library fill` is described in contracts/trainer.md (2026-10-07, library-first heads-up flops); the old preset x flop fill is gone. `pn solve new` postflop takes `--line srp|3bp` to derive pot and stacks; `--range POS=TEXT` replaces a seat's range.

## Additions 2026-10-02 (stream S, round 2)

- `Seat.folded`: a BB that folded before the decision stays listed (the tree requires a BB preflop) with the automatic fold lock `FOLD_LOCK` (`"preflop:*:*" = { fold = 1.0, order = "uniform" }`; checks when fold is illegal); its blind stays in the pot. This is the only lock a spot can carry.
- Presets: `SOLVE_PRESETS`, `Budget.preset(name)`, `apply_preset(spot, name) -> (spot, backend)`. `apply_preset` returns the backend from `choose_backend` (SolveError for spots no backend solves). `trainer` = 300 it / 0.5% pot; `review` = 600 it / 0.3%, `max_bets_per_street = 2`; `library` = 400 it / 0.3%, `max_bets_per_street = 2`, one flop size (the first of the sizing table entry). `time_limit_sec` (10 / 120 / 3600) only sizes the watchdog. Cap and budget are written into the TOML so `spot_key` differs per preset. `pn solve run --preset`; `pn library fill` uses `library`. Texas emits one street (`set_dump_rounds 1`); later streets are lifted with `subgame`.
- Preflop knobs: every preflop-start spot with more than 3 players gets `limp = "sb_only"`, `cold_call_3bet = false`, plus `max_callers = 2` above 6 players. Hand-based spots relax a knob when the real line needs it (limp -> `limp = "all"`; cold call of a 3-bet -> `cold_call_3bet = true`; third caller -> `max_callers = 8`), recorded in the TOML header comment.
- Registry persistence: generated solves (New spot, subgames) are indexed in `<cache root>/registry_names.json` with their cache key, so names survive eviction and restarts; trainer street solves stay memory-only.

## 2026-10-07: 2-player only

Multiway postflop (3+ players still in at the start of the flop, turn or river) is neither solved nor graded.
- `choose_backend` (and so `run_backend`, `SolveCache.solve` and `apply_preset`) raises `SolveError("multiway_unsolvable")` for such spots; no solver runs. `pn.solve.backend.is_multiway_postflop(spot)` is the shared test.
- Hand review (`pn ev run`): in a hand that saw the flop with 3+ players, decisions on every postflop street (also a later one that is heads-up again) get a `decision_ev` row with no grade and `note = "multiway_unsolvable"`.
- Live assist: `cache_solver` raises `LiveError("multiway_unsolvable")` for multiway postflop spots. Heads-up postflop is cache-only: a cached texas document whose `spot_key` matches the live spot (as is, or with the `library` / `review` / `trainer` preset applied) is served, else `LiveError("no_solve_available: ...")`; live assist never starts a solve. Preflop spots raise `LiveError` (`preflop_unsolvable`).
- Stale cache entries (`pn.solve.cache.is_stale`): a cached 2-player postflop document whose `meta.backend != "texas"` is a miss once TexasSolver is available. `SolveCache.solve` logs it at warning level and re-solves under the same `spot_key`, so registry names built from the key stay stable. Preflop and multiway documents are never stale.

## Plan 7 (2026-10-10, stream T3): study UI solve service

### Progress / cancel hooks (backend side, stream T1)
- `SolveCache.solve(spot, backend=None, *, priority=False, background=False, progress=None, should_stop=None)` forwards `progress=` and `should_stop=` to the runner as keyword arguments, only when they are not None. `run_backend(spot, backend=None, *, should_stop=None, progress=None)` hands both to `pn.solve.texas`.
- `progress(d: dict) -> None` is called from the solver thread on every printed TexasSolver iteration ("Total exploitability" log line), with `d = {"iterations": int, "exploitability_pct_pot": float | None, "elapsed_sec": float}`. Exploitability is in % of the pot, like `meta.exploitability_pct_pot`, and is None before the first measurement. `elapsed_sec` counts from the subprocess start. Exceptions raised by the callback are logged and ignored.
- `should_stop() -> bool` is polled (every <= 0.5 s). When it returns True the subprocess is terminated (then killed after 2 s), `pn.solve.backend.Preempted` is raised, and nothing is written to the cache.
- `pn.web.jobs.cache_solve(cache, spot, backend, hooks)` passes the hooks only if `cache.solve` accepts them (`inspect.signature`), so the web layer also works against a cache without them: the job then has no progress, and a cancelled job only discards its result.

### Jobs (`pn/pn/web/jobs.py`)
- One solve at a time in a background thread. Each job gets `SolveHooks(progress, should_stop)`.
- `GET /api/solves/jobs/{job_id}` -> `{job_id, status: "queued"|"running"|"done"|"error"|"cancelled", elapsed_sec, iterations: int|null, exploitability_pct_pot: float|null, curve: [[solver_elapsed_sec, iterations, exploitability_pct_pot|null], ...] (last 240 points), name?, error?, estimate_bytes?, limit_bytes?}`. `elapsed_sec` is the job's wall clock since it started running. `exploitability_pct_pot` is the last non-null value reported. 404 for an unknown job.
- `POST /api/solves/jobs/{job_id}/cancel` -> the job view with `status: "cancelled"` (immediately). It sets the job's stop flag. A queued job never runs, and the result of a running one is discarded. 404 for an unknown job, 409 when the job is already done or failed.

### Routes (`pn/pn/web/solve_routes.py`; replaces the solve block of `train_routes.py`)
- `POST /api/solves/new` (202) body `{table=6, depth=100, source="gto", street: "preflop"|"flop"|"turn"|"river", board: [3|4|5 cards] | "KdTs2c9h", line: "srp"|"3bp", hero?, seats: [{position, player_id?, range?}] (postflop: exactly 2), preset: "trainer"|"review"|"library" (default review), ranges?: {"oop"|"ip"|<position>: text}, sizes?: {flop_bets, turn_bets, river_bets, raise, allin_threshold, donk, max_bets_per_street}, pot?: cents, stack?: effective cents behind}` -> `{job_id}`. The spot is built and validated before the job starts: 422 with the message for a bad board (wrong length, bad or repeated cards), board/street mismatch, non-heads-up postflop, unknown `ranges` key, unknown `sizes` key, empty size lists, non-positive pot/stack, or an engine validation error. 400 for an unknown preset. The preset is used as given (the earlier review -> library swap for heads-up flops is gone). `GET /api/solves/{name}` -> `spot` echoes `sizes` / `pot` / `stack` when given.
- `POST /api/solves/{name}/subgame` is unchanged, except for 422 on bad or repeated cards. It reports progress and can be cancelled like any job.
- `GET /api/solves/library` -> `[{name, key, street, board, positions: [oop, ip], hero, pot, bb, pot_bb, stack_bb, depth_bb, pot_type: "limped"|"srp"|"3bp"|"4bp+", sizes, max_iterations, target_exploitability, source, parent: null|{name,node,cards,path}, line: "srp"|"3bp"|null, backend, iterations, exploitability_pct_pot, stop, wall_sec, size_bytes, date: ISO UTC}]`, one row per SolveCache document (`<cache>/<key>.json` + `.toml`), newest first. `meta` is read from the first 8 KB of the file. `name` is the indexed registry name for that key (a root name is preferred over a subgame's), else `cache-<first 16 hex of the key>`.
- `cache-<key prefix>` names open any SolveCache document in every `/api/solves/{name}...` route. The board comes from the spot TOML.
- `DELETE /api/solves/{name}` -> `{deleted: [names], files: [paths]}`. It deletes the cache entry (`.json` + `.toml`) of a generated or `cache-` solve, drops every indexed name sharing that key, and evicts the documents from memory. A `<solves_dir>` file solve has its file deleted, and a library root is only unregistered. 404 for an unknown name, 400 for `sample`.
- `GET /api/solves/{name}/ranges?path=tok,tok` -> `{id, type, player, players: [{index, position, reach: [1326 floats] | null}]}`: every player's combo weights at the node. Each weight is the range weight times the product of the player's own strategy along the path, and dealt cards zero the combos they block. A node's own `reach_by_player` (or the actor's `reach`) wins when present. A player's starting range is its reach at the first node down the first-child line where it acts. 404 for a bad path.
  `?node=<node id>` instead of `path` addresses the same node by id (the trainer's `feedback.grid_ref.node_id`); 404 for an unknown id. (Plan 15)
- `GET /api/solves/lines?table=6` -> `[{id: "srp:BTN:BB", line, positions: [opener, other], oop, ip, label: "BTN opens, BB calls"}]`: every seat pair, both srp and 3bp. 400 for an unknown table.
- `GET /api/solves/line?line=srp&positions=BTN,BB&table=6&depth=100` -> `{table, depth, line, pot, bb, stack, oop: {position, stack, range, origin, combos}, ip: {...}}`. These are the chart ranges `build_spot` uses for source `gto`, before board cards are removed. 422 for a bad line or seats.
- `GET /api/solves/presets` -> `{presets: [{name, max_iterations, accuracy_pct_pot, time_limit_sec, threads, max_bets_per_street}], default_sizes: {...DEFAULT_SIZES}}`.
- SPA pages: `/strategy` (saved solves), `/strategy/new` (spot builder), `/strategy/{name}?node=tok,tok` (tree browser).
