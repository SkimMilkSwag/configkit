"""configkit CLI -- validate a config file against a schema.

Usage::

    python -m configkit config.yaml --schema schema.yaml
    python -m configkit config.json --schema schema.json

The schema file is itself JSON or YAML (by extension) describing the expected
shape of the target; each top-level key maps to a spec entry in any form the
loader accepts (a type, a tuple of types, a dict form, or a pre-built ``Spec``).
The target is then validated against that schema.

Exit codes: 0 when the config is valid (prints ``<config>: OK``), 1 on any
I/O or validation problem -- one line per problem on stderr, so the output
drops straight into CI logs.
"""

from __future__ import annotations

import argparse
import json as _json
import os
import sys
from typing import Any, Dict, List

from .core import ConfigError
from .schema import Spec, ValidationError, _spec_from_any

# Type-name string -> Python type. YAML/JSON schema files reference types by
# name ("int", "str", ...) rather than as live objects, so the CLI resolves
# these strings to real type objects before handing the spec to validate().
_TYPE_NAMES = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "dict": dict,
    "list": list,
    "tuple": tuple,
    "type": type,
    "object": object,
    "any": object,
}


def _resolve_type_names(value: Any) -> Any:
    """Replace bare type-name strings ("int", "str", ...) with real types.

    Applied recursively to the raw schema document so that
    ``{"type": "int"}`` becomes ``{"type": int}``` before ``_spec_from_any``
    sees it. Strings that are not recognised type names pass through unchanged
    (they may be patterns, defaults, or other literal values).
    """
    if isinstance(value, str) and value in _TYPE_NAMES:
        return _TYPE_NAMES[value]
    if isinstance(value, dict):
        return {k: _resolve_type_names(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_type_names(v) for v in value]
    return value


def _read_raw(path: str) -> Dict[str, Any]:
    """Parse a JSON or YAML file and return its top-level mapping (no schema).

    Format is inferred from the extension. Raises ``ConfigError`` on I/O or
    parse problems; raises ``ConfigError`` if the top level is not a dict.
    """
    fmt = "yaml" if path.endswith((".yaml", ".yml")) else "json"
    if not os.path.exists(path):
        raise ConfigError(f"config file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if fmt == "json":
        try:
            data = _json.loads(text)
        except _json.JSONDecodeError as exc:
            raise ConfigError(f"invalid JSON: {exc}") from exc
    else:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ConfigError("PyYAML is required to load YAML files") from exc
        try:
            data = yaml.safe_load(text)
        except Exception as exc:
            raise ConfigError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top-level value must be a mapping/object")
    return data


def _load_schema(path: str) -> Dict[str, Spec]:
    """Read a schema file and normalise its entries into ``Spec`` objects.

    Raises ``ValidationError`` (with the offending key's path) when an entry
    is not a recognised spec form, and ``ConfigError`` on I/O/format problems.
    """
    raw = _read_raw(path)
    resolved = _resolve_type_names(raw)
    schema: Dict[str, Spec] = {}
    for key, entry in resolved.items():
        spec = _spec_from_any(entry, f"$.{key}")
        schema[key] = spec
    return schema


def check_file(config_path: str, schema: Dict[str, Spec]) -> List[str]:
    """Validate ``config_path`` against ``schema``; return problem lines.

    An empty list means the file is valid. Each line is ``<json-path>:
    <message>`` -- linter-style, one per failure, ready for CI logs.
    """
    from .core import load  # avoid circular import at module top

    try:
        load(config_path, schema)
        return []
    except ValidationError as exc:
        return [f"{exc.path}: {exc.message}"]
    except ConfigError as exc:
        return [f"{config_path}: {exc}"]


def main(argv: "List[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m configkit",
        description="Validate a JSON or YAML config file against a schema.",
    )
    parser.add_argument("config", help="path to the config file to check")
    parser.add_argument(
        "--schema",
        required=True,
        help="path to the schema file (JSON or YAML)",
    )
    args = parser.parse_args(argv)

    try:
        schema = _load_schema(args.schema)
    except ValidationError as exc:
        print(f"{exc.path}: {exc.message}", file=sys.stderr)
        return 1
    except ConfigError as exc:
        print(f"{args.schema}: {exc}", file=sys.stderr)
        return 1

    problems = check_file(args.config, schema)
    if not problems:
        print(f"{args.config}: OK")
        return 0
    for line in problems:
        print(line, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
