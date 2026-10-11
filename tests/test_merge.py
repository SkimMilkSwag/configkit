"""Tests for the configkit merge subcommand and its helpers."""

import json
import subprocess
import sys

import pytest

from configkit.__main__ import ConfigError, deep_merge, merge_configs, render_merged


def _run_cli(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, "-m", "configkit", *args],
        capture_output=True,
        text=True,
    )


# --- deep_merge (unit level) -------------------------------------------------


def test_deep_merge_scalars_override():
    assert deep_merge({"a": 1}, {"a": 2}) == {"a": 2}


def test_deep_merge_dict_combines_keys():
    base = {"a": 1, "nested": {"x": 1, "y": 2}}
    override = {"b": 3, "nested": {"y": 9, "z": 4}}
    assert deep_merge(base, override) == {
        "a": 1,
        "b": 3,
        "nested": {"x": 1, "y": 9, "z": 4},
    }


def test_deep_merge_list_replaces_wholesale():
    # Lists are not merged element-wise; the override list wins entirely.
    assert deep_merge({"tags": ["a", "b"]}, {"tags": ["c"]}) == {"tags": ["c"]}


def test_deep_merge_dict_vs_scalar_replaces():
    assert deep_merge({"cfg": {"a": 1}}, {"cfg": "plain"}) == {"cfg": "plain"}
    assert deep_merge({"cfg": "plain"}, {"cfg": {"a": 1}}) == {"cfg": {"a": 1}}


def test_deep_merge_does_not_mutate_inputs():
    base = {"nested": {"x": 1, "y": 2}}
    override = {"nested": {"y": 9}}
    result = deep_merge(base, override)
    assert result is not base
    assert result is not override
    # original base untouched
    assert base == {"nested": {"x": 1, "y": 2}}


# --- merge_configs (file level) ----------------------------------------------


def test_merge_configs_later_wins(tmp_path):
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"host": "old", "port": 80}))
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"port": 8080, "debug": True}))
    merged = merge_configs([str(a), str(b)])
    assert merged == {"host": "old", "port": 8080, "debug": True}


def test_merge_configs_deep_merges_nested(tmp_path):
    a = tmp_path / "a.yaml"
    a.write_text("db:\n  user: old\n  pool: 5\n")
    b = tmp_path / "b.yaml"
    b.write_text("db:\n  pool: 10\n  host: db.example.com\n")
    merged = merge_configs([str(a), str(b)])
    assert merged == {"db": {"user": "old", "pool": 10, "host": "db.example.com"}}


def test_merge_configs_missing_file_raises(tmp_path):
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"x": 1}))
    with pytest.raises(ConfigError):
        merge_configs([str(a), str(tmp_path / "nope.json")])


def test_merge_configs_no_files_raises():
    with pytest.raises(ConfigError):
        merge_configs([])


# --- render_merged -----------------------------------------------------------


def test_render_merged_json_pretty():
    out = render_merged({"a": 1, "b": [1, 2]}, "json")
    parsed = json.loads(out)
    assert parsed == {"a": 1, "b": [1, 2]}
    # pretty-printed with indentation
    assert '\n' in out and '  "' in out


def test_render_merged_yaml():
    out = render_merged({"host": "x", "port": 80}, "yaml")
    assert "host: x" in out
    assert "port: 80" in out


def test_render_merged_bad_format_raises():
    with pytest.raises(ConfigError):
        render_merged({"a": 1}, "toml")


# --- CLI end-to-end ----------------------------------------------------------


def test_cli_merge_json_default(tmp_path):
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"host": "old", "port": 80}))
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"port": 8080, "debug": True}))
    proc = _run_cli("merge", str(a), str(b))
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data == {"host": "old", "port": 8080, "debug": True}


def test_cli_merge_yaml_output(tmp_path):
    a = tmp_path / "a.yaml"
    a.write_text("db:\n  user: old\n  pool: 5\n")
    b = tmp_path / "b.yaml"
    b.write_text("db:\n  pool: 10\n")
    proc = _run_cli("merge", "--format", "yaml", str(a), str(b))
    assert proc.returncode == 0, proc.stderr
    assert "pool: 10" in proc.stdout
    # the key only present in the first file survives the deep merge
    assert "user: old" in proc.stdout


def test_cli_merge_missing_file_exits_one(tmp_path):
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"x": 1}))
    proc = _run_cli("merge", str(a), str(tmp_path / "nope.json"))
    assert proc.returncode == 1
    assert "not found" in proc.stderr


def test_cli_merge_three_files_left_to_right(tmp_path):
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"x": 1, "y": 2}))
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"y": 3, "z": 4}))
    c = tmp_path / "c.json"
    c.write_text(json.dumps({"x": 9}))
    proc = _run_cli("merge", str(a), str(b), str(c))
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    # left-to-right: x overridden last -> 9; y from b -> 3; z from b -> 4
    assert data == {"x": 9, "y": 3, "z": 4}


def test_cli_check_still_default_command(tmp_path):
    # No subcommand: the old check behavior is preserved.
    schema = tmp_path / "s.yaml"
    schema.write_text("host: str\n")
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"host": "x"}))
    proc = _run_cli(str(cfg), "--schema", str(schema))
    assert proc.returncode == 0, proc.stderr
    assert ": OK" in proc.stdout
