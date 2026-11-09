"""Tests for ``keys --values`` (path = value dump mode).

``keys`` normally prints bare dotted paths, one per line, ready to pipe back
into ``get``. The ``--values`` flag switches each line to ``path = value``
so a whole config can be dumped in a single scriptable call; values use the
same rendering as ``get`` (strings bare, everything else compact repr, null
for JSON/YAML null). Paths are still sorted and per-file, and multiple files
are concatenated in the given order.
"""

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


def test_keys_values_flat_document(tmp_path, capsys):
    doc = {"host": "x", "port": 8080, "debug": True, "ratio": 1.5, "none": None}
    path = _write(tmp_path, "cfg.json", doc)

    rc = cli.main(["keys", path, "--values"])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    # sorted paths, values rendered like `get`: strings bare, numbers/bools
    # via repr, null for None
    assert out == [
        "debug = True",
        "host = x",
        "none = null",
        "port = 8080",
        "ratio = 1.5",
    ]


def test_keys_values_nested_and_lists(tmp_path, capsys):
    doc = {
        "db": {"user": "u", "pool_size": 5},
        "workers": [{"name": "a"}, {"name": "b"}],
        "top": 1,
    }
    path = _write(tmp_path, "cfg.json", doc)

    rc = cli.main(["keys", path, "--values"])
    out = capsys.readouterr().out
    assert rc == 0
    lines = out.splitlines()
    # containers are dumped wholesale at their own key (repr form), and list
    # members still get their indexed paths
    assert "db = {'user': 'u', 'pool_size': 5}" in lines
    assert "db.user = u" in lines
    assert "db.pool_size = 5" in lines
    assert "workers.0.name = a" in lines
    assert "workers.1.name = b" in lines
    assert "top = 1" in lines
    # still sorted path-first: every line starts with its bare path
    paths = [line.split(" = ")[0] for line in lines]
    assert paths == sorted(paths)


def test_keys_values_strings_print_bare_for_pipes(tmp_path, capsys):
    doc = {"dsn": "postgres://u:p@h/db", "count": 3}
    path = _write(tmp_path, "cfg.json", doc)

    rc = cli.main(["keys", path, "--values"])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    # no repr quotes around the string: `cut -d' ' -f3- | xargs` stays clean
    assert "dsn = postgres://u:p@h/db" in out


def test_keys_default_still_bare_paths(tmp_path, capsys):
    doc = {"a": 1}
    path = _write(tmp_path, "cfg.json", doc)

    rc = cli.main(["keys", path])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    # without --values the historical bare-path form is unchanged
    assert out == ["a"]


def test_keys_values_multiple_files_in_order(tmp_path, capsys):
    p1 = _write(tmp_path, "a.json", {"a": 1})
    p2 = _write(tmp_path, "b.json", {"b": "y"})

    rc = cli.main(["keys", p1, p2, "--values"])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert out == ["a = 1", "b = y"]


def test_keys_values_missing_file_exits_one(tmp_path, capsys):
    path = _write(tmp_path, "cfg.json", {"a": 1})

    rc = cli.main(["keys", str(tmp_path / "nope.json"), "--values"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err


def test_keys_values_run_api_flag(tmp_path, capsys):
    # direct API: with_values=True flips the line format
    doc = {"a": {"b": "v"}}
    paths = cli._iter_keys(doc)
    assert paths == ["a", "a.b"]
    rendered = [
        f"{p} = {cli._render_get_value(cli.lookup_path(doc, p))}" for p in paths
    ]
    assert rendered == ["a = {'b': 'v'}", "a.b = v"]
