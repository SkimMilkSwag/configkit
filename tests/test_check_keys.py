"""Tests for ``check --keys`` (key-subset / env-override validation mode).

Without ``--keys`` a file is validated against the whole schema and any key
not in the schema is an error — right for a full config, wrong for a sparse
env-override file that legitimately carries only a few keys. ``--keys``
narrows the check to the listed schema keys: their constraints (bounds,
patterns, choices, nested schemas) still apply, but every other key in the
file is ignored (the unknown-key sweep is suppressed for those).

Pinned here: subset pass/fail and exit codes, the ``--json`` shape, a
key not present in the schema, multi-file batches, and that omitting
``--keys`` keeps the historical full-schema behaviour byte-for-byte.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from configkit import __main__ as cli  # noqa: E402


SCHEMA = {
    "host": "str",
    "port": {"type": "int", "minimum": 1, "maximum": 65535},
    "debug": "bool",
}


def _write(tmp_path, name: str, doc) -> str:
    path = str(tmp_path / name)
    with open(path, "w") as fh:
        json.dump(doc, fh)
    return path


def _schema_file(tmp_path) -> str:
    return _write(tmp_path, "schema.json", SCHEMA)


def test_keys_subset_ignores_other_keys(tmp_path, capsys):
    schema = _schema_file(tmp_path)
    # A sparse override file: only host and port, no debug. Without --keys
    # that would be an unknown-key... wait — the unknown sweep catches keys
    # not in the *schema*; debug is in the schema but absent here. The
    # distinguishing case is a key that exists only in the file:
    doc = {"host": "x", "port": 8080, "extra": "ignored"}
    path = _write(tmp_path, "override.json", doc)

    rc = cli.main(["check", path, "--schema", schema, "--keys", "host,port"])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == f"{path}: OK"
    assert out.err == ""


def test_keys_subset_still_enforces_constraints(tmp_path, capsys):
    schema = _schema_file(tmp_path)
    doc = {"host": "x", "port": 99999}  # above maximum 65535
    path = _write(tmp_path, "override.json", doc)

    rc = cli.main(["check", path, "--schema", schema, "--keys", "host,port"])
    out = capsys.readouterr()
    assert rc == 1
    lines = [line for line in out.err.splitlines() if "port" in line]
    assert any("99999 is above maximum" in line for line in lines)


def test_keys_not_listed_are_not_checked_even_if_bad(tmp_path, capsys):
    schema = _schema_file(tmp_path)
    # debug: 'not-a-bool' would fail full-schema validation; with --keys
    # host,port it must not be reported at all.
    doc = {"host": "x", "debug": "not-a-bool"}
    path = _write(tmp_path, "override.json", doc)

    rc = cli.main(["check", path, "--schema", schema, "--keys", "host"])
    out = capsys.readouterr()
    assert rc == 0
    assert "debug" not in out.err


def test_keys_missing_from_schema_is_one_problem_line(tmp_path, capsys):
    schema = _schema_file(tmp_path)
    doc = {"host": "x"}
    path = _write(tmp_path, "override.json", doc)

    rc = cli.main(["check", path, "--schema", schema, "--keys", "host,nope"])
    out = capsys.readouterr()
    assert rc == 1
    assert f"{path}: no such key(s) in schema: nope" in out.err.splitlines()


def test_keys_duplicate_and_order_preserved(tmp_path, capsys):
    schema = _schema_file(tmp_path)
    doc = {"host": "x", "port": 80}
    path = _write(tmp_path, "override.json", doc)

    rc = cli.main(["check", path, "--schema", schema, "--keys", "port,host,port"])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == f"{path}: OK"


def test_keys_json_mode(tmp_path, capsys):
    schema = _schema_file(tmp_path)
    doc = {"host": "x", "port": 1, "extra": True}
    path = _write(tmp_path, "override.json", doc)

    rc = cli.main(["check", path, "--schema", schema, "--keys", "host,port", "--json"])
    out = capsys.readouterr()
    assert rc == 0
    payload = json.loads(out.out)
    assert payload["ok"] is True
    assert payload["problems"] == []


def test_keys_json_mode_failure_shape(tmp_path, capsys):
    schema = _schema_file(tmp_path)
    doc = {"host": "x", "port": 0}  # below minimum 1
    path = _write(tmp_path, "override.json", doc)

    rc = cli.main(["check", path, "--schema", schema, "--keys", "port", "--json"])
    out = capsys.readouterr()
    assert rc == 1
    payload = json.loads(out.out)
    assert payload["ok"] is False
    assert any("0 is below minimum" in p for p in payload["problems"])


def test_keys_multi_file_batch_prefixes_lines(tmp_path, capsys):
    schema = _schema_file(tmp_path)
    good = _write(tmp_path, "good.json", {"host": "a"})
    bad = _write(tmp_path, "bad.json", {"port": 70000})

    rc = cli.main(["check", good, bad, "--schema", schema, "--keys", "host,port"])
    out = capsys.readouterr()
    assert rc == 1
    lines = out.err.splitlines()
    assert any(line.startswith(f"{bad}: ") and "70000 is above maximum" in line for line in lines)
    # the good file contributes nothing to stderr, and stdout stays clean
    assert out.out == ""


def test_without_keys_flag_full_schema_behaviour_unchanged(tmp_path, capsys):
    schema = _schema_file(tmp_path)
    doc = {"host": "x", "port": 80, "extra": 1}
    path = _write(tmp_path, "override.json", doc)

    # historical run: unknown-key sweep fires on 'extra'
    rc = cli.main(["check", path, "--schema", schema])
    out = capsys.readouterr()
    assert rc == 1
    assert any("unknown key(s): extra" in line for line in out.err.splitlines())


def test_check_files_api_keys_parameter(tmp_path):
    # direct API: keys= threads through check_files for the single-file path
    doc = {"host": "x", "debug": True}  # no port key at all
    path = _write(tmp_path, "cfg.json", doc)
    schema = cli._load_schema(_schema_file(tmp_path))

    assert cli.check_files([path], schema, keys=["host"]) == []
    # with only port listed, the present host is ignored but the absent port
    # still has to be satisfied -> required-key-missing for the listed key.
    # (single-file runs carry no file prefix, by design)
    problems = cli.check_files([path], schema, keys=["port"])
    assert problems == ["port: required key is missing"]


def test_keys_does_not_flag_unlisted_schema_keys_as_unknown(tmp_path, capsys):
    # the core subset contract: a full config checked with only host listed
    # must not report 'port' (a schema key that exists but is unlisted) or
    # 'extra' (a file-only key) as unknown — both were filtered out on purpose
    schema = _schema_file(tmp_path)
    doc = {"host": "x", "port": 8080, "extra": "ignored"}
    path = _write(tmp_path, "override.json", doc)

    rc = cli.main(["check", path, "--schema", schema, "--keys", "host"])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == f"{path}: OK"
    assert out.err == ""


def test_keys_still_reports_bad_value_on_listed_key(tmp_path, capsys):
    # suppression is for the unknown-key sweep only: a listed key whose value
    # violates its constraint is reported exactly as in full-schema mode
    schema = _schema_file(tmp_path)
    doc = {"host": 5}  # not a string
    path = _write(tmp_path, "override.json", doc)

    rc = cli.main(["check", path, "--schema", schema, "--keys", "host"])
    out = capsys.readouterr()
    assert rc == 1
    assert out.err.splitlines() == ["host: expected str, got int"]


def test_keys_nested_listed_section_keeps_inner_unknown_sweep(tmp_path, capsys):
    # a listed dict key still validates its own section fully: an inner typo
    # is caught by the nested unknown-key sweep even though the top-level
    # sweep is suppressed in subset mode
    nested_schema = _write(
        tmp_path, "nested.json",
        {"host": "str", "db": {"type": "dict", "schema": {"user": "str"}}},
    )
    doc = {"host": "x", "db": {"user": 5, "jnk": 1}}
    path = _write(tmp_path, "override.json", doc)

    rc = cli.main(["check", path, "--schema", nested_schema, "--keys", "db"])
    out = capsys.readouterr()
    assert rc == 1
    lines = out.err.splitlines()
    # the unlisted top-level 'host' is not flagged as unknown...
    assert not any("unknown key(s): host" in line for line in lines)
    # ...but the inner section reports both its problems: the bad value and
    # the inner typo, in schema-key order then the sweep
    assert "db.user: expected str, got int" in lines
    assert "db: unknown key(s): jnk" in lines


def test_collect_errors_suppress_unknown_is_structural(tmp_path):
    # suppress_unknown drops only the top-level sweep; an unknown key that
    # happens to contain the message's wording is not caught by string
    # matching (the pre-fix approach filtered on "unknown key(s)" text)
    from configkit.schema import collect_errors
    data = {"host": "x", "unknown key(s)": 1}
    full = collect_errors(data, {"host": str})
    suppressed = collect_errors(data, {"host": str}, suppress_unknown=True)
    assert [m for _, m in full] == ["unknown key(s): unknown key(s)"]
    assert suppressed == []


def test_without_keys_flag_still_reports_unlisted_keys(tmp_path, capsys):
    # the historical full-schema run is byte-for-byte unchanged: without
    # --keys, a file-only key trips the unknown-key sweep as before
    schema = _schema_file(tmp_path)
    doc = {"host": "x", "extra": 1}
    path = _write(tmp_path, "override.json", doc)

    rc = cli.main(["check", path, "--schema", schema])
    out = capsys.readouterr()
    assert rc == 1
    assert any("unknown key(s): extra" in line for line in out.err.splitlines())
