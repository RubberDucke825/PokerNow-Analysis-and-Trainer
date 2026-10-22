"""Pack, unpack and sample the trainer's heads-up flop library (a SolveCache directory: the index
hu_flop_library.json plus <key>.json.gz / <key>.json documents and their <key>.toml requests).

    uv run --project pn python tools/library_pack.py pack <cache_dir> <out_dir> [--part-bytes N]
    uv run --project pn python tools/library_pack.py unpack <parts_dir | archive.tar[.gz]> <cache_dir>
    uv run --project pn python tools/library_pack.py sample <cache_dir> <out.tar.gz> \
        --table 6 --depth 100 --line srp --seats BTN,BB --boards 60

pack: plain tar volumes library-NNN.tar, each under --part-bytes (default 1.9 GB: GitHub release
assets cap at 2 GB), the index in the first one, then documents in key order, streamed file by
file. MANIFEST.json (parts with size and sha256, document / board / matchup counts from the
index, pn version and git commit) and SHA256SUMS sit next to them.

unpack: a directory is verified against its MANIFEST.json (sha256 of every part) first; a single
archive (the committed sample) is taken as is. Only flat document / request / index names are
extracted. An existing document or request at least as new as the archived one is kept. The
archived index is merged into the existing one (boards already indexed win), then re-keyed to
the current matchup ids exactly as `pn library reindex` does, for every table / depth it holds.

sample: the first N boards (index order) of one matchup, the one the trainer looks up for that
preflop line (pn.train.flop_library.matchup_of), with a trimmed index. The first board's key is
re-derived from the request to prove the trainer would find it.

pack is stdlib only; unpack's reindex and sample import pn (run them with the project env).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

INDEX_NAME = "hu_flop_library.json"
MANIFEST_NAME = "MANIFEST.json"
SUMS_NAME = "SHA256SUMS"
PART_BYTES = 1_900_000_000
PART_FMT = "library-{:03d}.tar"
BLOCK = 512
TAR_TAIL = 2 * BLOCK + 10 * 1024  # end-of-archive blocks + worst-case record padding
CHUNK = 1 << 20
DOC_RE = re.compile(r"([0-9a-f]{64})\.(json\.gz|json|toml)")
LINE_STEPS = {  # --line -> preflop steps of --seats (opener, caller); everyone else folds
    "srp": lambda o, c: [(o, "raise"), (c, "call")],
    "3bp": lambda o, c: [(o, "raise"), (c, "raise"), (o, "call")],
    "4bp": lambda o, c: [(o, "raise"), (c, "raise"), (o, "raise"), (c, "call")],
}


# ------------------------------------------------------------------ helpers


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def library_files(root: Path) -> list[Path]:
    """Documents (gz preferred over a plain copy) and requests under `root`, in key order."""
    by_key: dict[str, dict[str, Path]] = {}
    for e in os.scandir(root):
        m = DOC_RE.fullmatch(e.name)
        if m and e.is_file():
            by_key.setdefault(m.group(1), {})[m.group(2)] = Path(e.path)
    out: list[Path] = []
    for key in sorted(by_key):
        forms = by_key[key]
        doc = forms.get("json.gz") or forms.get("json")
        out += [p for p in (doc, forms.get("toml")) if p is not None]
    return out


def member_bytes(size: int) -> int:
    """Bytes a regular file of `size` takes in a ustar archive (names here are under 100 bytes)."""
    return BLOCK + -(-size // BLOCK) * BLOCK


def index_counts(index: dict) -> dict[str, int]:
    """Matchups (aliases apart) and boards of an index."""
    ms = index.get("matchups", {})
    return {"matchups": sum("alias_of" not in e for e in ms.values()),
            "aliases": sum("alias_of" in e for e in ms.values()),
            "boards": sum(len(e.get("boards", {})) for e in ms.values())}  # fmt: skip


def _pn_version() -> dict[str, str | None]:
    repo = Path(__file__).resolve().parent.parent
    version = None
    with suppress(OSError):
        m = re.search(r'^version = "([^"]+)"', (repo / "pn" / "pyproject.toml").read_text(), re.MULTILINE)
        version = m.group(1) if m else None
    commit = None
    with suppress(OSError, subprocess.CalledProcessError):
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                                capture_output=True, text=True).stdout.strip() or None  # fmt: skip
    return {"pn_version": version, "commit": commit}


# ------------------------------------------------------------------ pack


def plan_parts(files: list[Path], part_bytes: int) -> list[list[Path]]:
    """Files split greedily, in order, into volumes whose tar size stays under `part_bytes`."""
    budget = part_bytes - TAR_TAIL
    parts: list[list[Path]] = [[]]
    used = 0
    for p in files:
        need = member_bytes(p.stat().st_size)
        if need > budget:
            raise ValueError(f"{p.name} ({need} bytes) does not fit a {part_bytes}-byte part")
        if used + need > budget and parts[-1]:
            parts.append([])
            used = 0
        parts[-1].append(p)
        used += need
    return parts


def pack(cache_dir: Path, out_dir: Path, part_bytes: int = PART_BYTES) -> dict:
    """Write the volumes, MANIFEST.json and SHA256SUMS into `out_dir`; the manifest."""
    index_path = cache_dir / INDEX_NAME
    if not index_path.is_file():
        raise ValueError(f"no {INDEX_NAME} in {cache_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    docs = library_files(cache_dir)
    with open(index_path) as f:
        counts = index_counts(json.load(f))
    parts = []
    for n, files in enumerate(plan_parts([index_path, *docs], part_bytes)):
        name = PART_FMT.format(n)
        dest = out_dir / name
        with tarfile.open(dest, "w", format=tarfile.USTAR_FORMAT) as tar:
            for p in files:
                tar.add(p, arcname=p.name, recursive=False)
        size = dest.stat().st_size
        if size >= part_bytes:
            raise RuntimeError(f"{name} is {size} bytes, over the {part_bytes} limit")
        parts.append({"name": name, "bytes": size, "sha256": sha256_file(dest), "files": len(files)})
        print(f"{name}: {len(files)} files, {size} bytes", file=sys.stderr)
    manifest = {
        "format": 1, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "documents": sum(DOC_RE.fullmatch(p.name).group(2) != "toml" for p in docs),  # type: ignore[union-attr]
        **counts, **_pn_version(), "part_bytes": part_bytes, "parts": parts,
    }  # fmt: skip
    (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1) + "\n")
    (out_dir / SUMS_NAME).write_text("".join(f"{p['sha256']}  {p['name']}\n" for p in parts))
    return manifest


# ------------------------------------------------------------------ unpack


def verified_parts(parts_dir: Path) -> list[Path]:
    """The manifest's parts in order, each checked for size and sha256 (ValueError otherwise)."""
    manifest = json.loads((parts_dir / MANIFEST_NAME).read_text())
    out = []
    for part in manifest["parts"]:
        name = part["name"]
        if Path(name).name != name:
            raise ValueError(f"bad part name {name!r}")
        p = parts_dir / name
        if not p.is_file() or p.stat().st_size != part["bytes"]:
            raise ValueError(f"{name}: missing or wrong size")
        if sha256_file(p) != part["sha256"]:
            raise ValueError(f"{name}: sha256 mismatch")
        out.append(p)
    return out


def _members(archive: Path) -> Iterator[tuple[tarfile.TarFile, tarfile.TarInfo]]:
    with tarfile.open(archive, "r:*") as tar:
        for m in tar:
            yield tar, m


def _newest(cache_dir: Path, name: str) -> float | None:
    """mtime of the existing file `name` stands for: either document form for a document."""
    m = DOC_RE.fullmatch(name)
    names = [name] if m is None or m.group(2) == "toml" else [f"{m.group(1)}.json.gz", f"{m.group(1)}.json"]
    times = [(cache_dir / n).stat().st_mtime for n in names if (cache_dir / n).exists()]
    return max(times) if times else None


def _write_member(cache_dir: Path, name: str, data: io.BufferedReader, mtime: float) -> None:
    fd, tmp = tempfile.mkstemp(dir=cache_dir, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            while chunk := data.read(CHUNK):
                f.write(chunk)
        os.chmod(tmp, 0o644)
        os.utime(tmp, (mtime, mtime))
        os.replace(tmp, cache_dir / name)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    m = DOC_RE.fullmatch(name)
    if m and m.group(2) != "toml":  # drop the other (older) form, as pn.solve.docstore.dump does
        other = f"{m.group(1)}.json" if m.group(2) == "json.gz" else f"{m.group(1)}.json.gz"
        with suppress(FileNotFoundError):
            os.unlink(cache_dir / other)


def merge_index(cache_dir: Path, incoming: dict) -> int:
    """Merge `incoming` into `<cache_dir>/hu_flop_library.json`; boards already indexed (and
    whole alias entries) win. The number of boards added."""
    path = cache_dir / INDEX_NAME
    data = json.loads(path.read_text()) if path.exists() else {"matchups": {}}
    added = 0
    for mid, entry in incoming.get("matchups", {}).items():
        cur = data["matchups"].get(mid)
        if cur is None:
            data["matchups"][mid] = entry
            added += len(entry.get("boards", {}))
            continue
        if "alias_of" in cur or "alias_of" in entry:
            continue
        for board, b in entry.get("boards", {}).items():
            if board not in cur.setdefault("boards", {}):
                cur["boards"][board] = b
                added += 1
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(path)
    return added


def reindex(cache_dir: Path) -> int:
    """`pn library reindex` for every table / depth the index holds; entries moved."""
    from pn.solve.request import TABLE_SEATS
    from pn.train import flop_library as flib

    data = json.loads((cache_dir / INDEX_NAME).read_text())
    libs = {(int(e.get("table", "6max").removesuffix("max")), int(e["depth"]))
            for e in data["matchups"].values() if "depth" in e}  # fmt: skip
    return sum(flib.reindex(cache_dir, t, d) for t, d in sorted(libs) if t in TABLE_SEATS)


def unpack(src: Path, cache_dir: Path, *, do_reindex: bool = True) -> dict[str, int]:
    """Extract a verified parts directory or one archive into `cache_dir` (module doc)."""
    if do_reindex:
        from pn.train import library_fill as lf

        if (pid := lf.running_fill(cache_dir)) is not None:
            raise RuntimeError(f"a library fill is running on {cache_dir} (pid {pid}): its final "
                               "index write would undo this one; rerun when it ends")  # fmt: skip
    archives = verified_parts(src) if src.is_dir() else [src]
    cache_dir.mkdir(parents=True, exist_ok=True)
    stats = {"written": 0, "kept": 0, "skipped": 0, "boards_added": 0, "rekeyed": 0}
    for archive in archives:
        for tar, m in _members(archive):
            name = m.name
            ok = m.isfile() and Path(name).name == name and (name == INDEX_NAME or DOC_RE.fullmatch(name))
            if not ok:
                print(f"skipped {name!r}: not a library file", file=sys.stderr)
                stats["skipped"] += 1
                continue
            data = tar.extractfile(m)
            assert data is not None
            if name == INDEX_NAME:
                stats["boards_added"] += merge_index(cache_dir, json.load(data))
                continue
            have = _newest(cache_dir, name)
            if have is not None and have >= m.mtime:
                stats["kept"] += 1
                continue
            _write_member(cache_dir, name, data, m.mtime)  # type: ignore[arg-type]
            stats["written"] += 1
    if do_reindex and (cache_dir / INDEX_NAME).exists():
        stats["rekeyed"] = reindex(cache_dir)
    return stats


# ------------------------------------------------------------------ sample


def sample_matchup(table: int, depth: int, line: str, seats: list[str]):
    """The trainer's Matchup for `line` between `seats` (opener first), everyone else folding."""
    from pn.solve.request import TABLE_SEATS
    from pn.train import flop_library as flib

    if line not in LINE_STEPS or len(seats) != 2 or table not in TABLE_SEATS:
        raise ValueError(f"want --line {'|'.join(LINE_STEPS)}, two --seats, --table {sorted(TABLE_SEATS)}")
    order = TABLE_SEATS[table]
    st = flib.replay(order, depth, LINE_STEPS[line](*seats), sb=50, bb=100, fold_others=True)
    m = flib.matchup_of(st, f"{table}max", depth) if st is not None else None
    if m is None:
        raise ValueError(f"{line} {','.join(seats)} does not reach a heads-up flop at {table}-max {depth}bb")
    return m


def sample(cache_dir: Path, out: Path, *, table: int, depth: int, line: str, seats: list[str],
           boards: int) -> dict:  # fmt: skip
    """Write the first `boards` boards of the matchup with a trimmed index to `out` (.tar.gz)."""
    from pn.solve import flops as fl
    from pn.train import flop_library as flib

    m = sample_matchup(table, depth, line, seats)
    index = json.loads((cache_dir / INDEX_NAME).read_text())
    entry = index["matchups"].get(m.mid)
    if entry is None:
        raise ValueError(f"{m.label} ({m.mid}) is not in {cache_dir / INDEX_NAME}")
    trimmed: dict[str, dict] = {}
    src_mid = m.mid
    if "alias_of" in entry:
        trimmed[m.mid] = entry
        src_mid = entry["alias_of"]
        entry = index["matchups"][src_mid]
    picked = dict(list(entry.get("boards", {}).items())[:boards])
    if not picked:
        raise ValueError(f"{m.label}: no solved boards")
    trimmed[src_mid] = {k: v for k, v in entry.items() if k not in ("flops", "subset")} | {"boards": picked}
    board, first = next(iter(picked.items()))
    key = flib.flop_spot(m, tuple(fl.flop_cards(board)), first.get("accuracy"))[2]
    if key != first["key"]:
        raise ValueError(f"{m.label} {board}: indexed key {first['key'][:12]} != trainer key {key[:12]}")
    files: list[Path] = []
    for b in picked.values():
        doc = next((cache_dir / f"{b['key']}{s}" for s in (".json.gz", ".json")
                    if (cache_dir / f"{b['key']}{s}").exists()), None)  # fmt: skip
        if doc is None:
            raise ValueError(f"document {b['key']} missing from {cache_dir}")
        files.append(doc)
        if (toml := cache_dir / f"{b['key']}.toml").exists():
            files.append(toml)
    blob = json.dumps({"matchups": trimmed}, indent=1, sort_keys=True).encode()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz", format=tarfile.USTAR_FORMAT) as tar:
        info = tarfile.TarInfo(INDEX_NAME)
        info.size, info.mtime, info.mode = len(blob), int(time.time()), 0o644
        tar.addfile(info, io.BytesIO(blob))
        for p in files:
            tar.add(p, arcname=p.name, recursive=False)
    return {"matchup": m.mid, "label": m.label, "boards": len(picked), "bytes": out.stat().st_size}


# ------------------------------------------------------------------ CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack", help="cache -> tar volumes + MANIFEST.json + SHA256SUMS")
    p.add_argument("cache_dir", type=Path)
    p.add_argument("out_dir", type=Path)
    p.add_argument("--part-bytes", type=int, default=PART_BYTES)
    p = sub.add_parser("unpack", help="verified volumes (or one archive) -> cache, then reindex")
    p.add_argument("src", type=Path, help="directory with MANIFEST.json, or a .tar / .tar.gz")
    p.add_argument("cache_dir", type=Path)
    p.add_argument("--no-reindex", action="store_true", help="skip `pn library reindex`")
    p = sub.add_parser("sample", help="first N boards of one matchup -> .tar.gz")
    p.add_argument("cache_dir", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--table", type=int, default=6)
    p.add_argument("--depth", type=int, default=100)
    p.add_argument("--line", default="srp", choices=sorted(LINE_STEPS))
    p.add_argument("--seats", default="BTN,BB", help="opener,caller")
    p.add_argument("--boards", type=int, default=60)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "pack":
            res = pack(a.cache_dir, a.out_dir, a.part_bytes)
            res = {k: v for k, v in res.items() if k != "parts"} | {"parts": len(res["parts"])}
        elif a.cmd == "unpack":
            res = unpack(a.src, a.cache_dir, do_reindex=not a.no_reindex)
        else:
            res = sample(a.cache_dir, a.out, table=a.table, depth=a.depth, line=a.line,
                         seats=[s.strip().upper() for s in a.seats.split(",")], boards=a.boards)  # fmt: skip
    except (OSError, ValueError, RuntimeError, tarfile.TarError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
