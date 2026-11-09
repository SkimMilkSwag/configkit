"""configkit CLI -- validate or merge config files from the command line.

Usage::

    # check (default; also callable as `configkit check`)
    python -m configkit config.yaml --schema schema.yaml
    python -m configkit config.json --schema schema.json

    # merge several config files (later wins, deep merge); prints the result
    python -m configkit merge base.yaml overrides.yaml
    python -m configkit merge a.json b.yaml --format yaml

    # diff two config files; one line per change (added / removed / replaced)
    python -m configkit diff base.yaml deployed.yaml

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
from typing import Any, Dict, List, Optional, Tuple, cast

from .core import ConfigError
from .env import EnvError, load_env_file
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
    fmt = None
    if path.endswith((".yaml", ".yml")):
        fmt = "yaml"
    elif path.endswith(".toml"):
        fmt = "toml"
    else:
        fmt = "json"
    if not os.path.exists(path):
        raise ConfigError(f"config file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if fmt == "toml":
        try:
            from .core import _parse_toml  # local parser lives with the loader
        except ImportError as exc:
            raise ConfigError(f"cannot parse TOML: {exc}") from exc
        try:
            data = _parse_toml(text)
        except ConfigError as exc:
            raise exc
        except Exception as exc:
            raise ConfigError(f"invalid TOML: {exc}") from exc
    elif fmt == "json":
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


def check_file(
    config_path: str,
    schema: Dict[str, Spec],
    env: "Optional[Dict[str, str]]" = None,
) -> List[str]:
    """Validate ``config_path`` against ``schema``; return problem lines.

    An empty list means the file is valid. Every failing key is reported —
    not just the first one — so a bad file lists all of its problems in a
    single run (each line is ``<json-path>: <message>``, linter-style, ready
    for CI logs). Problems are ordered by schema key order, with the unknown-
    key sweep last; a document that fails to parse or an unresolvable
    ``${VAR}`` placeholder produces a single problem line for the file.

    When ``env`` (a KEY -> value mapping from an --env-file) is given,
    ``${VAR}`` placeholders in the config are resolved against it instead of
    the ambient environment before validation runs.

    Every returned line is ``<json-path>: <message>``; a single-file run
    therefore prints exactly the historical format, and :func:`check_files`
    re-prefixes each line with its file name when several files are checked.
    """
    from .core import _load_raw_dict  # avoid circular import at module top

    try:
        data = _load_raw_dict(config_path)
    except ConfigError as exc:
        return [f"{config_path}: {exc}"]
    if env is not None:
        from .env import resolve_env as _resolve_env
        data = _resolve_env(data, env)
    from .schema import collect_errors
    return [f"{p}: {m}" for p, m in collect_errors(data, schema)]


def check_files(
    config_paths: "List[str]",
    schema: Dict[str, Spec],
    env: "Optional[Dict[str, str]]" = None,
) -> List[str]:
    """Validate each file in ``config_paths`` against ``schema``; return all problem lines.

    Files are checked independently and in the given order: a parse failure
    or an unresolvable placeholder in one file does not stop the others from
    being validated, so a batch run reports every problem across the whole
    set in a single pass (each line is ``<file>: <key>: <message>``). An
    empty list means every file is valid. A single-file batch returns exactly
    what :func:`check_file` returns for that file (no file prefix), so the
    historical single-file output is byte-for-byte unchanged. See
    :func:`check_file` for the per-file rules.
    """
    if len(config_paths) == 1:
        return check_file(config_paths[0], schema, env=env)
    lines: List[str] = []
    for path in config_paths:
        lines.extend(f"{path}: {line}" for line in check_file(path, schema, env=env))
    return lines


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


def _run_check(
    config_paths: "List[str]",
    schema_path: str,
    as_json: bool = False,
    env_file: "str | None" = None,
) -> int:
    # All output for a multi-file run is attributed to the first file in the
    # batch (the primary target); per-problem lines carry their own file name.
    config_path = config_paths[0]
    env: "Dict[str, Any] | None" = None
    if env_file is not None:
        try:
            env = load_env_file(env_file)
        except EnvError as exc:
            message = str(exc.args[0])
            if not as_json:
                print(f"{message}", file=sys.stderr)
            else:
                print(_json.dumps({"file": config_path, "ok": False,
                                   "problems": [message]}))
            return 1
    try:
        schema = _load_schema(schema_path)
    except ValidationError as exc:
        if not as_json:
            print(f"{exc.path}: {exc.message}", file=sys.stderr)
        else:
            print(_json.dumps({"file": config_path, "ok": False,
                               "problems": [f"{exc.path}: {exc.message}"]}))
        return 1
    except ConfigError as exc:
        if not as_json:
            print(f"{schema_path}: {exc}", file=sys.stderr)
        else:
            print(_json.dumps({"file": config_path, "ok": False,
                               "problems": [f"{schema_path}: {exc}"]}))
        return 1

    try:
        problems = check_files(config_paths, schema, env=env)
    except EnvError as exc:
        # A ${VAR} placeholder whose variable is missing from the (file or
        # ambient) environment: surface it like any other problem line.
        message = str(exc.args[0])
        if not as_json:
            print(f"{config_path}: {message}", file=sys.stderr)
        else:
            print(_json.dumps({"file": config_path, "ok": False,
                               "problems": [f"{config_path}: {message}"]}))
        return 1
    if as_json:
        # stdout-only, machine-consumable: one JSON object regardless of the
        # exit code, so `configkit --json ... | jq` works in CI pipelines.
        print(_json.dumps({"file": config_path, "ok": not problems,
                           "problems": problems}, indent=2))
        return 1 if problems else 0
    if not problems:
        # Single file keeps the historical one-line form; a batch reports the
        # whole set as OK in one line (naming every file) instead of one
        # indistinguishable "OK" per file.
        if len(config_paths) == 1:
            print(f"{config_path}: OK")
        else:
            print(", ".join(config_paths) + ": OK")
        return 0
    for line in problems:
        print(line, file=sys.stderr)
    return 1


# --- diff --------------------------------------------------------------------


def _format_value(value: Any) -> str:
    """Render a config value for diff lines.

    Strings are shown bare (no repr quotes), so output stays human-readable;
    everything else uses ``repr``, which is unambiguous for numbers, booleans,
    ``None``, lists and dicts (nested dicts render with the same compact form
    a JSON consumer would see).
    """
    if isinstance(value, str):
        return value
    return repr(value)


def diff_configs(a: Any, b: Any, prefix: str = "") -> List[str]:
    """Diff two config documents as compact one-line-per-change strings.

    Lines are ordered deterministically (sorted paths); ``prefix`` is a
    dotted-path fragment prepended to every key of the change, used to report
    nested locations correctly::

        host: old -> new
        db.pool_size: 5 -> 10
        debug: added (true)
        timeout: removed (30)
        tags: replaced ([a, b] -> [c])

    Two mappings are walked key by key (so nested sections report their own
    changes instead of a wholesale ``replaced``); any other value change is a
    single ``replaced`` line.  ``a`` is the left/base document and ``b`` the
    right/override one, matching ``merge``'s argument order.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        lines: List[str] = []
        for key in sorted(set(a) | set(b)):
            path = f"{prefix}.{key}" if prefix else key
            in_a, in_b = key in a, key in b
            if in_a and in_b:
                lines.extend(diff_configs(a[key], b[key], path))
            elif in_b:
                lines.append(f"{path}: added ({_format_value(b[key])})")
            else:
                lines.append(f"{path}: removed ({_format_value(a[key])})")
        return lines
    if a == b:
        return []
    return [f"{prefix}: replaced ({_format_value(a)} -> {_format_value(b)})"]


def _run_diff(paths: "List[str]") -> int:
    try:
        docs = [_read_raw(path) for path in paths]
    except ConfigError as exc:
        print(f"configkit: {exc}", file=sys.stderr)
        return 1
    if len(docs) != 2:
        print("configkit: diff takes exactly two config files", file=sys.stderr)
        return 1
    lines = diff_configs(docs[0], docs[1])
    if not lines:
        print(f"{paths[0]} and {paths[1]} are identical")
        return 0
    for line in lines:
        print(line)
    print(f"{len(lines)} change(s)")
    return 0


def _iter_keys(doc: Any, prefix: str = "") -> List[str]:
    """Return all dotted key paths in a config document, sorted.

    Lists are traversed by index (``a.b.0.c``) so nested structures inside
    arrays are covered. Scalars and empty containers contribute no keys of
    their own — only dict members produce paths. The result is sorted so
    output is stable for scripting / tab-completion consumers.
    """
    out: List[str] = []
    if isinstance(doc, dict):
        for key in doc:
            path = f"{prefix}.{key}" if prefix else str(key)
            out.append(path)
            out.extend(_iter_keys(doc[key], path))
    elif isinstance(doc, list):
        for i, item in enumerate(doc):
            out.extend(_iter_keys(item, f"{prefix}.{i}" if prefix else str(i)))
    return sorted(out)


def _run_keys(paths: List[str], with_values: bool = False) -> int:
    """List all dotted key paths found in the given config file(s).

    By default each path is printed bare (one per line, sorted), ready for
    tab-completion or piping back into ``get``. With ``--values`` each line
    becomes ``path = value`` (values rendered with the same compact form as
    ``get``) so a single call can dump an entire config in scriptable form.
    """
    for path in paths:
        try:
            doc = _read_raw(path)
        except ConfigError as exc:
            print(f"configkit: {exc}", file=sys.stderr)
            return 1
        for key_path in _iter_keys(doc):
            if with_values:
                value = lookup_path(doc, key_path)
                print(f"{key_path} = {_render_get_value(value)}")
            else:
                print(key_path)
    return 0


def lookup_path(doc: Any, dotted: str) -> Any:
    """Walk ``dotted`` (e.g. ``a.b.0.c``) into ``doc`` and return the value.

    Dict members are walked by key name; list members by integer index (so a
    path is exactly the form :func:`_iter_keys` prints, which means
    ``configkit keys`` output pipes straight back into ``configkit get``).
    Raises ``KeyError`` for a missing dict key and ``IndexError`` for an
    out-of-range or non-numeric list index.
    """
    cur: Any = doc
    for segment in dotted.split("."):
        if isinstance(cur, dict):
            if segment not in cur:
                raise KeyError(segment)
            cur = cur[segment]
        elif isinstance(cur, list):
            try:
                idx = int(segment)
            except ValueError as exc:
                raise IndexError(f"{segment!r} is not a list index") from exc
            if idx < -len(cur) or idx >= len(cur):
                raise IndexError(f"list index {idx} out of range")
            cur = cur[idx]
        else:
            # scalar (or None) where a container was expected: the path
            # descends past a leaf, so report the type mismatch cleanly
            raise KeyError(
                f"{segment!r}: cannot descend into {type(cur).__name__}"
            )
    return cur


def _render_get_value(value: Any) -> str:
    """Render one value for ``get`` output.

    Strings print bare (the common case — `configkit get cfg.yaml db.host | xargs`
    must not quote its argument); everything else uses the same compact form
    as ``diff``'s `_format_value` so numbers, booleans, None and containers are
    unambiguous for a shell consumer.
    """
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    return repr(value)


def _run_get(paths: List[str], dotted: str) -> int:
    """Print the value at ``dotted`` in each given config file.

    Exit 0 when every lookup succeeds; 1 on any I/O/format problem or a path
    that does not resolve (missing key, bad index, descent into a scalar) —
    one line per failing location on stderr so it is obvious which file/path
    broke in a multi-file call.
    """
    status = 0
    for path in paths:
        try:
            doc = _read_raw(path)
        except ConfigError as exc:
            print(f"configkit: {exc}", file=sys.stderr)
            return 1
        try:
            value = lookup_path(doc, dotted)
        except KeyError as exc:
            print(
                f"{path}: no key {dotted!r} (missing segment {exc.args[0]!r})",
                file=sys.stderr,
            )
            status = 1
            continue
        except IndexError as exc:
            print(f"{path}: bad index in {dotted!r}: {exc.args[0]}", file=sys.stderr)
            status = 1
            continue
        print(_render_get_value(value))
    return status


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
    if not argv or argv[0] not in ("check", "merge", "diff", "keys", "get"):
        argv = ["check"] + argv

    parser = argparse.ArgumentParser(
        prog="python -m configkit",
        description="Validate, merge, or diff JSON/YAML config files.",
    )
    subparsers = parser.add_subparsers(dest="command")

    check_p = subparsers.add_parser(
        "check", help="validate a config file against a schema (default command)"
    )
    check_p.add_argument(
        "configs", nargs="+", metavar="CONFIG",
        help="config file(s) to check against the schema (one or more; every "
             "file is validated in the same run and each problem line is "
             "prefixed with its file name)",
    )
    check_p.add_argument("--schema", required=True, help="path to the schema file (JSON or YAML)")
    check_p.add_argument(
        "--json", dest="as_json", action="store_true",
        help='print the result as a JSON object {"file", "ok", "problems"} on stdout '
             "instead of the human-readable lines (exit codes unchanged)",
    )
    check_p.add_argument(
        "--env-file", metavar="PATH", default=None,
        help="KEY=VALUE file whose variables resolve ${VAR} placeholders in the "
             "config instead of the ambient environment (comments and blank lines "
             "allowed; quoted values stripped)",
    )

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

    diff_p = subparsers.add_parser(
        "diff",
        help="print what changed between two config files as a compact list",
    )
    diff_p.add_argument(
        "configs", nargs=2, metavar="CONFIG",
        help="two config files to compare (left is the base, right the override)",
    )

    keys_p = subparsers.add_parser(
        "keys",
        help="list all dotted key paths in a config file (one per line) for scripting",
    )
    keys_p.add_argument(
        "configs", nargs="+", metavar="CONFIG",
        help="config file(s) to list keys from",
    )
    keys_p.add_argument(
        "--values", action="store_true",
        help="print 'path = value' lines (values rendered like the get "
             "command) instead of bare paths",
    )

    get_p = subparsers.add_parser(
        "get",
        help="print the value at a dotted path (e.g. db.host or server.tls.0)",
    )
    get_p.add_argument(
        "configs", nargs="+", metavar="CONFIG",
        help="config file(s) to look up in",
    )
    get_p.add_argument(
        "key", metavar="DOTTED.KEY",
        help="dotted path into the document; list members are addressed by "
             "index (the same paths `keys` prints)",
    )

    args = parser.parse_args(argv)

    if getattr(args, "command", None) == "merge":
        return _run_merge(list(args.configs), args.fmt)
    if getattr(args, "command", None) == "diff":
        return _run_diff(list(args.configs))
    if getattr(args, "command", None) == "keys":
        return _run_keys(list(args.configs), with_values=getattr(args, "values", False))
    if getattr(args, "command", None) == "get":
        return _run_get(list(args.configs), args.key)
    return _run_check(list(args.configs), args.schema, as_json=getattr(args, "as_json", False),
                      env_file=getattr(args, "env_file", None))


if __name__ == "__main__":
    sys.exit(main())
