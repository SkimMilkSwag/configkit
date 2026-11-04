"""Tests for the ``get`` subcommand (dotted-path value lookup)."""

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


DOC = {
    "server": {"host": "0.0.0.0", "tls": {"cert": "/etc/ssl/cert.pem", "port": 8443}},
    "db": {"pool_size": 5, "hosts": ["a.local", "b.local"]},
    "plain": "scalar",
    "off": None,
}


def test_get_scalar_leaf(tmp_path, capsys):
    path = _write(tmp_path, "cfg.json", DOC)
    rc = cli.main(["get", path, "db.pool_size"])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.splitlines() == ["5"]


def test_get_string_prints_bare(tmp_path, capsys):
    """Strings must print unquoted — `get ... | xargs` is the main use case."""
    path = _write(tmp_path, "cfg.json", DOC)
    rc = cli.main(["get", path, "server.tls.cert"])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == "/etc/ssl/cert.pem"


def test_get_none_prints_null(tmp_path, capsys):
    path = _write(tmp_path, "cfg.json", DOC)
    rc = cli.main(["get", path, "off"])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == "null"


def test_get_nested_dict(tmp_path, capsys):
    path = _write(tmp_path, "cfg.json", DOC)
    rc = cli.main(["get", path, "server.tls.port"])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.splitlines() == ["8443"]


def test_get_list_member_by_index(tmp_path, capsys):
    path = _write(tmp_path, "cfg.json", DOC)
    rc = cli.main(["get", path, "db.hosts.1"])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == "b.local"


def test_get_whole_section(tmp_path, capsys):
    """A path that stops at a dict returns its compact repr form."""
    path = _write(tmp_path, "cfg.json", DOC)
    rc = cli.main(["get", path, "db"])
    out = capsys.readouterr()
    assert rc == 0
    line = out.out.strip()
    assert "pool_size" in line and "5" in line


def test_get_missing_key_exit_1(tmp_path, capsys):
    path = _write(tmp_path, "cfg.json", DOC)
    rc = cli.main(["get", path, "nope.host"])
    out = capsys.readouterr()
    assert rc == 1
    assert "no key" in out.err and "'nope'" in out.err
    assert out.out == ""


def test_get_bad_list_index_exit_1(tmp_path, capsys):
    path = _write(tmp_path, "cfg.json", DOC)
    rc = cli.main(["get", path, "db.hosts.9"])
    out = capsys.readouterr()
    assert rc == 1
    assert "bad index" in out.err


def test_get_descend_into_scalar_exit_1(tmp_path, capsys):
    path = _write(tmp_path, "cfg.json", DOC)
    rc = cli.main(["get", path, "plain.child"])
    out = capsys.readouterr()
    assert rc == 1
    # the error names the segment that hit the scalar
    assert "child" in out.err


def test_get_missing_file_exit_1(tmp_path, capsys):
    rc = cli.main(["get", str(tmp_path / "nope.json"), "db.pool_size"])
    out = capsys.readouterr()
    assert rc == 1
    assert "not found" in out.err


def test_get_multiple_files_prints_each(tmp_path, capsys):
    a = _write(tmp_path, "a.json", {"v": 1})
    b = _write(tmp_path, "b.json", {"v": 2})
    rc = cli.main(["get", a, b, "v"])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.splitlines() == ["1", "2"]


def test_get_one_missing_among_many_exit_1(tmp_path, capsys):
    """A bad path in one file fails the call, but good lookups still print."""
    a = _write(tmp_path, "a.json", {"v": 1})
    b = _write(tmp_path, "b.json", {"w": 2})
    rc = cli.main(["get", a, b, "v"])
    out = capsys.readouterr()
    assert rc == 1
    # file a's value still made it to stdout
    assert out.out.splitlines() == ["1"]
    assert str(b) in out.err


def test_lookup_path_direct():
    from configkit.__main__ import lookup_path

    doc = {"a": {"b": [{"c": 7}]}}
    assert lookup_path(doc, "a.b.0.c") == 7
    assert lookup_path(doc, "a.b") == [{"c": 7}]


def test_lookup_path_keyerror_names_missing_segment():
    from configkit.__main__ import lookup_path

    try:
        lookup_path({"a": 1}, "a.b.c")
    except KeyError as exc:
        # the innermost missing segment is what a caller wants to show
        assert "'b'" in str(exc) or "'c'" in str(exc)
    else:
        raise AssertionError("expected KeyError")
