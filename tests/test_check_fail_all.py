"""Tests for check's fail-all reporting (collect_errors).

Before the fail-all change, check reported only the first validation failure
and stopped. These tests pin the new behaviour: every failing key is listed,
in schema key order with the unknown-key sweep last, in both the human
stderr form and the --json problems array.
"""

import json
import subprocess
import sys

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


def test_all_failures_reported_in_stderr(tmp_path):
    schema = tmp_path / "s.yaml"
    schema.write_text(SCHEMA_YAML)
    cfg = tmp_path / "c.json"
    # host wrong type, port out of bounds, unknown key: three separate faults
    cfg.write_text(json.dumps({"host": 42, "port": 99999, "hose": "typo"}))

    proc = _run_cli(str(cfg), "--schema", str(schema))
    assert proc.returncode == 1
    lines = [l for l in proc.stderr.splitlines() if l.strip()]
    assert len(lines) == 3, f"expected 3 problem lines, got {lines!r}"
    # schema key order: host, port, then the unknown-key sweep
    assert lines[0].startswith("host:")
    assert lines[1].startswith("port:")
    assert "unknown key" in lines[2]
    # nothing leaks to stdout for the human form
    assert proc.stdout == ""


def test_missing_required_and_bad_value_together(tmp_path):
    schema = tmp_path / "s.yaml"
    schema.write_text(SCHEMA_YAML)
    cfg = tmp_path / "c.json"
    # no host (required, no default) AND port with the wrong type
    cfg.write_text(json.dumps({"port": "not-a-number"}))

    proc = _run_cli(str(cfg), "--schema", str(schema))
    assert proc.returncode == 1
    lines = [l for l in proc.stderr.splitlines() if l.strip()]
    assert len(lines) == 2, f"expected 2 problem lines, got {lines!r}"
    assert "host" in lines[0] and "required key is missing" in lines[0]
    assert lines[1].startswith("port:")


def test_json_problems_array_lists_every_fault_in_order(tmp_path):
    schema = tmp_path / "s.yaml"
    schema.write_text(SCHEMA_YAML)
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"host": 42, "port": 0, "hose": "typo"}))

    proc = _run_cli(str(cfg), "--schema", str(schema), "--json")
    assert proc.returncode == 1
    body = json.loads(proc.stdout)
    assert body["ok"] is False
    assert len(body["problems"]) == 3
    # same ordering as the stderr form: schema key order, sweep last
    assert body["problems"][0].startswith("host:")
    assert body["problems"][1].startswith("port:")
    assert "unknown key" in body["problems"][2]
    # stdout-only contract still holds in fail-all mode
    assert proc.stderr == ""


def test_valid_config_still_has_empty_problems(tmp_path):
    schema = tmp_path / "s.yaml"
    schema.write_text(SCHEMA_YAML)
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"host": "example.com", "port": 8080}))

    proc = _run_cli(str(cfg), "--schema", str(schema), "--json")
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout)
    assert body["ok"] is True
    assert body["problems"] == []


def test_nested_dict_failures_all_reported(tmp_path):
    schema = tmp_path / "s.yaml"
    schema.write_text(
        """
db:
  type: dict
  schema:
    user: str
    pool_size:
      type: int
      minimum: 1
      maximum: 128
"""
    )
    cfg = tmp_path / "c.json"
    # both nested keys wrong at once: fail-all must list both, not just user
    cfg.write_text(json.dumps({"db": {"user": 7, "pool_size": 0}}))

    proc = _run_cli(str(cfg), "--schema", str(schema))
    assert proc.returncode == 1
    lines = [l for l in proc.stderr.splitlines() if l.strip()]
    assert len(lines) == 2, f"expected 2 problem lines, got {lines!r}"
    # nested paths are reported with their full dotted location
    assert "db.user" in lines[0]
    assert "db.pool_size" in lines[1]


def test_missing_nested_section_reports_its_own_keys(tmp_path):
    schema = tmp_path / "s.yaml"
    schema.write_text(
        """
db:
  type: dict
  schema:
    user: str
    pool_size:
      type: int
      default: 5
"""
    )
    cfg = tmp_path / "c.json"
    # db absent entirely: its required inner key is missing; the one with a
    # default (pool_size) stands in, so exactly one problem line appears
    cfg.write_text(json.dumps({}))

    proc = _run_cli(str(cfg), "--schema", str(schema))
    assert proc.returncode == 1
    lines = [l for l in proc.stderr.splitlines() if l.strip()]
    assert lines == ["db: required key is missing"]


def test_bad_file_parse_error_is_a_single_problem_line(tmp_path):
    schema = tmp_path / "s.yaml"
    schema.write_text(SCHEMA_YAML)
    cfg = tmp_path / "c.json"
    cfg.write_text("{not json")

    proc = _run_cli(str(cfg), "--schema", str(schema))
    assert proc.returncode == 1
    lines = [l for l in proc.stderr.splitlines() if l.strip()]
    # a parse failure short-circuits validation: one line, not a key walk
    assert len(lines) == 1
    assert "invalid JSON" in lines[0]
