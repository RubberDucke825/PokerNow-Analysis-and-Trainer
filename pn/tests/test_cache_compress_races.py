"""Plan 17 review: `pn cache compress` racing other writers and readers. Each writer has its own
temp file, compress holds the solve lock per file and re-checks the plain file before it swaps,
a per-file OSError never aborts the run, and readers survive a swap (or a corrupt gz next to a
good plain copy)."""

from __future__ import annotations

import errno
import gzip
import json
import os
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pn.cli.main import app
from pn.solve import SolveCache, docstore
from pn.solve.lock import file_lock

K1, K2 = "a" * 64, "b" * 64
OLD = time.time() - 3600
STALE, FRESH = {"meta": {"backend": "stale"}}, {"meta": {"backend": "texas", "fresh": True}}


def _plain(root: Path, key: str, doc: object, age: float = OLD) -> Path:
    p = root / f"{key}.json"
    p.write_text(json.dumps(doc))
    os.utime(p, (age, age))
    return p


def _during_gzip(monkeypatch: pytest.MonkeyPatch, action) -> None:
    """Run `action` once, inside compress's window between reading the plain file and the swap."""
    real = gzip.compress

    def hook(data: bytes, *a: object, **kw: object) -> bytes:
        monkeypatch.setattr(docstore.gzip, "compress", real)
        action()
        return real(data, *a, **kw)

    monkeypatch.setattr(docstore.gzip, "compress", hook)


# ------------------------------------------------------------------ writers


def test_gz_dump_during_compress_keeps_the_fresh_document(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    _plain(tmp_path, K1, STALE)
    _plain(tmp_path, K2, STALE)
    _during_gzip(monkeypatch, lambda: docstore.dump(tmp_path, K1, FRESH))  # a re-solve lands
    rep = docstore.compress(tmp_path, min_age_sec=60)
    assert rep.vanished == 1 and rep.converted == 1 and rep.failed == 0
    assert docstore.load(tmp_path / f"{K1}.json.gz") == FRESH
    assert not (tmp_path / f"{K1}.json").exists() and not list(tmp_path.glob("*.tmp"))
    assert docstore.load(tmp_path / f"{K2}.json.gz") == STALE


def test_plain_dump_during_compress_is_not_overwritten(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    _plain(tmp_path, K1, STALE)

    def resolve_plain() -> None:
        monkeypatch.setenv("PN_CACHE_GZIP", "0")
        docstore.dump(tmp_path, K1, FRESH)
        monkeypatch.delenv("PN_CACHE_GZIP")

    _during_gzip(monkeypatch, resolve_plain)
    rep = docstore.compress(tmp_path, min_age_sec=60)
    assert rep.converted == 0 and rep.recent == 1 and rep.failed == 0
    assert docstore.doc_files(tmp_path) == {K1: tmp_path / f"{K1}.json"}
    assert docstore.load(tmp_path / f"{K1}.json") == FRESH and not list(tmp_path.glob("*.tmp"))
    assert docstore.compress(tmp_path, min_age_sec=0).converted == 1
    assert docstore.load(tmp_path / f"{K1}.json.gz") == FRESH


def test_writers_never_share_a_temp_file(tmp_path: Path) -> None:
    """Another writer's temp file (the old fixed `<key>.json.gz.tmp` name) is left alone by dump
    and by compress while it is fresh."""
    _plain(tmp_path, K1, STALE)
    other = tmp_path / f"{K1}.json.gz.tmp"
    other.write_bytes(b"another writer's bytes")
    docstore.compress(tmp_path, min_age_sec=60)
    docstore.dump(tmp_path, K2, FRESH)
    assert other.read_bytes() == b"another writer's bytes"
    assert docstore.load(tmp_path / f"{K1}.json.gz") == STALE
    assert docstore.load(tmp_path / f"{K2}.json.gz") == FRESH


def test_compress_sweeps_old_temp_files_of_either_naming(tmp_path: Path) -> None:
    names = [f"{K1}.json.gz.tmp", f"{K1}.json.gz.x1y2z3.tmp", f"{K2}.json.k9.tmp"]
    for n in names:
        (tmp_path / n).write_bytes(b"x")
        os.utime(tmp_path / n, (OLD, OLD))
    (tmp_path / f"{K2}.toml.tmp").write_bytes(b"x")  # not a document temp file
    os.utime(tmp_path / f"{K2}.toml.tmp", (OLD, OLD))
    docstore.compress(tmp_path)
    left = sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp"))
    assert left == [f"{K2}.toml.tmp"]


def test_compress_waits_for_the_solve_lock(tmp_path: Path) -> None:
    _plain(tmp_path, K1, STALE)
    done = threading.Event()
    with file_lock(tmp_path):  # a solve is writing
        t = threading.Thread(target=lambda: (docstore.compress(tmp_path), done.set()))
        t.start()
        time.sleep(0.4)
        assert not done.is_set() and (tmp_path / f"{K1}.json").exists()
    t.join(10)
    assert done.is_set() and docstore.doc_files(tmp_path) == {K1: tmp_path / f"{K1}.json.gz"}


# ------------------------------------------------------------------ per-file errors


def test_a_plain_file_vanishing_after_the_read_is_skipped(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    _plain(tmp_path, K1, STALE)
    _plain(tmp_path, K2, STALE)
    _during_gzip(monkeypatch, lambda: docstore.remove(tmp_path, K1))  # pn's delete, or an rsync
    rep = docstore.compress(tmp_path)
    assert (rep.vanished, rep.converted, rep.failed) == (1, 1, 0)
    assert docstore.doc_files(tmp_path) == {K2: tmp_path / f"{K2}.json.gz"}
    assert not list(tmp_path.glob("*.tmp"))


def test_an_os_error_fails_that_file_only_and_disk_full_stops(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    for k in (K1, K2, "c" * 64):
        _plain(tmp_path, k, STALE)
    real = os.fsync
    calls = {"n": 0, "err": errno.EACCES}

    def flaky(fd: int) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(calls["err"], os.strerror(calls["err"]))
        real(fd)

    monkeypatch.setattr(docstore.os, "fsync", flaky)
    rep = docstore.compress(tmp_path)
    assert (rep.failed, rep.converted, rep.stopped) == (1, 2, False)
    assert (tmp_path / f"{K1}.json").exists() and not list(tmp_path.glob("*.tmp"))
    calls.update(n=0, err=errno.ENOSPC)
    rep = docstore.compress(tmp_path)
    assert (rep.failed, rep.converted, rep.stopped) == (1, 0, True)
    assert not list(tmp_path.glob("*.tmp"))
    monkeypatch.setenv("PN_SOLVE_CACHE", str(tmp_path))
    calls.update(n=0)
    r = CliRunner().invoke(app, ["cache", "compress"])
    assert r.exit_code == 1 and "disk full" in r.output


# ------------------------------------------------------------------ readers


def _swap_on_first_load(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """The first docstore.load converts the directory first (compress lands between the reader's
    doc_path and its open), so the plain path it was handed is gone."""
    real = docstore.load

    def load(path: str | Path) -> object:
        monkeypatch.setattr(docstore, "load", real)
        docstore.compress(root, min_age_sec=0)
        return real(path)

    monkeypatch.setattr(docstore, "load", load)


def test_read_retries_a_swap_and_falls_back_from_a_corrupt_gz(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    _plain(tmp_path, K1, STALE)
    _swap_on_first_load(monkeypatch, tmp_path)
    assert docstore.read(tmp_path, K1) == STALE
    assert docstore.read(tmp_path, K2) is None
    _plain(tmp_path, K2, FRESH)
    (tmp_path / f"{K2}.json.gz").write_bytes(b"E\xa5 not gzip")  # torn gz, good plain copy
    assert docstore.read(tmp_path, K2) == FRESH and SolveCache(tmp_path).get(K2) == FRESH
    (tmp_path / f"{K2}.json").unlink()
    with pytest.raises(OSError):
        SolveCache(tmp_path).get(K2)  # nothing good to fall back to: the error stays visible


def test_leaks_entry_converted_mid_read_is_still_counted(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    from fake_tree import fake_runner
    from test_solve_docstore import _spot

    from pn.train import leaks

    monkeypatch.setenv("PN_CACHE_GZIP", "0")
    SolveCache(tmp_path, runner=fake_runner).solve(_spot("Kd7s2c"))
    (toml,) = tmp_path.glob("*.toml")
    os.utime(tmp_path / f"{toml.stem}.json", (OLD, OLD))
    want = leaks.doc_stats(toml)
    assert want is not None
    _swap_on_first_load(monkeypatch, tmp_path)
    assert leaks.doc_stats(toml) == want and (tmp_path / f"{toml.stem}.json.gz").exists()


def test_registry_rereads_a_document_whose_first_read_lost_the_swap(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    from pn.web import solves as ws

    cache = tmp_path / "cache"
    cache.mkdir()
    _plain(cache, K1, FRESH)
    reg = ws.Registry(tmp_path / "solves", SolveCache(cache))
    monkeypatch.setattr(ws, "stale_doc", lambda d: d["meta"]["backend"] != "texas")
    monkeypatch.setattr(ws, "with_board", lambda d, board: d)
    reg._generated["g1"] = STALE
    reg._info["g1"] = {"key": K1}
    _swap_on_first_load(monkeypatch, cache)
    reg._fresh("g1")  # its read may lose the race (the plain file it opened is gone)
    assert reg._fresh("g1") == FRESH  # the gz kept the plain file's mtime: re-read anyway
