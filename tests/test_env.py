"""Tests for environment-variable substitution (${VAR} / ${VAR:default})."""

import json

import pytest

from configkit import EnvError, ValidationError, load, loads, resolve_env


class _Env(dict):
    """dict-like env so .get() works like os.environ does."""


def test_simple_placeholder_resolves():
    env = _Env({"HOST": "prod.example.com"})
    cfg = loads(json.dumps({"host": "${HOST}", "port": 80}), {"host": str, "port": int}, env=env)
    assert cfg.host == "prod.example.com"


def test_default_used_when_unset():
    env = _Env({})
    cfg = loads(
        json.dumps({"host": "${HOST:fallback.local}"}),
        {"host": str},
        env=env,
    )
    assert cfg.host == "fallback.local"


def test_unset_without_default_raises():
    with pytest.raises(EnvError, match="HOST"):
        loads(json.dumps({"host": "${HOST}"}), {"host": str}, env=_Env({}))


def test_placeholder_in_list_and_nested_dict():
    env = _Env({"API_KEY": "k-123"})
    data = {
        "host": "${API_KEY}",
        "tags": ["${API_KEY}-suffix", "literal"],
        "db": {"user": "${API_KEY}"},
    }
    schema = {
        "host": str,
        "tags": {"type": list, "items": {"type": str}},
        "db": {
            "type": dict,
            "schema": {"user": str},
        },
    }
    cfg = loads(json.dumps(data), schema, env=env)
    assert cfg.host == "k-123"
    assert cfg.tags == ["k-123-suffix", "literal"]
    assert cfg.db.user == "k-123"


def test_multiple_placeholders_in_one_value():
    env = _Env({"USER": "wes", "DB": "main"})
    cfg = loads(
        json.dumps({"dsn": "${USER}@${DB}"}),
        {"dsn": str},
        env=env,
    )
    assert cfg.dsn == "wes@main"


def test_default_may_contain_placeholder():
    # ${HOST:${FALLBACK}}: HOST is unset, so its default (${FALLBACK}) is
    # re-scanned and resolves to the value of FALLBACK.
    env = _Env({"FALLBACK": "safe.local"})
    cfg = loads(
        json.dumps({"host": "${HOST:${FALLBACK}}"}),
        {"host": str},
        env=env,
    )
    assert cfg.host == "safe.local"


def test_unset_default_used_without_recursing():
    # ${HOST:default} with HOST unset returns the literal default; no error.
    cfg = loads(
        json.dumps({"host": "${HOST:localhost}"}),
        {"host": str},
        env=_Env({}),
    )
    assert cfg.host == "localhost"


def test_non_string_values_pass_through_untouched():
    data = {"port": 80, "debug": True, "tags": ["a", 5, None]}
    schema = {
        "port": int,
        "debug": bool,
        "tags": {"type": list},
    }
    cfg = loads(json.dumps(data), schema, env=_Env({}))
    assert cfg.port == 80 and cfg.debug is True
    assert cfg.tags == ["a", 5, None]


def test_substituted_value_must_still_pass_validation():
    # A placeholder that resolves to a string where an int is expected must
    # fail schema validation, not silently pass.
    env = _Env({"PORT": "not-a-number"})
    with pytest.raises(ValidationError, match="port"):
        loads(json.dumps({"port": "${PORT}"}), {"port": int}, env=env)


def test_resolve_env_walks_whole_document():
    env = _Env({"X": "1", "Y": "2"})
    out = resolve_env({"a": "${X}", "b": ["${Y}-x"], "c": 7, "d": None}, env=env)
    assert out == {"a": "1", "b": ["2-x"], "c": 7, "d": None}


def test_load_file_with_env(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"host": "${HOST:localhost}", "port": 80}))
    cfg = load(str(p), {"host": str, "port": int}, env=_Env({}))
    assert cfg.host == "localhost"
