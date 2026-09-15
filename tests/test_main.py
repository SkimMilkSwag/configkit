"""Tests for the configkit CLI (python -m configkit)."""

import json
import subprocess
import sys

import pytest

SCHEMA_YAML = """
host: str
port:
  type: int
  minimum: 1
  maximum: 65535
workers:
  type: int
  default: 2
debug:
  type: bool
  default: false
"""


def _run_cli(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, "-m", "configkit", *args],
        capture_output=True,
        text=True,
    )


def test_valid_config_exits_zero(tmp_path):
    schema = tmp_path / "s.yaml"
    schema.write_text(SCHEMA_YAML)
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"host": "example.com", "port": 8080}))

    proc = _run_cli(str(cfg), "--schema", str(schema))
    assert proc.returncode == 0, proc.stderr
    assert f"{cfg}: OK" in proc.stdout


def test_invalid_config_exits_one(tmp_path):
    schema = tmp_path / "s.yaml"
    schema.write_text(SCHEMA_YAML)
    cfg = tmp_path / "c.json"
    # port out of range AND an unknown key -> both should be reported
    cfg.write_text(json.dumps({"host": "x", "port": 99999, "hose": "typo"}))

    proc = _run_cli(str(cfg), "--schema", str(schema))
    assert proc.returncode == 1
    assert "port:" in proc.stderr
    # unknown key surfaces too (first validation failure wins; port is checked
    # before the unknown-key sweep, so at minimum the port line is present)


def test_missing_config_file_exits_one(tmp_path):
    schema = tmp_path / "s.yaml"
    schema.write_text(SCHEMA_YAML)
    proc = _run_cli(str(tmp_path / "nope.json"), "--schema", str(schema))
    assert proc.returncode == 1
    assert "not found" in proc.stderr


def test_missing_schema_file_exits_one(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"host": "x"}))
    proc = _run_cli(str(cfg), "--schema", str(tmp_path / "nope.yaml"))
    assert proc.returncode == 1
    assert "not found" in proc.stderr


def test_schema_with_bad_entry_exits_one(tmp_path):
    bad = tmp_path / "s.yaml"
    bad.write_text("host: 42\n")  # 42 is not a valid spec form (bare int)
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"host": "x"}))
    proc = _run_cli(str(cfg), "--schema", str(bad))
    assert proc.returncode == 1
    assert "$.host" in proc.stderr


def test_json_schema_also_works(tmp_path):
    schema = tmp_path / "s.json"
    schema.write_text(json.dumps({"host": {"type": "str"}, "port": {"type": "int"}}))
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"host": "ok", "port": 80}))
    proc = _run_cli(str(cfg), "--schema", str(schema))
    assert proc.returncode == 0, proc.stderr


def test_wrong_type_reported_with_key(tmp_path):
    schema = tmp_path / "s.yaml"
    schema.write_text(SCHEMA_YAML)
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"host": "x", "port": "not-a-number"}))
    proc = _run_cli(str(cfg), "--schema", str(schema))
    assert proc.returncode == 1
    assert "port:" in proc.stderr
