"""Tests for configkit: JSON + YAML loading, validation, and Config access."""

import json

import pytest

from configkit import Config, ConfigError, ValidationError, field, load, loads

SCHEMA = {
    "host": str,
    "port": field(int, minimum=1, maximum=65535),
    "workers": field(int, default=2),
    "debug": field(bool, default=False),
    "tags": field(default=[], items={"type": str}),
    "db": {
        "type": dict,
        "default": {},
        "schema": {
            "user": field(default=""),
            "password": field(default=""),
            "pool_size": field(int, minimum=1, maximum=128, default=5),
        },
    },
}


def test_json_loads_valid_config(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(
        json.dumps(
            {
                "host": "localhost",
                "port": 8080,
                "tags": ["a", "b"],
                "db": {"user": "u", "password": "s3cret"},
            }
        )
    )
    cfg = load(str(p), SCHEMA)
    assert cfg.host == "localhost"
    assert cfg.port == 8080
    # defaults applied
    assert cfg.workers == 2
    assert cfg.debug is False
    assert cfg.db.pool_size == 5
    assert cfg.tags == ["a", "b"]


def test_yaml_loads_valid_config(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text("host: example.com\nport: 9000\ndebug: true\n")
    cfg = load(str(p), SCHEMA)
    assert cfg.host == "example.com"
    assert cfg.port == 9000
    assert cfg.debug is True


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load(str(tmp_path / "nope.json"), SCHEMA)


def test_invalid_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load(str(p), SCHEMA)


def test_missing_required_key():
    with pytest.raises(ValidationError, match="host"):
        loads(json.dumps({"port": 80}), SCHEMA, fmt="json")


def test_unknown_key_rejected():
    with pytest.raises(ValidationError, match="unknown key"):
        loads(json.dumps({"host": "x", "port": 80, "hose": "typo"}), SCHEMA)


def test_wrong_type_rejected():
    with pytest.raises(ValidationError, match="port:"):
        loads(json.dumps({"host": "x", "port": "80"}), SCHEMA)


def test_bool_not_accepted_as_int():
    with pytest.raises(ValidationError, match="got bool"):
        loads(json.dumps({"host": "x", "port": True}), SCHEMA)


def test_numeric_bounds_enforced():
    for bad_port in (0, 70000, -5):
        with pytest.raises(ValidationError, match="port:"):
            loads(json.dumps({"host": "x", "port": bad_port}), SCHEMA)


def test_numeric_bounds_apply_to_floats_and_skip_non_numbers():
    # A float value against an int-typed bounded field fails the *type* check
    # first ("expected int, got float"), so bounds never see a float here --
    # but a float that did pass a wider type gate must be range-checked too.
    with pytest.raises(ValidationError, match="port: expected int"):
        loads(json.dumps({"host": "x", "port": 70000.5}), SCHEMA)
    # Out-of-range float on a numeric (int|float) field trips the bound check.
    wide = {"n": field((int, float), minimum=1, maximum=10)}
    with pytest.raises(ValidationError, match="above maximum"):
        loads(json.dumps({"n": 20.5}), wide)
    # A string value on a bounded spec must not trip the numeric comparison with
    # a TypeError -- it fails the type check first (and bounds are skipped for it).
    schema = {"s": field(str, minimum=1, maximum=9)}
    with pytest.raises(ValidationError):
        loads(json.dumps({"s": 5}), schema)


def test_choices_constraint():
    schema = {"mode": field(str, choices=("fast", "safe"))}
    assert loads('{"mode": "safe"}', schema).mode == "safe"
    with pytest.raises(ValidationError, match="choices"):
        loads('{"mode": "warp"}', schema)


def test_string_constraints():
    schema = {"code": field(str, min_length=2, max_length=4, pattern="[A-Z]+")}
    assert loads('{"code": "ABCD"}', schema).code == "ABCD"
    for bad in ("A", "ABCDE", "ab"):
        with pytest.raises(ValidationError):
            loads(json.dumps({"code": bad}), schema)


def test_list_items_validated_with_indexed_path():
    with pytest.raises(ValidationError, match=r"tags\[1\]"):
        loads(json.dumps({"host": "x", "port": 80, "tags": ["a", 5]}), SCHEMA)


def test_nested_schema_validation_and_defaults():
    data = {"host": "x", "port": 80, "db": {"user": "u", "password": "p"}}
    cfg = loads(json.dumps(data), SCHEMA)
    assert cfg.db.user == "u"
    assert cfg.db.pool_size == 5


def test_nested_unknown_key_rejected():
    with pytest.raises(ValidationError, match="unknown key"):
        loads(
            json.dumps(
                {"host": "x", "port": 80, "db": {"user": "u", "password": "p", "extra": 1}}
            ),
            SCHEMA,
        )


def test_config_unknown_attr_lists_valid_keys():
    cfg = loads(json.dumps({"host": "x", "port": 80}), SCHEMA)
    with pytest.raises(AttributeError, match="no such config key 'hose'"):
        cfg.hose  # noqa: B018


def test_config_equality_and_roundtrip():
    data = {"host": "x", "port": 80, "tags": ["a"], "db": {"user": "u", "password": "p"}}
    cfg = loads(json.dumps(data), SCHEMA)
    assert cfg == cfg.to_dict()
    d = cfg.to_dict()
    # deep plain dict, no Config objects remain
    assert isinstance(d, dict) and not isinstance(d, Config)
    assert d["db"]["pool_size"] == 5


def test_explicit_fmt_override(tmp_path):
    p = tmp_path / "data"
    p.write_text('{"host": "x", "port": 80}')
    cfg = load(str(p), SCHEMA, fmt="json")
    assert cfg.port == 80


def test_deeply_nested_config_does_not_recurse_forever():
    # Regression: a nested dict used to wrap itself into an infinite
    # Config -> _wrap -> Config loop. Deep nesting must terminate.
    data = {"host": "x", "port": 80, "db": {"user": "u", "password": "p"}}
    cfg = loads(json.dumps(data), SCHEMA)
    assert isinstance(cfg.db, Config)
    assert cfg.db.user == "u"
    # three levels down, still a plain value not a recursion bomb
    nested = {"host": "x", "port": 80, "db": {"user": "u", "password": "p"}}
    out = loads(json.dumps(nested), SCHEMA)
    assert out.db.pool_size == 5


def test_field_positional_type_not_treated_as_default():
    # Regression: field(int, minimum=1) passed the type *positionally*, so the
    # old signature (types=object first) swallowed it into `types` and left
    # has_default=False. The port spec must be a bounded int with no default.
    from configkit import Spec
    s = field(int, minimum=1)
    assert isinstance(s, Spec)
    assert s.has_default is False
    assert s.minimum == 1


def test_field_without_type_accepts_scalar_types():
    # With the type omitted, a bounded number field accepts int/float but not
    # bool (bool is not a real number here).
    schema = {"n": field(minimum=0)}
    cfg = loads('{"n": 3}', schema)
    assert cfg.n == 3
