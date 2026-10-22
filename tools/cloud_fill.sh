#!/usr/bin/env bash
# Run the trainer's library fill sharded on a Linux box (`pn library fill --shard i/n`), then pull the
# solved documents back into data/solves/cache. The cache is content-addressed, so merging is a copy;
# the next local `pn library fill` records the pulled boards as "cached". Each shard gets its own
# PN_SOLVE_CACHE (the solve lock and fill pid file live in the cache root), so shards run in parallel.
#
#   tools/cloud_fill.sh push   user@host          rsync repo + index/sizes/non-library docs (tools/seed_list.py), build solver, venv
#   tools/cloud_fill.sh bootstrap user@host       like push, but the box clones the public mirror + downloads the seed asset (no bulk upload)
#   tools/cloud_fill.sh start  user@host N [fill args]   N shards (default args: the current local run)
#       several boxes: SHARD_BASE=k SHARD_TOTAL=T start user@host N runs shards k..k+N-1 of T
#   tools/cloud_fill.sh resume user@host N [fill args]  after a preemption: clean half-written files, relaunch missing shards
#   tools/cloud_fill.sh refresh user@host         push the current flop index into every shard root (after each local walk)
#   tools/cloud_fill.sh status user@host
#   tools/cloud_fill.sh stop   user@host
#   tools/cloud_fill.sh pull   user@host          shard caches -> data/solves/cache (rerun fill to index)
#   tools/cloud_fill.sh keys   user@host [fill args]  dry-run sample flop keys here and on the box; must match
#
# Plan 16 (default args): one order over the 8-max 100bb, 6-max 200bb and 8-max 200bb libraries, the
# weighted 184-flop subset, no limped pots and no 8-max line that aliases a 6-max one; 100bb to 1% pot,
# 200bb to 2% (user decision). Walk after each
# pull with the SAME args plus --cached-only (another plan indexes nothing):
#   pn library fill --configs 0 --subset 184 --lib 8x100 --lib 6x200 --lib 8x200 --new-only --accuracy 1 --depth-accuracy 200=2 --cached-only
#
# Box: Ubuntu 24.04, 3 vCPU + 1.5 GB per shard (a solve uses ~2.4 cores, ~1 GB). Keep the solver's
# default thread count: it is part of the cache key, so a different value would never be looked up.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_DIR=pn-fill  # also hard-coded in the remote snippets below
# Plan 16 (Plan 11 used: --configs 0 --flops all --pots srp --accuracy 1 --reverse)
# GHA walks forward (tools/gha/fill.yml, same args without --reverse); a box walks the light end,
# so the two pools meet in the middle (pn/tests/test_fill_tooling_args.py checks both)
DEFAULT_FILL_ARGS=(--configs 0 --subset 184 --lib 8x100 --lib 6x200 --lib 8x200 --new-only --accuracy 1 --depth-accuracy 200=2 --reverse)

cmd="${1:-}"; host="${2:-}"
[[ -n "$cmd" && -n "$host" ]] || { sed -n '2,20p' "$0"; exit 1; }
R="\$HOME/$REMOTE_DIR"

push() {
  rsync -az --delete \
    --exclude .git --exclude target --exclude .venv --exclude build --exclude __pycache__ \
    --exclude .claude --exclude data --exclude graphify-out --exclude '*.log' \
    "$ROOT/" "$host:$REMOTE_DIR/"
  ssh "$host" "mkdir -p $R/data/solves/cache $R/data/shards"
  # the same inputs the GHA seed carries (tools/seed_list.py): config, the flop index (shards skip
  # indexed boards by it), the GTO Wizard sizes, and the non-library documents
  (cd "$ROOT" && pn/.venv/bin/python tools/seed_list.py | rsync -az --files-from=- . "$host:$REMOTE_DIR/")
  ssh "$host" bash -s <<'REMOTE'
set -euo pipefail
cd "$HOME/pn-fill"
if ! command -v cmake >/dev/null || ! command -v g++ >/dev/null; then
  sudo apt-get update -qq && sudo apt-get install -y -qq build-essential cmake rsync >/dev/null
fi
command -v uv >/dev/null || { curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null; }
export PATH="$HOME/.local/bin:$PATH"
mkdir -p third_party/texassolver/build && cd third_party/texassolver/build
cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 >/dev/null
make -j"$(nproc)" console_solver 2>&1 | tail -1
cd "$HOME/pn-fill/pn"
[[ -x .venv/bin/pn ]] || { uv venv --python 3.13 -q .venv && uv pip install -q -e . --python .venv/bin/python; }
.venv/bin/pn library fill --dry-run --configs 1 --flops 1 --db ../data/none.sqlite | tail -1
echo "ready: $(nproc) cores, $(free -g | awk '/Mem:/{print $2}') GB"
REMOTE
}

bootstrap() {
  # Like push, but nothing bulky leaves this machine: the box clones the public mirror (PN_FILL_REPO:
  # pn/ + third_party/texassolver) and downloads the encrypted seed asset from its GitHub release
  # (config + index + GTO Wizard sizes + non-library documents), then builds the solver and venv.
  # Use it when rsync uploads stall; `pull`/`refresh` still work (small transfers).
  local key_file="$HOME/.ssh/pn_fill_key" repo="${PN_FILL_REPO:?set PN_FILL_REPO=<owner>/<repo> (the public fill mirror)}"
  local url; url=$(gh release view seed -R "$repo" --json assets -q '.assets[]|select(.name=="seed.tgz.enc")|.url')
  ssh "$host" "FILL_KEY='$(cat "$key_file")' SEED_URL='$url' MIRROR='https://github.com/$repo' bash -s" <<'REMOTE'
set -euo pipefail
cd "$HOME"
if [[ -d pn-fill/.git ]]; then git -C pn-fill pull -q; else rm -rf pn-fill; git clone -q --depth 1 "$MIRROR" pn-fill; fi
curl -sL -o seed.tgz.enc "$SEED_URL" && openssl enc -d -aes-256-cbc -pbkdf2 -pass env:FILL_KEY -in seed.tgz.enc | tar xz -C pn-fill && rm seed.tgz.enc
mkdir -p pn-fill/data/solves/cache pn-fill/data/shards
echo "seeded: $(find pn-fill/data/solves/cache -maxdepth 1 -name '[0-9a-f]*.json*' ! -name '*.tmp' | sed -E 's/[.]json([.]gz)?$//' | sort -u | wc -l) documents (keys), index $(python3 -c "import json;print(sum(len(m.get('boards', {})) for m in json.load(open('pn-fill/data/solves/cache/hu_flop_library.json'))['matchups'].values()))") boards"
cd "$HOME/pn-fill"
if ! command -v cmake >/dev/null || ! command -v g++ >/dev/null; then
  sudo apt-get update -qq && sudo apt-get install -y -qq build-essential cmake rsync >/dev/null
fi
command -v uv >/dev/null || { curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null; }
export PATH="$HOME/.local/bin:$PATH"
mkdir -p third_party/texassolver/build && cd third_party/texassolver/build
cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 >/dev/null
make -j"$(nproc)" console_solver 2>&1 | tail -1
cd "$HOME/pn-fill/pn"
[[ -x .venv/bin/pn ]] || { uv venv --python 3.13 -q .venv && uv pip install -q -e . --python .venv/bin/python; }
.venv/bin/pn library fill --dry-run --configs 1 --flops 1 --db ../data/none.sqlite | tail -1
echo "ready: $(nproc) cores, $(free -g | awk '/Mem:/{print $2}') GB"
REMOTE
}

start() {
  local n="${3:?N shards}"; shift 3 || true
  local args=("$@"); [[ ${#args[@]} -gt 0 ]] || args=("${DEFAULT_FILL_ARGS[@]}")
  # one cache root per shard: separate solve lock and fill pid; the seeded cache is hard-linked in
  # (same inode, so `pull` sees unchanged files and skips them)
  local base="${SHARD_BASE:-0}" total="${SHARD_TOTAL:-$n}"
  ssh "$host" bash -s "$n" "$base" "$total" "${args[@]}" <<'REMOTE'
set -euo pipefail
n="$1"; base="$2"; total="$3"; shift 3
cd "$HOME/pn-fill"
for i in $(seq "$base" $((base + n - 1))); do
  d="data/shards/$i"; mkdir -p "$d"
  find data/solves/cache -maxdepth 1 \( -name '*.json' -o -name '*.json.gz' -o -name '*.toml' \) ! -name hu_flop_library.json \
    -exec cp -ln {} "$d/" \; 2>/dev/null || true
  rm -f "$d/hu_flop_library.json"; cp data/solves/cache/hu_flop_library.json "$d/"  # own copy: rewritten per board
  # PASSIVE: shards oversubscribe the cores (8 OpenMP threads each); spinning waits starve each other.
  # --db none.sqlite: the same burn priors as the GHA shards, so both pools walk one order.
  OMP_WAIT_POLICY=PASSIVE PN_SOLVE_CACHE="$PWD/$d" nohup pn/.venv/bin/pn library fill "$@" --shard "$i/$total" \
    --db "$PWD/data/none.sqlite" > "data/shards/shard$i.log" 2>&1 &
done
sleep 2; echo "started shards $base..$((base + n - 1)) of $total: $(pgrep -fc 'pn library fill') fills running"
REMOTE
}

resume() {
  # after a spot preemption: drop half-written documents, repair each shard's index copy, relaunch
  # the shards that are not running (same base/count/total and fill args as `start`)
  local n="${3:?N shards}"; shift 3 || true
  local args=("$@"); [[ ${#args[@]} -gt 0 ]] || args=("${DEFAULT_FILL_ARGS[@]}")
  local base="${SHARD_BASE:-0}" total="${SHARD_TOTAL:-$n}"
  ssh "$host" bash -s "$n" "$base" "$total" "${args[@]}" <<'REMOTE'
set -euo pipefail
n="$1"; base="$2"; total="$3"; shift 3
cd "$HOME/pn-fill"
echo "empty files removed: $(find data/shards -maxdepth 2 \( -name '*.json' -o -name '*.json.gz' -o -name '*.toml' \) -size 0 -print -delete | wc -l)"
started=0
for i in $(seq "$base" $((base + n - 1))); do
  d="data/shards/$i"; mkdir -p "$d"
  python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$d/hu_flop_library.json" 2>/dev/null \
    || { echo "shard $i: index copy replaced"; cp data/solves/cache/hu_flop_library.json "$d/"; }
  if ! pgrep -f "pn library fill .*--shard $i/$total" >/dev/null; then
    rm -f "$d/.library_fill.pid"
    OMP_WAIT_POLICY=PASSIVE PN_SOLVE_CACHE="$PWD/$d" nohup pn/.venv/bin/pn library fill "$@" --shard "$i/$total" \
      --db "$PWD/data/none.sqlite" >> "data/shards/shard$i.log" 2>&1 &
    started=$((started + 1))
  fi
done
sleep 3; echo "relaunched $started; fills running: $(pgrep -fc 'pn library fill')"
REMOTE
}

refresh() {
  # push the local flop index into the box's seed cache and every shard root: a shard skips a board
  # by its index copy alone, and other pools (GitHub, other boxes) keep adding boards. FlopIndex
  # re-reads on mtime change, so running shards pick it up; their own boards stay "cached".
  rsync -az "$ROOT/data/solves/cache/hu_flop_library.json" "$host:$REMOTE_DIR/data/solves/cache/"
  ssh "$host" bash -s <<'REMOTE'
cd "$HOME/pn-fill"
n=0; for d in data/shards/*/; do cp data/solves/cache/hu_flop_library.json "$d"; n=$((n+1)); done
echo "index refreshed in $n shard roots ($(python3 -c "import json;print(sum(len(m.get('boards', {})) for m in json.load(open('data/solves/cache/hu_flop_library.json'))['matchups'].values()))") boards)"
REMOTE
}

status() {
  ssh "$host" bash -s <<'REMOTE'
cd "$HOME/pn-fill/data/shards" 2>/dev/null || { echo "no shards"; exit 0; }
echo "fills running: $(pgrep -fc 'pn library fill')   solvers: $(pgrep -fc console_solver)   load: $(cut -d' ' -f1-3 /proc/loadavg)"
for f in shard*.log; do
  printf '%-12s solved %4s  failed %3s  last: %s\n' "$f" "$(grep -c '^solved' "$f")" \
    "$(grep -c '^failed' "$f")" "$(tail -n1 "$f" | cut -c1-60)"
done
echo "total solved: $(cat shard*.log | grep -c '^solved')"
REMOTE
}

# [p] so the pattern does not match the remote shell running this very command (pkill would kill it)
stop() { ssh "$host" "pkill -f '[p]n library fill' || true; pkill -f '[c]onsole_solver' || true; echo stopped"; }

pull() {
  # only cache documents: <sha256>.json[.gz]/.toml; not lock, pid, index or log files. Kept as they come
  # (gz from Plan 17 code, plain from older boxes): readers take both; `pn cache compress` converts the plain
  rsync -az --exclude '.*' --exclude '*.tmp' --exclude '*.log' \
    --exclude hu_flop_library.json --exclude registry_names.json \
    "$host:$REMOTE_DIR/data/shards/*/" "$ROOT/data/solves/cache/"
  echo "pulled; run 'pn library fill ...' locally to index the new boards (recorded as cached)"
}

keys() {
  # the end-to-end key compare before a wave: the same dry-run here and on the box must print the
  # same sample matchup ids and flop keys (one per library; each already checked against the
  # trainer's lookup, "trainer lookup same"). A difference means the box would solve boards the
  # trainer never looks up (other freqs, charts, preset or code): stop and fix before `start`.
  shift 2 || true
  local args=("$@"); [[ ${#args[@]} -gt 0 ]] || args=("${DEFAULT_FILL_ARGS[@]}")
  local here there
  here=$(cd "$ROOT/pn" && COLUMNS=400 .venv/bin/pn library fill "${args[@]}" --dry-run --db ../data/none.sqlite | grep -E '^ +sample|matchups')
  there=$(ssh "$host" "cd \$HOME/$REMOTE_DIR/pn && COLUMNS=400 .venv/bin/pn library fill $(printf '%q ' "${args[@]}") --dry-run --db ../data/none.sqlite | grep -E '^ +sample|matchups'")
  echo "$here"
  if [[ "$here" == "$there" ]]; then echo "keys match"; else echo "KEYS DIFFER on the box:"; echo "$there"; exit 1; fi
}

case "$cmd" in
  push) push ;; bootstrap) bootstrap ;; start) start "$@" ;; resume) resume "$@" ;; refresh) refresh ;; status) status ;; stop) stop ;; pull) pull ;;
  keys) keys "$@" ;;
  *) echo "unknown command $cmd"; exit 1 ;;
esac
