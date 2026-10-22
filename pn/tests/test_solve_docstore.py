"""Plan 17 C1/C2: SolveCache documents on disk as <key>.json.gz (written by default) or plain
<key>.json (older entries, pulls); readers take either, gz first. `pn cache compress` converts
plain documents in place and is crash-safe: at any instant each key has a complete plain file,
a verified .json.gz, or both."""

from __future__ import annotations

import gzip
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pn.cli.main import app
from pn.solve import SolveCache, build_spot, docstore, spot_key
from pn.solve.request import new_request, parse_board

K1, K2, K3 = "a" * 64, "b" * 64, "c" * 64
OLD = time.time() - 3600


def _spot(board: str = "Kd2cKh") -> dict:
    return build_spot(new_request(["BTN", "BB"], 100, board=parse_board(board), line="srp", hero="BTN"))


def _runner(spot: dict, backend: str | None) -> tuple[dict, str]:
    return {"meta": {"backend": "texas"}, "nodes": {}, "board": spot["game"]["board"]}, "texas"


def _plain(root: Path, key: str, doc: object, age: float = OLD) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / f"{key}.json"
    p.write_text(json.dumps(doc))
    os.utime(p, (age, age))
    return p


def _gz(root: Path, key: str, doc: object, age: float = OLD) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / f"{key}.json.gz"
    p.write_bytes(gzip.compress(json.dumps(doc).encode()))
    os.utime(p, (age, age))
    return p


# ------------------------------------------------------------------ read / write paths


def test_reader_takes_plain_or_gz_and_prefers_gz(tmp_path: Path) -> None:
    _plain(tmp_path, K1, {"v": "plain"})
    _gz(tmp_path, K2, {"v": "gz"})
    _plain(tmp_path, K3, {"v": "plain"})
    _gz(tmp_path, K3, {"v": "gz"})
    cache = SolveCache(tmp_path, runner=_runner)
    assert cache.get(K1) == {"v": "plain"} and cache.get(K2) == {"v": "gz"}
    assert cache.get(K3) == {"v": "gz"} and cache.path(K3).name == f"{K3}.json.gz"
    assert docstore.doc_path(tmp_path, "d" * 64) is None
    assert docstore.doc_files(tmp_path) == {K1: tmp_path / f"{K1}.json", K2: tmp_path / f"{K2}.json.gz",
                                            K3: tmp_path / f"{K3}.json.gz"}  # fmt: skip


def test_doc_files_skip_index_lazydoc_and_tmp_files(tmp_path: Path) -> None:
    _plain(tmp_path, K1, {})
    for name in ("hu_flop_library.json", "registry_names.json", f"{K2}.json.index.json",
                 f"{K2}.json.gz.tmp", f"{K2}.json.tmp", f"{K2}.toml", "6max_100bb_gto.json"):
        (tmp_path / name).write_text("{}")
    assert list(docstore.doc_files(tmp_path)) == [K1]


def test_solve_writes_gz_by_default_and_plain_behind_the_switch(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    spot = _spot()
    key = spot_key(spot)
    SolveCache(tmp_path / "a", runner=_runner).solve(spot)
    assert (tmp_path / "a" / f"{key}.json.gz").exists() and not (tmp_path / "a" / f"{key}.json").exists()
    assert (tmp_path / "a" / f"{key}.toml").exists()  # the request stays plain
    assert json.loads(gzip.decompress((tmp_path / "a" / f"{key}.json.gz").read_bytes()))["board"] == ["Kd", "2c", "Kh"]
    monkeypatch.setenv("PN_CACHE_GZIP", "0")
    _gz(tmp_path / "b", key, {"meta": {"backend": "old"}})  # a re-solve in plain must not leave the gz to shadow it
    cache = SolveCache(tmp_path / "b", runner=_runner)
    monkeypatch.setattr("pn.solve.cache.is_stale", lambda s, b, d: d["meta"]["backend"] == "old")
    cache.solve(spot)
    assert (tmp_path / "b" / f"{key}.json").exists() and not (tmp_path / "b" / f"{key}.json.gz").exists()
    assert SolveCache(tmp_path / "b").get(key)["meta"]["backend"] == "texas"


def test_gz_write_removes_an_older_plain_copy(tmp_path: Path) -> None:
    _plain(tmp_path, K1, {"v": 1})
    docstore.dump(tmp_path, K1, {"v": 2})
    assert not (tmp_path / f"{K1}.json").exists() and docstore.load(tmp_path / f"{K1}.json.gz") == {"v": 2}


def test_read_meta_from_a_gz_head(tmp_path: Path) -> None:
    from pn.web.study import read_meta

    p = _gz(tmp_path, K1, {"meta": {"backend": "texas", "iterations": 7}, "nodes": {}})
    assert read_meta(p) == {"backend": "texas", "iterations": 7}


# ------------------------------------------------------------------ compress


def _docs(root: Path, n: int, size: int = 50) -> dict[str, dict]:
    docs = {}
    for i in range(n):
        key = f"{i:064x}"
        docs[key] = {"meta": {"i": i}, "nodes": {str(j): [j * 0.125] * 8 for j in range(size)}}
        _plain(root, key, docs[key])
        (root / f"{key}.toml").write_text(f"# {key}\n")
    return docs


def _assert_complete(root: Path, docs: dict[str, dict]) -> None:
    for key, doc in docs.items():
        forms = [p for p in (root / f"{key}.json", root / f"{key}.json.gz") if p.exists()]
        assert forms, key
        for p in forms:
            assert docstore.load(p) == doc, p


def test_compress_converts_verifies_and_is_idempotent(tmp_path: Path) -> None:
    docs = _docs(tmp_path, 5)
    (tmp_path / "hu_flop_library.json").write_text('{"matchups": {}}')
    dry = docstore.compress(tmp_path, dry_run=True)
    assert dry.converted == 5 and dry.after_bytes == 0 and len(list(tmp_path.glob("*.json"))) == 6
    rep = docstore.compress(tmp_path)
    assert rep.converted == 5 and rep.failed == 0 and 0 < rep.after_bytes < rep.before_bytes
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["hu_flop_library.json"]
    assert len(list(tmp_path.glob("*.toml"))) == 5
    _assert_complete(tmp_path, docs)
    assert docstore.compress(tmp_path).converted == 0


def test_compress_limit_recent_files_and_bad_json(tmp_path: Path) -> None:
    _docs(tmp_path, 4)
    os.utime(tmp_path / f"{0:064x}.json")  # touched now: a pull may still be writing it
    (tmp_path / ("e" * 64 + ".json")).write_text('{"cut')
    os.utime(tmp_path / ("e" * 64 + ".json"), (OLD, OLD))
    rep = docstore.compress(tmp_path, limit=2)
    assert rep.converted == 2 and rep.recent == 1
    rep = docstore.compress(tmp_path)
    assert rep.converted == 1 and rep.failed == 1 and rep.recent == 1
    assert (tmp_path / ("e" * 64 + ".json")).exists()  # an unreadable document is left as it is
    assert docstore.compress(tmp_path, min_age_sec=0).converted == 1


def test_compress_resolves_both_forms_by_mtime(tmp_path: Path) -> None:
    _gz(tmp_path, K1, {"v": "gz"}, age=OLD)
    _plain(tmp_path, K1, {"v": "newer plain"}, age=OLD + 60)  # pulled after the conversion
    _plain(tmp_path, K2, {"v": "old plain"}, age=OLD)
    _gz(tmp_path, K2, {"v": "gz"}, age=OLD + 60)  # a crash after the rename, before the unlink
    docstore.compress(tmp_path)
    assert docstore.doc_files(tmp_path) == {K1: tmp_path / f"{K1}.json.gz", K2: tmp_path / f"{K2}.json.gz"}
    assert docstore.load(tmp_path / f"{K1}.json.gz") == {"v": "newer plain"}
    assert docstore.load(tmp_path / f"{K2}.json.gz") == {"v": "gz"}


@pytest.mark.parametrize("crash_at", ["replace", "unlink"])
def test_compress_crash_between_steps_leaves_complete_files(tmp_path: Path, crash_at: str,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    docs = _docs(tmp_path, 3)
    real = getattr(os, crash_at)
    calls = {"n": 0}

    def boom(*a: object, **kw: object) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt  # the process dies here
        real(*a, **kw)

    monkeypatch.setattr(docstore.os, crash_at, boom)
    with pytest.raises(KeyboardInterrupt):
        docstore.compress(tmp_path)
    monkeypatch.setattr(docstore.os, crash_at, real)
    _assert_complete(tmp_path, docs)
    docstore.compress(tmp_path)
    assert all(p.suffix == ".gz" for p in docstore.doc_files(tmp_path).values())
    assert not list(tmp_path.glob("*.tmp")) and not list(tmp_path.glob("*.json"))
    _assert_complete(tmp_path, docs)


def test_compress_survives_kill_9(tmp_path: Path) -> None:
    """A real SIGKILL mid-run: every key is still complete (plain, verified gz or both), and a
    second run finishes the job."""
    root = tmp_path / "cache"
    docs = _docs(root, 200, size=400)
    code = ("import sys; from pn.solve import docstore; "
            "docstore.compress(sys.argv[1], level=9)")  # level 9: slow enough to kill mid-run
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    proc = subprocess.Popen([sys.executable, "-c", code, str(root)], env=env)
    deadline = time.time() + 30
    while time.time() < deadline and len(list(root.glob("*.json.gz"))) < 20:
        time.sleep(0.005)
    proc.send_signal(signal.SIGKILL)
    proc.wait()
    left = len(list(root.glob("*.json")))
    assert 0 < left < 200, "kill landed before or after the run: make the docs bigger"
    _assert_complete(root, docs)
    docstore.compress(root)
    assert not list(root.glob("*.json")) and len(list(root.glob("*.json.gz"))) == 200
    _assert_complete(root, docs)


def test_cli_cache_compress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PN_SOLVE_CACHE", str(tmp_path))
    _docs(tmp_path, 3)
    r = CliRunner().invoke(app, ["cache", "compress", "--dry-run"])
    assert r.exit_code == 0 and "3 plain" in r.output and len(list(tmp_path.glob("*.json"))) == 3
    r = CliRunner().invoke(app, ["cache", "compress", "--limit", "2"])
    assert r.exit_code == 0 and "2 converted" in r.output
    assert len(list(tmp_path.glob("*.json.gz"))) == 2


# ------------------------------------------------------------------ readers (C2)


def test_leaks_doc_stats_reads_gz_like_plain(tmp_path: Path) -> None:
    from fake_tree import fake_runner

    from pn.train import leaks

    spot = _spot("Kd7s2c")
    SolveCache(tmp_path / "gz", runner=fake_runner).solve(spot)
    (toml,) = (tmp_path / "gz").glob("*.toml")
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / toml.name).write_text(toml.read_text())
    (plain / f"{toml.stem}.json").write_text(json.dumps(SolveCache(tmp_path / "gz").get(toml.stem)))
    assert leaks.doc_stats(toml) is not None and leaks.doc_stats(toml) == leaks.doc_stats(plain / toml.name)
    model = leaks.Leaks(None, cache_root=tmp_path / "gz")
    assert model.postflop() == leaks.Leaks(None, cache_root=plain).postflop() != {}


def test_registry_and_library_rows_open_and_delete_gz_documents(tmp_path: Path) -> None:
    from pn import ranges
    from pn.web import solves as ws
    from pn.web.study import library_rows

    def runner(spot: dict, backend: str | None) -> tuple[dict, str]:
        return {"meta": {"backend": "texas", "iterations": 3}, "players": [],
                "combos": list(ranges.all_combos()), "root": "n0",
                "nodes": {"n0": {"type": "terminal", "kind": "fold", "pot": 1}}}, "texas"  # fmt: skip

    cache = SolveCache(tmp_path / "cache", runner=runner)
    spot = _spot("As7d2c")
    key = spot_key(spot)
    cache.solve(spot)
    _plain(cache.root, K1, {"meta": {"backend": "texas"}})  # a plain document without a .toml: not listed
    reg = ws.Registry(tmp_path / "solves", cache)
    rows = library_rows(reg)
    assert [r["key"] for r in rows] == [key] and rows[0]["iterations"] == 3
    name = rows[0]["name"]
    assert reg.get(name)["meta"]["backend"] == "texas"
    out = reg.delete(name)
    assert sorted(Path(f).name for f in out["files"]) == [f"{key}.json.gz", f"{key}.toml"]
    assert docstore.doc_path(cache.root, key) is None


def test_seed_list_skips_library_documents_in_either_form(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    tools = Path(__file__).resolve().parents[2] / "tools"
    spec = importlib.util.spec_from_file_location("seed_list", tools / "seed_list.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    cache = tmp_path / "data" / "solves" / "cache"
    for name in (f"{K1}.json.gz", f"{K1}.toml", f"{K2}.json", f"{K2}.toml", f"{K3}.json.gz",
                 f"{K3}.json.gz.tmp", "hu_flop_library.json"):
        cache.mkdir(parents=True, exist_ok=True)
        (cache / name).write_text("{}")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mod, "library_keys", lambda: {K1, K2})
    out: list[str] = []
    monkeypatch.setattr("builtins.print", out.append)
    mod.main()
    assert [x for x in out if x.startswith(mod.CACHE + "/") and x != mod.IDX] == [f"{mod.CACHE}/{K3}.json.gz"]
