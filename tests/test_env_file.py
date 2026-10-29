"""Tests for the ``check --env-file`` flag and ``load_env_file`` parsing."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from configkit import EnvError, load_env_file  # noqa: E402
from configkit import __main__ as cli  # noqa: E402


def _write(tmp_path, name: str, doc) -> str:
    path = str(tmp_path / name)
    with open(path, "w") as fh:
        if isinstance(doc, (dict, list)):
            json.dump(doc, fh)
        else:
            fh.write(doc)
    return path


# --- load_env_file parsing ----------------------------------------------------


def test_load_env_file_basic(tmp_path):
    p = _write(tmp_path, "env", "HOST=prod.example.com\nPORT=8080\n")
    assert load_env_file(p) == {"HOST": "prod.example.com", "PORT": "8080"}


def test_load_env_file_ignores_comments_and_blank_lines(tmp_path):
    p = _write(
        tmp_path,
        "env",
        "# production overrides\n\nHOST=prod.example.com\n\n# trailing comment\n",
    )
    assert load_env_file(p) == {"HOST": "prod.example.com"}


def test_load_env_file_strips_surrounding_quotes(tmp_path):
    p = _write(
        tmp_path,
        "env",
        'NAME="a b c"\nPATH_VAR=\'/usr/local/bin\'\nBARE=plain\n',
    )
    out = load_env_file(p)
    assert out["NAME"] == "a b c"
    assert out["PATH_VAR"] == "/usr/local/bin"
    assert out["BARE"] == "plain"


def test_load_env_file_export_prefix_accepted(tmp_path):
    p = _write(tmp_path, "env", "export FOO=bar\n")
    assert load_env_file(p) == {"FOO": "bar"}


def test_load_env_file_later_line_wins(tmp_path):
    p = _write(tmp_path, "env", "HOST=a\nHOST=b\n")
    assert load_env_file(p) == {"HOST": "b"}


def test_load_env_file_malformed_line_raises(tmp_path):
    p = _write(tmp_path, "env", "HOST=ok\nnot-a-pair\n")
    with pytest.raises(EnvError, match="expected KEY=VALUE"):
        load_env_file(p)


def test_load_env_file_missing_file_raises(tmp_path):
    with pytest.raises(EnvError, match="cannot read env file"):
        load_env_file(str(tmp_path / "nope.env"))


# --- CLI: check --env-file -----------------------------------------------------


SCHEMA = {"host": "str", "port": "int"}


def test_check_env_file_resolves_placeholders(tmp_path, monkeypatch, capsys):
    schema = _write(tmp_path, "s.json", SCHEMA)
    cfg = _write(tmp_path, "c.json", {"host": "${HOST}", "port": 80})
    envf = _write(tmp_path, "vars.env", "HOST=from-file.example.com\n")

    # The ambient environment must NOT be consulted: even though this value is
    # different, the file wins.
    monkeypatch.setenv("HOST", "ambient.example.com")

    rc = cli.main(["check", cfg, "--schema", schema, "--env-file", envf])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out


def test_check_env_file_missing_var_fails(tmp_path):
    schema = _write(tmp_path, "s.json", SCHEMA)
    cfg = _write(tmp_path, "c.json", {"host": "${HOST}", "port": 80})
    envf = _write(tmp_path, "vars.env", "PORT=80\n")

    rc = cli.main(["check", cfg, "--schema", schema, "--env-file", envf])
    assert rc == 1


def test_check_env_file_default_still_applies(tmp_path):
    # ${HOST:default} with HOST absent from the file uses the literal default.
    schema = _write(tmp_path, "s.json", SCHEMA)
    cfg = _write(tmp_path, "c.json", {"host": "${HOST:fallback.local}", "port": 80})
    envf = _write(tmp_path, "vars.env", "PORT=80\n")

    rc = cli.main(["check", cfg, "--schema", schema, "--env-file", envf])
    assert rc == 0


def test_check_env_file_malformed_file_exits_one(tmp_path, capsys):
    schema = _write(tmp_path, "s.json", SCHEMA)
    cfg = _write(tmp_path, "c.json", {"host": "x", "port": 80})
    envf = _write(tmp_path, "vars.env", "BROKEN LINE NO EQUALS\n")

    rc = cli.main(["check", cfg, "--schema", schema, "--env-file", envf])
    assert rc == 1
    err = capsys.readouterr().err
    assert "expected KEY=VALUE" in err


def test_check_env_file_json_mode(tmp_path, capsys):
    schema = _write(tmp_path, "s.json", SCHEMA)
    cfg = _write(tmp_path, "c.json", {"host": "${HOST}", "port": 80})
    envf = _write(tmp_path, "vars.env", "HOST=ok.example.com\n")

    rc = cli.main(["check", cfg, "--schema", schema, "--env-file", envf, "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["problems"] == []
