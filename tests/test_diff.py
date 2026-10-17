"""Tests for the configkit diff subcommand and its helpers."""
import json
import subprocess
import sys

from configkit.__main__ import ConfigError, diff_configs


def _run_cli(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, "-m", "configkit", *args],
        capture_output=True,
        text=True,
    )


# --- diff_configs (unit level) -------------------------------------------------


def test_diff_configs_identical_documents_empty():
    doc = {"a": 1, "nested": {"x": [1, 2]}}
    assert diff_configs(doc, json.loads(json.dumps(doc))) == []


def test_diff_configs_replaced_scalar():
    lines = diff_configs({"host": "old"}, {"host": "new"})
    assert lines == ["host: replaced (old -> new)"]


def test_diff_configs_added_and_removed_keys():
    lines = diff_configs({"a": 1, "b": 2}, {"a": 1, "c": 3})
    # deterministic: sorted by path, b first, then c
    assert lines == ["b: removed (2)", "c: added (3)"]


def test_diff_configs_nested_paths_dotted():
    base = {"db": {"pool_size": 5, "user": "old"}, "port": 80}
    override = {"db": {"pool_size": 10, "host": "db.example.com"}, "port": 80}
    lines = diff_configs(base, override)
    assert sorted(lines) == [
        "db.host: added (db.example.com)",
        "db.pool_size: replaced (5 -> 10)",
        "db.user: removed (old)",
    ]


def test_diff_configs_non_dict_value_change_is_single_replaced_line():
    lines = diff_configs({"tags": ["a", "b"]}, {"tags": ["c"]})
    assert lines == ["tags: replaced (['a', 'b'] -> ['c'])"]


def test_diff_configs_dict_vs_scalar_replaced_wholesale():
    # A section that is a mapping on one side and a scalar on the other has
    # no keys to walk, so it reports one replaced line, not per-key noise.
    lines = diff_configs({"cfg": {"a": 1}}, {"cfg": "plain"})
    assert lines == ["cfg: replaced ({'a': 1} -> plain)"]


def test_diff_configs_bool_and_none_rendered_repr():
    lines = diff_configs(
        {"debug": False, "timeout": 30},
        {"debug": True, "timeout": None},
    )
    assert lines == ["debug: replaced (False -> True)", "timeout: replaced (30 -> None)"]


def test_diff_configs_deeply_nested_paths():
    base = {"a": {"b": {"c": 1}}}
    override = {"a": {"b": {"c": 2, "d": 3}}}
    lines = diff_configs(base, override)
    assert lines == ["a.b.c: replaced (1 -> 2)", "a.b.d: added (3)"]


def test_diff_configs_strings_rendered_bare():
    # No repr quotes around string values: output stays human-readable.
    lines = diff_configs({"mode": "fast"}, {"mode": "safe"})
    assert lines == ["mode: replaced (fast -> safe)"]


# --- CLI end-to-end ------------------------------------------------------------


def test_cli_diff_prints_changes_and_count(tmp_path):
    a = tmp_path / "a.yaml"
    a.write_text("host: old\nport: 80\ntimeout: 30\n")
    b = tmp_path / "b.yaml"
    b.write_text("host: new\nport: 80\ndebug: true\n")
    proc = _run_cli("diff", str(a), str(b))
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "host: replaced (old -> new)" in out
    assert "timeout: removed (30)" in out
    # booleans render as Python repr (True/False) — YAML true parses to True
    assert "debug: added (True)" in out
    # unchanged port is not reported; total count at the end
    assert "port" not in out
    assert "3 change(s)" in out


def test_cli_diff_identical_files(tmp_path):
    doc = json.dumps({"x": 1, "y": [1, 2]})
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(doc)
    b.write_text(doc)
    proc = _run_cli("diff", str(a), str(b))
    assert proc.returncode == 0, proc.stderr
    assert "are identical" in proc.stdout
    # no change lines, no count
    assert "change(s)" not in proc.stdout


def test_cli_diff_nested_yaml_changes(tmp_path):
    a = tmp_path / "a.yaml"
    a.write_text("db:\n  user: old\n  pool_size: 5\n")
    b = tmp_path / "b.yaml"
    b.write_text("db:\n  pool_size: 10\n  host: db.example.com\n")
    proc = _run_cli("diff", str(a), str(b))
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "db.user: removed (old)" in out
    assert "db.pool_size: replaced (5 -> 10)" in out
    assert "db.host: added (db.example.com)" in out
    assert "3 change(s)" in out


def test_cli_diff_missing_file_exits_one(tmp_path):
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"x": 1}))
    proc = _run_cli("diff", str(a), str(tmp_path / "nope.json"))
    assert proc.returncode == 1
    assert "not found" in proc.stderr


def test_cli_diff_direction_is_left_base_right_override(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"host": "old"}))
    b.write_text(json.dumps({"host": "new"}))
    fwd = _run_cli("diff", str(a), str(b))
    rev = _run_cli("diff", str(b), str(a))
    assert fwd.returncode == 0 and rev.returncode == 0
    # the same change is reported in both directions; the values swap sides
    assert "host: replaced (old -> new)" in fwd.stdout
    assert "host: replaced (new -> old)" in rev.stdout


def test_cli_merge_still_works_alongside_diff(tmp_path):
    # diff is a sibling of merge, not a rename of it
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"host": "old", "port": 80}))
    b.write_text(json.dumps({"port": 8080}))
    merged = _run_cli("merge", str(a), str(b))
    assert merged.returncode == 0, merged.stderr
    assert json.loads(merged.stdout) == {"host": "old", "port": 8080}
