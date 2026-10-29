"""Tests for the ``keys`` subcommand (dotted key-path listing)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from configkit import __main__ as cli  # noqa: E402


def _write(tmp_path, name: str, doc) -> str:
    path = str(tmp_path / name)
    with open(path, "w") as fh:
        json.dump(doc, fh)
    return path


def test_keys_flat_document(tmp_path, capsys):
    doc = {"host": "x", "port": 8080, "debug": True}
    path = _write(tmp_path, "cfg.json", doc)

    rc = cli.main(["keys", path])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert out == ["debug", "host", "port"]  # sorted


def test_keys_nested_paths_dotted(tmp_path, capsys):
    doc = {
        "server": {"host": "x", "tls": {"cert": "/c"}},
        "db": {"pool_size": 5},
        "plain": "scalar",
    }
    path = _write(tmp_path, "cfg.json", doc)

    rc = cli.main(["keys", path])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    # dict members at every level appear as dotted paths; scalar leaves
    # don't spawn children
    expected = sorted([
        "server", "server.host", "server.tls", "server.tls.cert",
        "db", "db.pool_size", "plain",
    ])
    assert out == expected


def test_keys_inside_lists_indexed(tmp_path, capsys):
    doc = {"workers": [{"name": "a"}, {"name": "b"}], "top": 1}
    path = _write(tmp_path, "cfg.json", doc)

    rc = cli.main(["keys", path])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    # list elements are addressed by index when they carry dict members;
    # the container key itself and scalar leaves produce no extra paths
    expected = sorted([
        "workers", "workers.0.name", "workers.1.name", "top",
    ])
    assert out == expected


def test_keys_empty_dict_prints_nothing(tmp_path, capsys):
    path = _write(tmp_path, "cfg.json", {})

    rc = cli.main(["keys", path])
    out = capsys.readouterr().out
    assert rc == 0
    assert out == ""


def test_keys_missing_file_returns_1(tmp_path, capsys):
    rc = cli.main(["keys", str(tmp_path / "nope.json")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "configkit:" in err and "nope.json" in err


def test_keys_multiple_files_concatenated(tmp_path, capsys):
    p1 = _write(tmp_path, "a.json", {"a": 1})
    p2 = _write(tmp_path, "b.json", {"b": 2})

    rc = cli.main(["keys", p1, p2])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    # per-file sorted output, files in given order
    assert out == ["a", "b"]


def test_keys_yaml_file(tmp_path, capsys):
    path = str(tmp_path / "cfg.yaml")
    with open(path, "w") as fh:
        fh.write("outer:\n  inner: value\nflag: true\n")

    rc = cli.main(["keys", path])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert out == sorted(["outer", "outer.inner", "flag"])


def test_keys_iter_helper_unit():
    doc = {"a": {"b": [1, {"c": 2}]}, "z": None}
    paths = cli._iter_keys(doc)
    assert paths == sorted([
        "a", "a.b", "a.b.1.c", "z",
    ])
