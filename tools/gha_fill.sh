#!/usr/bin/env bash
# Sharded library fill on GitHub Actions (free 4-vCPU runners, 20 at a time, on a PUBLIC repo).
# The public repo holds code only: pn/, third_party/texassolver, the workflow.
# Private inputs (config/ charts + the solved cache) go up as an encrypted release asset `seed`;
# shards return encrypted artifacts; `pull` merges them into data/solves/cache (content-addressed,
# so the next local `pn library fill` records them as cached).
#
#   tools/gha_fill.sh export            build/refresh $PN_FILL_DIR from this repo (git commit there)
#   tools/gha_fill.sh create            point $PN_FILL_DIR at the (existing, public) repo, push, set the FILL_KEY secret
#   tools/gha_fill.sh seed              encrypt config + index + non-library docs -> release asset `seed` (after each pull + `pn library fill --cached-only`)
#   tools/gha_fill.sh start [total] [minutes] [fill args]   dispatch the workflow (defaults 60, 330; args: fill.yml's Plan 16 forward order)
#   tools/gha_fill.sh status            runs and per-job state
#   tools/gha_fill.sh pull [run-id]     download + decrypt every shard artifact into data/solves/cache
#   env: PN_FILL_REPO=<owner>/<repo> (the public mirror on GitHub), PN_FILL_DIR=<its local checkout>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ $# -ge 1 ]] || { sed -n '2,14p' "$0"; exit 1; }
PUB="${PN_FILL_DIR:?set PN_FILL_DIR (local checkout of the public fill mirror)}"
REPO="${PN_FILL_REPO:?set PN_FILL_REPO=<owner>/<repo> (the public fill mirror)}"
KEY_FILE="$HOME/.ssh/pn_fill_key"
SCRATCH="${TMPDIR:-/tmp}/gha_fill.$$"; trap 'rm -rf "$SCRATCH"' EXIT; mkdir -p "$SCRATCH"

key() {
  [[ -s "$KEY_FILE" ]] || { umask 077; openssl rand -hex 32 > "$KEY_FILE"; echo "new key in $KEY_FILE"; }
  FILL_KEY="$(cat "$KEY_FILE")"; export FILL_KEY
}

export_tree() {
  mkdir -p "$PUB"
  rsync -a --delete --exclude .venv --exclude build --exclude __pycache__ --exclude tests \
    --exclude '*.pyc' --exclude tmp_log.txt "$ROOT/pn/" "$PUB/pn/"
  rsync -a --delete --exclude build "$ROOT/third_party/texassolver/" "$PUB/third_party/texassolver/"
  mkdir -p "$PUB/.github/workflows"
  cp "$ROOT/tools/gha/fill.yml" "$PUB/.github/workflows/fill.yml"
  cat > "$PUB/README.md" <<'MD'
# pn fill mirror

Compute-only mirror of a poker study tool: the `pn` package and a vendored
[TexasSolver](https://github.com/bupticybee/TexasSolver) (AGPL-3.0, see `third_party/texassolver/LICENSE`).
Its one job is the `fill` workflow, which solves heads-up flop spots in shards on GitHub-hosted runners.
Inputs and results are encrypted; nothing here is meant to run on its own.
MD
  printf '.venv/\n__pycache__/\nbuild/\n*.pyc\ndata/\nconfig/\n*.enc\n' > "$PUB/.gitignore"
  if grep -rn --exclude-dir=.venv "@gmail" "$PUB/pn" "$PUB/README.md" >/dev/null; then echo "private id left in export"; exit 1; fi
  cd "$PUB"; [[ -d .git ]] || git init -q -b main
  git add -A; git commit -q -m "fill: sync from private repo $(git -C "$ROOT" rev-parse --short HEAD)" || echo "nothing to commit"
  echo "export at $PUB: $(git rev-parse --short HEAD)"
}

create() {
  key
  cd "$PUB"
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$REPO.git"
  git push -u origin main
  gh secret set FILL_KEY -R "$REPO" --body "$FILL_KEY"
  echo "pushed to https://github.com/$REPO, secret FILL_KEY set"
}

seed() {
  key
  # Library documents stay home: a shard skips an indexed board by the index alone ("finer" at the
  # pass accuracy), so the seed is config + the index + the documents the index does not list
  # (preflop solves etc.) plus the GTO Wizard frequencies (the preflop engine sizes raises from them,
  # so a shard without the file would key every 3-bet/4-bet spot differently), small as the library grows.
  (cd "$ROOT" && pn/.venv/bin/python tools/seed_list.py > "$SCRATCH/seed.txt" &&
   tar cz --exclude '.*' --exclude '*.tmp' --exclude '*.log' -T "$SCRATCH/seed.txt") \
    | openssl enc -aes-256-cbc -pbkdf2 -pass env:FILL_KEY -out "$SCRATCH/seed.tgz.enc"
  echo "seed: $(wc -l < "$SCRATCH/seed.txt" | tr -d ' ') entries, $(ls -lh "$SCRATCH/seed.tgz.enc" | awk '{print $5}')"
  gh release view seed -R "$REPO" >/dev/null 2>&1 || gh release create seed -R "$REPO" -t seed -n "encrypted fill inputs"
  gh release upload seed "$SCRATCH/seed.tgz.enc" -R "$REPO" --clobber
  echo "seeded the index ($(python3 -c 'import json,sys;print(sum(len(m.get("boards", {})) for m in json.load(open(sys.argv[1]))["matchups"].values()))' "$ROOT/data/solves/cache/hu_flop_library.json") boards) + non-library documents"
}

start() {
  local total="${1:-60}" minutes="${2:-330}"
  [[ $# -ge 2 ]] && shift 2 || shift $#
  if [[ $# -gt 0 ]]; then
    gh workflow run fill.yml -R "$REPO" -f total="$total" -f minutes="$minutes" -f args="$*"
  else
    gh workflow run fill.yml -R "$REPO" -f total="$total" -f minutes="$minutes"
  fi
  echo "dispatched: $total shards, $minutes min each"
}

status() {
  gh run list -R "$REPO" --workflow fill.yml -L 3
  local id; id="$(gh run list -R "$REPO" --workflow fill.yml -L 1 --json databaseId -q '.[0].databaseId')"
  [[ -n "$id" ]] || return 0
  gh run view "$id" -R "$REPO" --json jobs -q '.jobs[] | select(.name | startswith("fill")) | .conclusion // .status' \
    | sort | uniq -c
}

doc_keys() {
  # documents in the cache, one per key (a key present as both .json and .json.gz counts once); not ls: 10k+ files
  find "$ROOT/data/solves/cache" -maxdepth 1 \( -name '*.json' -o -name '*.json.gz' \) | sed -E 's/[.]json([.]gz)?$//' | sort -u | wc -l | tr -d ' '
}

pull() {
  key
  local id="${1:-$(gh run list -R "$REPO" --workflow fill.yml -L 1 --json databaseId -q '.[0].databaseId')}"
  gh run download "$id" -R "$REPO" -D "$SCRATCH/art"
  local before; before=$(doc_keys)
  for f in "$SCRATCH"/art/*/shard-*.tgz.enc; do
    openssl enc -d -aes-256-cbc -pbkdf2 -pass env:FILL_KEY -in "$f" | tar xz -C "$ROOT/data/solves/cache"
  done
  echo "pulled run $id: documents $before -> $(doc_keys)"
  echo "next: pn library fill <the run's args> --cached-only  (index them; Plan 16: --configs 0 --subset 184 --lib 8x100 --lib 6x200 --lib 8x200 --new-only --accuracy 1 --depth-accuracy 200=2), then tools/gha_fill.sh seed"
}

case "${1:-}" in
  export) export_tree ;; create) create ;; seed) seed ;;
  start) shift; start "$@" ;; status) status ;; pull) shift; pull "$@" ;;
  *) sed -n '2,14p' "$0"; exit 1 ;;
esac
