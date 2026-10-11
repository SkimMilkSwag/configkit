"""configkit CLI -- validate or merge config files from the command line.

Usage::

    # check (default; also callable as `configkit check`)
    python -m configkit config.yaml --schema schema.yaml
    python -m configkit config.json --schema schema.json

    # merge several config files (later wins, deep merge); prints the result
    python -m configkit merge base.yaml overrides.yaml
    python -m configkit merge a.json b.yaml --format yaml

The check command validates a target file against a schema: the schema file is
itself JSON or YAML (by extension) describing the expected shape of the target;
each top-level key maps to a spec entry in any form the loader accepts (a type,
a tuple of types, a dict form, or a pre-built ``Spec``).

Exit codes: 0 on success (``<config>: OK`` for check, the merged document on
stdout for merge), 1 on any I/O or validation problem -- one line per problem
on stderr, so the output drops straight into CI logs.
"""

from __future__ import annotations

import argparse
import copy
import json as _json
import os
import sys
from typing import Any, Dict, List, cast

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


# --- merge -------------------------------------------------------------------


def deep_merge(base: Any, override: Any) -> Any:
    """Recursively merge two plain documents; ``override`` wins.

    Dicts are merged key-by-key (so a nested section present in both is
    combined, not clobbered); any non-dict value — lists, scalars, and dicts
    with different shapes on the same side — replaces the base value
    wholesale. The inputs are not mutated: a new document is returned.
    """
    if isinstance(base, dict) and isinstance(override, dict):
        merged: Dict[str, Any] = {k: o for k, o in override.items()}
        for k, b in base.items():
            if k in merged:
                merged[k] = deep_merge(b, merged[k])
            else:
                merged[k] = copy.deepcopy(b)
        return merged
    return copy.deepcopy(override)


def merge_configs(paths: "List[str]") -> Dict[str, Any]:
    """Read the raw documents at ``paths`` and merge them left to right.

    Later files override earlier ones (deep merge for mappings, replace
    otherwise). Raises ``ConfigError`` on I/O or format problems; a non-dict
    top level in any file is an error because merging needs mappings.
    """
    if not paths:
        raise ConfigError("merge: at least one config file is required")
    merged: Any = {}
    for path in paths:
        raw = _read_raw(path)
        merged = deep_merge(merged, raw)
    return cast(Dict[str, Any], merged)


def render_merged(doc: Dict[str, Any], fmt: str) -> str:
    """Serialize a merged document as JSON (pretty, 2-space) or YAML.

    ``fmt`` is ``"json"`` or ``"yaml"``; anything else raises ``ConfigError``.
    """
    if fmt == "json":
        return _json.dumps(doc, indent=2) + "\n"
    if fmt == "yaml":
        try:
            import yaml
        except ImportError as exc:
            raise ConfigError("PyYAML is required to emit YAML") from exc
        return cast(str, yaml.safe_dump(doc, sort_keys=False))
    raise ConfigError(f"unsupported output format {fmt!r} (use 'json' or 'yaml')")


def _run_check(config_path: str, schema_path: str) -> int:
    try:
        schema = _load_schema(schema_path)
    except ValidationError as exc:
        print(f"{exc.path}: {exc.message}", file=sys.stderr)
        return 1
    except ConfigError as exc:
        print(f"{schema_path}: {exc}", file=sys.stderr)
        return 1

    problems = check_file(config_path, schema)
    if not problems:
        print(f"{config_path}: OK")
        return 0
    for line in problems:
        print(line, file=sys.stderr)
    return 1


def _run_merge(paths: List[str], fmt: str) -> int:
    try:
        merged = merge_configs(paths)
    except ConfigError as exc:
        print(f"configkit: {exc}", file=sys.stderr)
        return 1
    try:
        sys.stdout.write(render_merged(merged, fmt))
    except ConfigError as exc:
        print(f"configkit: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: "List[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # If no subcommand is given, treat the args as `check` so the historical
    # form `python -m configkit cfg.yaml --schema s.yaml` keeps working.
    if not argv or argv[0] not in ("check", "merge"):
        argv = ["check"] + argv

    parser = argparse.ArgumentParser(
        prog="python -m configkit",
        description="Validate or merge JSON/YAML config files.",
    )
    subparsers = parser.add_subparsers(dest="command")

    check_p = subparsers.add_parser(
        "check", help="validate a config file against a schema (default command)"
    )
    check_p.add_argument("config", help="path to the config file to check")
    check_p.add_argument("--schema", required=True, help="path to the schema file (JSON or YAML)")

    merge_p = subparsers.add_parser(
        "merge", help="merge config files (later wins, deep merge); print the result"
    )
    merge_p.add_argument("configs", nargs="+", help="config files to merge, left to right")
    merge_p.add_argument(
        "--format",
        dest="fmt",
        default="json",
        choices=["json", "yaml"],
        help="output format (default: json)",
    )

    args = parser.parse_args(argv)

    if getattr(args, "command", None) == "merge":
        return _run_merge(list(args.configs), args.fmt)
    return _run_check(args.config, args.schema)


if __name__ == "__main__":
    sys.exit(main())
