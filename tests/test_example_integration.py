"""Integration test: load the example configs from disk end-to-end.

These read the real files in ``example/`` through the full pipeline --
file I/O, format inference (JSON and YAML), env-var substitution, schema
validation, defaults, and the immutable Config wrapper -- rather than using
inline strings, so they catch breakage that unit tests with synthetic data
miss (e.g. a broken extension check or a YAML-specific parsing bug).
"""

import os
from pathlib import Path

import pytest

from configkit import Config, field, load

EXAMPLES = Path(__file__).resolve().parent.parent / "example"

SCHEMA = {
    "host": str,
    "port": field(int, minimum=1, maximum=65535),
    "workers": field(int, default=2),
    "mode": field(str, choices=("fast", "safe")),
    "tags": {"type": list, "items": {"type": str}},
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


def test_example_json_loads_and_validates():
    cfg = load(str(EXAMPLES / "webapp.json"), SCHEMA)
    assert isinstance(cfg, Config)
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 8080
    assert cfg.workers == 4
    assert cfg.mode == "fast"
    assert cfg.tags == ["web", "api"]
    # nested section, with its own default applied for a key the file omits
    assert cfg.db.user == "app"
    assert cfg.db.pool_size == 10
    assert isinstance(cfg.db, Config)


def test_example_yaml_loads_and_validates():
    cfg = load(str(EXAMPLES / "webapp.yaml"), SCHEMA)
    assert cfg.host == "example.internal"
    assert cfg.port == 9443
    assert cfg.workers == 8
    assert cfg.mode == "safe"
    assert cfg.tags == ["web", "admin"]
    assert cfg.db.user == "app_ro"
    assert cfg.db.pool_size == 25


def test_example_files_exist():
    # Guard against the example dir being accidentally emptied or renamed --
    # if these are missing, the two tests above would fail with a confusing
    # "file not found", so name them explicitly in the error.
    for name in ("webapp.json", "webapp.yaml"):
        assert (EXAMPLES / name).is_file(), f"missing example file: {name}"


def test_example_env_substitution_roundtrip(tmp_path):
    # Write a copy of the JSON example with an env placeholder, load it with
    # an explicit env mapping, and confirm the substitution happened before
    # validation (the substituted value must still satisfy the schema).
    import json

    src = json.loads((EXAMPLES / "webapp.json").read_text())
    src["host"] = "${HOST_OVERRIDE}"
    p = tmp_path / "override.json"
    p.write_text(json.dumps(src))

    cfg = load(str(p), SCHEMA, env={"HOST_OVERRIDE": "injected.example"})
    assert cfg.host == "injected.example"
    # everything else came straight from the real example file
    assert cfg.port == 8080
