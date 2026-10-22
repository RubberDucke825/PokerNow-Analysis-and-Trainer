#!/usr/bin/env bash
# One entry point for the whole workflow. Run from anywhere; it cds to the repo root.
#   ./poker.sh setup                         build TexasSolver, uv sync the Python env (pn/.venv)
#   ./poker.sh ingest <game-url|id|log.json|export.csv> [--name "Full Name=<id or username>" ...]
#   ./poker.sh study [player]                profile everyone, then the player's matrix, leaks, tips, review
#   ./poker.sh solve <hand_id> [street]      build the spot for hero and solve it -> data/solves/
#   ./poker.sh library fill|status [...]     trainer flop library (pn library --help)
#   ./poker.sh library-overnight [args]      detached `pn library fill` (caffeinate); library-status
#   ./poker.sh web                           study UI at http://127.0.0.1:8765
#   ./poker.sh all <game-url|id|csv> [...]   ingest + study + web
#   ./poker.sh test                          pytest + ruff
#   ./poker.sh quickstart                    after setup: sample flop library, fixture game, one turn solve, web
# Hero is your PokerNow player id, from PN_HERO=<id> (ingest of a CSV, solve and study without a
# player need it); database is data/pn.sqlite (PN_DB=<path> overrides).
set -euo pipefail
cd "$(dirname "$0")"

HERO="${PN_HERO:-}"
DB="${PN_DB:-data/pn.sqlite}"; export PN_DB="$DB"   # ingest takes --db; the other commands read PN_DB
VENV=pn/.venv

activate() {
    [ -x "$VENV/bin/pn" ] || { echo "no venv: run ./poker.sh setup" >&2; exit 1; }
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
}

cmd_setup() {
    command -v uv >/dev/null || {
        echo "uv not found: install it from https://docs.astral.sh/uv/getting-started/installation/" >&2
        exit 1
    }
    tools/build_texassolver.sh             # needs cmake + OpenMP (macOS: brew install cmake libomp)
    (cd pn && uv sync --all-extras --frozen)   # pn/.venv from pn/uv.lock, Python per pn/.python-version
    echo "ok: third_party/texassolver/build/console_solver and $VENV ready"
}

cmd_ingest() {
    [ $# -ge 1 ] || { echo "usage: ./poker.sh ingest <game-url|id|log.json|export.csv> [--name ...]" >&2; exit 1; }
    local src=$1; shift
    activate
    case "$src" in
        *.csv) pn import-csv "$src" --db "$DB" --hero "${HERO:?set PN_HERO}" "$@" ;;   # logged-in export: has hero's hole cards
        *)     pn fetch "$src" --db "$DB" "$@" ;;                       # public log (URL, id or saved JSON)
    esac
}

cmd_study() {
    local player=${1:-${HERO:?set PN_HERO}}
    activate
    pn profile all
    pn profile "$player" --by-position
    pn pos "$player"
    pn pos leaks "$player"
    pn tips "$player"
    pn review "${HERO:-$player}"
    if [ "${PN_EV:-0}" = 1 ]; then pn ev run "$player"; fi   # frequency grade per decision (slow: postflop solves)
}

cmd_solve() {
    [ $# -ge 1 ] || { echo "usage: ./poker.sh solve <hand_id> [flop|turn|river]" >&2; exit 1; }
    local hand=$1 street=${2:-flop}
    activate
    mkdir -p data/solves
    local spot="data/solves/${hand}-${street}.toml"
    pn spot "$hand" --street "$street" --hero "${HERO:?set PN_HERO}" --out "$spot"   # heads-up postflop only
    pn solve run "$spot" --preset review                              # TexasSolver, through the cache
    echo "solved (open it in ./poker.sh web, Strategy tab)"
}

cmd_web() { activate; pn web "$@"; }
cmd_library() { activate; pn library "$@"; }

cmd_library_overnight() {
    activate
    local dir=data/solves/library; mkdir -p "$dir"
    local pidf="$dir/fill.pid" out="$dir/fill.log"
    if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then
        echo "already running: pid $(cat "$pidf")" >&2; exit 1
    fi
    # New session so closing the terminal does not stop it: setsid where it exists (Linux),
    # else the python os.setsid trick (macOS has no setsid binary). caffeinate -i (macOS only)
    # keeps the machine from idle-sleeping (a closed lid still sleeps).
    local newsess=(python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])')
    command -v setsid >/dev/null && newsess=(setsid)
    local awake=()
    command -v caffeinate >/dev/null && awake=(caffeinate -i)
    nohup "${newsess[@]}" ${awake[@]+"${awake[@]}"} pn library fill "$@" \
        > "$out" 2>&1 < /dev/null &
    echo $! > "$pidf"
    echo "started pid $(cat "$pidf") (pid file $pidf)"
    echo "log: $out   status: ./poker.sh library-status"
}

cmd_library_status() {
    activate
    local dir=data/solves/library
    if [ -f "$dir/fill.pid" ] && kill -0 "$(cat "$dir/fill.pid")" 2>/dev/null; then
        echo "running: pid $(cat "$dir/fill.pid")"
    else
        echo "not running"
    fi
    tail -n 20 "$dir/fill.log" 2>/dev/null || echo "no $dir/fill.log yet"
    pn library status
}
cmd_ev() { activate; pn ev "$@"; }

cmd_all() { cmd_ingest "$@"; cmd_study; cmd_web; }

# One sample-library flop (data/fixtures/library_sample.tar.gz: 6-max 100bb BTN open, BB call)
# plus a turn card, solved with the trainer preset (a few seconds).
QS_BOARD=6c4c2dKh

cmd_quickstart() {
    activate
    local t0=$SECONDS cache=${PN_SOLVE_CACHE:-data/solves/cache}
    if [ -f "$cache/hu_flop_library.json" ]; then
        echo "== flop library already in $cache: sample not unpacked"
    else
        echo "== unpacking the sample flop library into $cache"
        python tools/library_pack.py unpack data/fixtures/library_sample.tar.gz "$cache"
    fi
    local log gid ledger
    log=$(ls data/fixtures/pokernow_*.json | head -n 1)
    gid=${log##*/pokernow_}; gid=${gid%.json}
    ledger=data/fixtures/ledger_$gid.csv
    echo "== ingesting the fixture game $gid into $DB"
    if [ -f "$ledger" ]; then
        pn fetch "$log" --game-id "$gid" --db "$DB" --ledger "$ledger"
    else
        pn fetch "$log" --game-id "$gid" --db "$DB" --no-ledger
    fi
    echo "== solving BTN vs BB single-raised pot on $QS_BOARD (turn, trainer preset)"
    mkdir -p data/solves
    local spot=data/solves/quickstart-turn.toml
    pn solve new --seats BTN,BB --line srp --hero BTN --board "$QS_BOARD" --out "$spot"
    pn solve run "$spot" --preset trainer --progress
    echo "== quickstart ready in $((SECONDS - t0)) s"
    echo "open http://127.0.0.1:8765 (Train: BTN vs BB single-raised flops come from the sample library)"
    pn web
}

cmd_test() {
    activate
    (cd pn && ruff check pn tests && python -m pytest -q)
}

case "${1:-help}" in
    setup|ingest|study|solve|library|ev|web|all|test|quickstart) c=$1; shift; "cmd_$c" "$@" ;;
    library-overnight) shift; cmd_library_overnight "$@" ;;
    library-status) cmd_library_status ;;
    *) sed -n '2,14p' "$0" ;;
esac
