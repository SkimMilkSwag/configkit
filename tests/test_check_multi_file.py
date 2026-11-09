"""Tests for multi-file ``check`` (several configs in one invocation).

The historical single-file form -- one config argument, ``<key>: <message>``
problem lines with no file prefix -- must stay byte-for-byte unchanged. The
new form accepts two or more config files validated against the same schema:
every file is checked in the same run (a parse failure in one does not stop
the others), and each problem line is prefixed with its file name so a batch
run stays attributable. Exit code is 0 only when every file is valid.
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
"""


def _run_cli(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, "-m", "configkit", *args],
        capture_output=True,
        text=True,
    )


def _write(tmp_path, name: str, doc) -> str:
    path = tmp_path / name
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return str(path)


def test_single_file_output_unchanged(tmp_path):
    # the pre-existing contract: one file, bare <key>: <message> lines
    schema = _write(tmp_path, "s.yaml", SCHEMA_YAML)
    cfg = _write(tmp_path, "c.json", {"port": 0})

    proc = _run_cli(str(cfg), "--schema", schema)
    assert proc.returncode == 1
    lines = [l for l in proc.stderr.splitlines() if l.strip()]
    # exactly the historical format -- no file-name prefix on single-file runs
    assert lines[0].startswith("host:")
    assert lines[1].startswith("port:")


def test_multi_file_reports_problems_from_every_file(tmp_path):
    schema = _write(tmp_path, "s.yaml", SCHEMA_YAML)
    good = _write(tmp_path, "good.json", {"host": "a", "port": 80})
    bad1 = _write(tmp_path, "bad1.json", {"host": 42, "port": 0})
    bad2 = _write(tmp_path, "bad2.json", {"port": 999})

    proc = _run_cli(good, bad1, bad2, "--schema", schema)
    assert proc.returncode == 1
    lines = [l for l in proc.stderr.splitlines() if l.strip()]
    # the valid file contributes nothing; each failing file's problems are
    # prefixed with that file's name and kept in file-argument order
    assert lines[0] == f"{bad1}: host: expected str, got int"
    assert lines[1].startswith(f"{bad1}: port:")
    assert lines[2] == f"{bad2}: host: required key is missing"
    # stdout stays clean for the human form
    assert proc.stdout == ""


def test_multi_file_all_valid_exits_zero(tmp_path):
    schema = _write(tmp_path, "s.yaml", SCHEMA_YAML)
    a = _write(tmp_path, "a.json", {"host": "a", "port": 1})
    b = _write(tmp_path, "b.json", {"host": "b", "port": 2})

    proc = _run_cli(a, b, "--schema", schema)
    assert proc.returncode == 0
    # the whole batch is confirmed as OK in a single summary line naming both
    # files, rather than one indistinguishable "OK" per file
    assert proc.stdout.splitlines() == [f"{a}, {b}: OK"]
    assert proc.stderr == ""


def test_multi_file_parse_error_does_not_stop_the_batch(tmp_path):
    schema = _write(tmp_path, "s.yaml", SCHEMA_YAML)
    broken = _write(tmp_path, "broken.json", "{not json")
    bad = _write(tmp_path, "bad.json", {"host": 42})

    proc = _run_cli(broken, bad, "--schema", schema)
    assert proc.returncode == 1
    lines = [l for l in proc.stderr.splitlines() if l.strip()]
    # the parse failure is one line for its file; the second file was still
    # validated and reported in the same run (host missing + port out of
    # range both fail under the schema)
    assert len(lines) == 3
    assert "invalid JSON" in lines[0]
    assert lines[1].startswith(f"{bad}: host:")
    assert lines[2].startswith(f"{bad}: port:")


def test_multi_file_json_problem_lines_are_file_prefixed(tmp_path):
    schema = _write(tmp_path, "s.yaml", SCHEMA_YAML)
    bad1 = _write(tmp_path, "bad1.json", {"host": 42})
    bad2 = _write(tmp_path, "bad2.json", {"port": 0})

    proc = _run_cli(bad1, bad2, "--schema", schema, "--json")
    assert proc.returncode == 1
    body = json.loads(proc.stdout)
    assert body["ok"] is False
    # every problem in the array names its file
    assert all(
        line.startswith(f"{bad1}: ") or line.startswith(f"{bad2}: ")
        for line in body["problems"]
    )
    # stdout-only contract still holds: no stderr in --json mode
    assert proc.stderr == ""


def test_check_files_api_single_file_no_prefix(tmp_path):
    from configkit import __main__ as cli

    schema = _write(tmp_path, "s.yaml", SCHEMA_YAML)
    bad = _write(tmp_path, "bad.json", {"port": 0})
    spec = cli._load_schema(schema)

    single = cli.check_files([bad], spec)
    multi = cli.check_files([bad, bad], spec)
    # one-file batch is the historical shape; two identical files duplicate
    # it with a file prefix on every line
    assert single[0].startswith("host:")
    assert len(multi) == len(single) * 2
    assert all(line.startswith(f"{bad}: ") for line in multi)
