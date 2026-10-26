"""TOML input support: loads/fmt="toml", .toml extension inference, CLI check.

Parsing strategy (see core._parse_toml): tomllib on 3.11+, tomli elsewhere,
and a built-in mini-parser as the last resort. Tests below exercise the
mini-parser directly by forcing _toml_lib() to return None via monkeypatch —
so both code paths get coverage regardless of what's installed on this box.
"""

import json

import pytest

from configkit import Config, ConfigError, ValidationError, field, load, loads
from configkit import core


SCHEMA = {
    "host": str,
    "port": field(int, minimum=1, maximum=65535),
    "debug": field(bool, default=False),
    "tags": field(default=[], items={"type": str}),
    "db": {
        "type": dict,
        "default": {},
        "schema": {
            "user": field(default=""),
            "pool_size": field(int, minimum=1, maximum=128, default=5),
        },
    },
}

TOML_DOC = """\
# a comment
host = "toml.internal"   # trailing comment
port = 7070
debug = true
tags = ["a", "b"]

[db]
user = "app"
pool_size = 15
"""


def test_toml_loads_valid():
    cfg = loads(TOML_DOC, SCHEMA, fmt="toml")
    assert isinstance(cfg, Config)
    assert cfg.host == "toml.internal"
    assert cfg.port == 7070
    assert cfg.debug is True
    assert cfg.tags == ["a", "b"]
    assert cfg.db.user == "app"
    assert cfg.db.pool_size == 15


def test_toml_extension_inference(tmp_path):
    p = tmp_path / "cfg.toml"
    p.write_text(TOML_DOC)
    cfg = load(str(p), SCHEMA)  # no fmt given: inferred from .toml
    assert cfg.host == "toml.internal"
    assert cfg.db.pool_size == 15


def test_toml_validation_errors_report_paths():
    bad = TOML_DOC.replace("port = 7070", "port = 99999")
    with pytest.raises(ValidationError) as exc:
        loads(bad, SCHEMA, fmt="toml")
    assert "port" in str(exc.value.path)


def test_toml_non_dict_top_level_rejected():
    # A bare string is not a mapping: the parser path must reject it with a
    # ConfigError rather than feeding validation something shapeless.
    with pytest.raises(ConfigError):
        core._mini_parse_toml('"just a string"')  # no key=value line -> error
    # And a valid table doc that parses to a mapping is accepted:
    assert isinstance(core._mini_parse_toml("x = 1"), dict)


def test_toml_malformed_raises_config_error():
    with pytest.raises(ConfigError):
        loads("host = ", SCHEMA, fmt="toml")
    with pytest.raises(ConfigError):
        loads("no_equals_sign_here", SCHEMA, fmt="toml")


# --- mini parser direct (force _toml_lib to None) ----------------------------


@pytest.fixture
def no_toml_lib(monkeypatch):
    monkeypatch.setattr(core, "_toml_lib", lambda: None)
    yield


def test_mini_parser_scalars_and_types(no_toml_lib):
    doc = core._mini_parse_toml(
        'name = "quoted"\n'
        "literal = 'raw'\n"
        "i = 42\n"
        "f = 1.5\n"
        "t = true\n"
        "fl = false\n"
        "arr = [1, 2, 3]\n"
        'strarr = ["x", "y"]\n'
    )
    assert doc == {
        "name": "quoted",
        "literal": "raw",
        "i": 42,
        "f": 1.5,
        "t": True,
        "fl": False,
        "arr": [1, 2, 3],
        "strarr": ["x", "y"],
    }


def test_mini_parser_tables_and_dotted_keys(no_toml_lib):
    text = (
        "a.b.c = 1\n"
        "[server]\n"
        "host = \"h\"\n"
        "port = 80\n"
        "[server.tls]\n"
        "enabled = true\n"
    )
    doc = core._mini_parse_toml(text)
    assert doc == {
        "a": {"b": {"c": 1}},
        "server": {"host": "h", "port": 80, "tls": {"enabled": True}},
    }


def test_mini_parser_comments_and_escapes(no_toml_lib):
    text = (
        '# full line comment\n'
        'msg = "line1\\nline2 # not a comment"  # real comment\n'
    )
    doc = core._mini_parse_toml(text)
    assert doc == {"msg": "line1\nline2 # not a comment"}


def test_mini_parser_table_header_collision_raises(no_toml_lib):
    with pytest.raises(ConfigError):
        core._mini_parse_toml("x = 1\n[x]\n")


def test_mini_parser_dotted_key_into_non_table_raises(no_toml_lib):
    # A dotted key whose path passes through an EXISTING scalar is invalid:
    # a.c = 1 defines a.c as a value, so a.c.d = 2 collides with it. (The
    # subtler case — a.b = 1 then a.c.d = 2 — is actually legal per the TOML
    # spec and tomllib/tomli both accept it; the mini-parser matches.) The
    # error names the offending line.
    with pytest.raises(ConfigError) as exc:
        core._mini_parse_toml("a.c = 1\na.c.d = 2\n")
    assert "line 2" in str(exc.value)


def test_mini_parser_unsupported_value_raises(no_toml_lib):
    with pytest.raises(ConfigError):
        core._mini_parse_toml("when = 1979-05-27T07:32:00Z\n")


def test_loads_toml_via_miniparser_end_to_end(no_toml_lib):
    cfg = loads(TOML_DOC, SCHEMA, fmt="toml")
    assert cfg.port == 7070
    assert cfg.db.pool_size == 15


# --- CLI ---------------------------------------------------------------------


def test_cli_check_toml_file(tmp_path):
    from configkit.__main__ import main

    p = tmp_path / "cfg.toml"
    p.write_text(TOML_DOC)
    schema_p = tmp_path / "schema.json"
    schema_p.write_text(
        json.dumps(
            {
                "host": "str",
                "port": {"type": "int", "minimum": 1, "maximum": 65535},
                "debug": {"type": "bool"},
                "tags": {"type": "list"},
                "db": {"type": "dict"},
            }
        )
    )
    assert main(["check", str(p), "--schema", str(schema_p)]) == 0


def test_cli_check_toml_invalid_exit_1(tmp_path):
    from configkit.__main__ import main

    p = tmp_path / "cfg.toml"
    p.write_text("host = 5\n")  # host must be a string
    schema_p = tmp_path / "schema.json"
    schema_p.write_text(json.dumps({"host": "str"}))
    assert main(["check", str(p), "--schema", str(schema_p)]) == 1
