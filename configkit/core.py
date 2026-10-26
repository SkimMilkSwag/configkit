"""File loading, schema sugar, and the immutable Config container."""

from __future__ import annotations

import copy
import json
import os
from types import ModuleType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union, Callable, cast

from .schema import Spec, ValidationError, validate

__all__ = ["ConfigError", "Config", "load", "loads", "field"]


class ConfigError(Exception):
    """Raised for I/O and format problems (missing file, bad JSON, ...)."""


_UNSET = object()


def field(
    types: Union[type, Sequence[type], Any] = _UNSET,
    default: Any = None,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
    min_length: Optional[int] = None,
    max_length: Optional[int] = None,
    pattern: Optional[str] = None,
    choices: Optional[Sequence[Any]] = None,
    schema: Optional[Dict[str, Any]] = None,
    items: Optional[Any] = None,
) -> Spec:
    """Build a ``Spec`` with named arguments -- friendlier than positional dataclass.

    The accepted type(s) are optional; when omitted the value must match at
    least one of ``str``/``int``/``float``/``bool`` (anything not otherwise
    constrained). Pass a single type, or a tuple of types, to narrow it::

        schema = {
            "port": field(int, minimum=1, maximum=65535),
            "mode": field(str, choices=("fast", "safe")),
            "retries": field(minimum=0),  # any number, bounded below
        }
    """
    if types is _UNSET:
        # Type omitted: accept the common value types (bool excluded, to keep
        # the bool-vs-int guard strict for numeric bounds) plus container types
        # so a spec with ``items`` or a collection default still validates its
        # contents rather than rejecting the whole value.
        type_tuple: Tuple[type, ...] = (str, int, float, dict, list, tuple)
    elif isinstance(types, type):
        type_tuple = (types,)
    else:
        type_tuple = tuple(types)
    return Spec(
        types=type_tuple,
        default=copy.deepcopy(default),
        has_default=default is not None,
        minimum=minimum,
        maximum=maximum,
        min_length=min_length,
        max_length=max_length,
        pattern=pattern,
        choices=tuple(choices) if choices is not None else None,
        schema=schema,
        items=items,
    )
def _wrap(value: Any) -> Any:
    """Recursively convert nested dicts into ``Config`` objects (lists stay lists).

    A dict becomes a fresh :class:`Config` wrapping it; the caller is responsible
    for not calling this on the mapping a Config is already wrapping (to avoid a
    self-reference). Lists wrap their elements.
    """
    if isinstance(value, dict):
        return Config(value)
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


def _unwrap(value: Any) -> Any:
    """Inverse of :func:`_wrap`: Config objects back to plain dicts/lists."""
    if isinstance(value, Config):
        return {k: _unwrap(v) for k, v in value._data.items()}  # noqa: SLF001
    if isinstance(value, list):
        return [_unwrap(v) for v in value]
    return value


class Config:
    """Immutable, attribute-accessible view of a validated config.

    Nested dicts become nested ``Config`` objects; lists stay plain lists.
    Any attribute access on an unknown name raises ``AttributeError`` with
    the list of valid keys (typo protection, again).
    """

    def __init__(self, data: Dict[str, Any]) -> None:
        # The top-level mapping is stored as-is (this Config object wraps it);
        # each *value* is run through _wrap so nested dicts become nested Configs
        # and lists wrap their elements. One level deep -- no self-reference.
        object.__setattr__(self, "_data", {k: _wrap(v) for k, v in data.items()})

    def __getattr__(self, name: str) -> Any:
        data = object.__getattribute__(self, "_data")
        if name in data:
            return data[name]
        valid = ", ".join(sorted(data)) or "(empty)"
        raise AttributeError(f"no such config key {name!r} (valid keys: {valid})")

    def __getitem__(self, name: str) -> Any:
        try:
            return getattr(self, name)
        except AttributeError as exc:
            raise KeyError(name) from exc

    def __contains__(self, name: str) -> bool:
        return name in object.__getattribute__(self, "_data")

    def to_dict(self) -> Dict[str, Any]:
        """Return a deep plain-dict copy (Config objects reverted to dicts)."""
        return cast(Dict[str, Any], _unwrap(object.__getattribute__(self, "_data")))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Config):
            return self.to_dict() == other.to_dict()
        if isinstance(other, dict):
            return self.to_dict() == other
        return NotImplemented

    def __repr__(self) -> str:
        return f"Config({self.to_dict()!r})"


def _read_source(path: str) -> str:
    if not os.path.exists(path):
        raise ConfigError(f"config file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# --- TOML parsing ------------------------------------------------------------
#
# Python 3.11+ ships tomllib in the stdlib; earlier interpreters (this box is
# 3.10) use tomli, which has the same API and is already installed here. When
# neither is available we fall back to a small built-in parser that covers the
# TOML subset config files actually need: [table] headers, bare keys, quoted
# strings, integers, floats, booleans, inline arrays, and dotted-key nesting.


def _toml_lib() -> Optional[ModuleType]:
    """Return an available TOML library module (tomllib or tomli), else None."""
    try:
        # tomllib is 3.11+ stdlib; mypy resolves it against the pyproject
        # python_version target (3.10 here) so it needs the ignore.
        import tomllib  # type: ignore[import-not-found]

        return cast(ModuleType, tomllib)
    except ImportError:
        pass
    try:
        import tomli

        return tomli
    except ImportError:
        return None


def _toml_unquote(token: str) -> str:
    """Strip surrounding quotes from a TOML string token (basic or literal)."""
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        # basic string: handle the escapes a config file realistically uses
        body = token[1:-1]
        out: List[str] = []
        i = 0
        while i < len(body):
            ch = body[i]
            if ch == "\\" and i + 1 < len(body):
                nxt = body[i + 1]
                mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "b": "\b", "f": "\f"}
                if nxt in mapping:
                    out.append(mapping[nxt])
                    i += 2
                    continue
            out.append(ch)
            i += 1
        return "".join(out)
    if len(token) >= 2 and token[0] == "'" and token[-1] == "'":
        return token[1:-1]  # literal string: no escapes
    raise ConfigError(f"invalid TOML string {token!r}")


def _toml_parse_value(token: str) -> Any:
    """Parse a scalar or inline-array TOML value from its raw text form."""
    token = token.strip()
    if token.startswith("["):
        if not token.endswith("]"):
            raise ConfigError(f"unterminated array in {token!r}")
        inner = token[1:-1].strip()
        if not inner:
            return []
        parts: List[str] = []
        depth = 0
        current = ""
        for ch in inner:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            parts.append(current.strip())
        return [_toml_parse_value(p) for p in parts]
    if token.startswith('"') or token.startswith("'"):
        return _toml_unquote(token)
    if token == "true":
        return True
    if token == "false":
        return False
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    raise ConfigError(f"unsupported TOML value {token!r} (mini-parser needs a scalar or array)")


def _toml_set_path(doc: Dict[str, Any], parts: List[str], value: Any) -> None:
    cur = doc
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            raise ConfigError(f"invalid TOML: key {part!r} used as table and value")
        cur = nxt
    cur[parts[-1]] = value


def _mini_parse_toml(text: str) -> Dict[str, Any]:
    """Parse the TOML subset config files use (see module comment above)."""
    doc: Dict[str, Any] = {}
    table_parts: List[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        # strip comments outside of strings: a '#' starts a comment when it is
        # at line start (after whitespace) or follows whitespace and is not
        # inside quotes — track quote state across the line for that.
        line = ""
        in_str: Optional[str] = None
        i = 0
        while i < len(raw):
            ch = raw[i]
            if in_str:
                line += ch
                if ch == "\\" and in_str == '"':
                    if i + 1 < len(raw):
                        line += raw[i + 1]
                        i += 2
                        continue
                elif ch == in_str:
                    in_str = None
            else:
                if ch in ('"', "'"):
                    in_str = ch
                    line += ch
                elif ch == "#":
                    break
                else:
                    line += ch
            i += 1
        stripped = line.strip()
        if not stripped:
            continue
        # dotted keys (a.b.c = 1) are processed BEFORE [table] headers so that
        # the nested dicts they create already exist when a header like
        # [a.b] is later resolved against them (TOML allows this order).
        if "=" in stripped and not (stripped.startswith("[") and stripped.endswith("]")):
            key_part, _, value_part = stripped.partition("=")
            key_parts = [p.strip() for p in key_part.strip().split(".")]
            if any(not p for p in key_parts):
                raise ConfigError(f"line {lineno}: empty segment in key")
            value = _toml_parse_value(value_part.strip())
            # keys are placed into the currently-open table (empty at top level)
            target: Dict[str, Any] = doc
            for part in table_parts:
                nxt = target.get(part)
                if not isinstance(nxt, dict):
                    raise ConfigError(f"line {lineno}: key path passes through non-table")
                target = nxt
            # Pre-walk the key path BEFORE writing anything: an implicit
            # super-table is legal (a.b.c = 1 before any [a] header), but the
            # whole path must be walkable — a.b = 1 then a.c.d = 2 walks
            # through the existing scalar 1, which collides. Doing this up
            # front keeps the doc clean when we reject the line (no half-
            # created empty tables left behind).
            probe: Dict[str, Any] = target
            for part in key_parts[:-1]:
                nxt = probe.get(part)
                if nxt is not None and not isinstance(nxt, dict):
                    raise ConfigError(
                        f"line {lineno}: key path passes through non-table ({part!r})"
                    )
                probe = nxt if isinstance(nxt, dict) else {}
            # Now create the implicit super-tables and write the value.
            for part in key_parts[:-1]:
                nxt = target.get(part)
                if nxt is None:
                    target[part] = {}
                    nxt = target[part]
                target = nxt
            target[key_parts[-1]] = value
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            name = stripped[1:-1].strip()
            if not name:
                raise ConfigError(f"line {lineno}: empty table header")
            # [a.b.c] style headers are dotted paths; bare single names too
            table_parts = [p.strip() for p in name.split(".")]
            cur = doc
            for part in table_parts:
                if not part:
                    raise ConfigError(f"line {lineno}: empty segment in table header")
                nxt = cur.get(part)
                if nxt is None:
                    cur[part] = {}
                    cur = cur[part]
                elif isinstance(nxt, dict):
                    cur = nxt
                else:
                    raise ConfigError(f"line {lineno}: table header {name!r} collides with a value")
            continue
        # leftover non-empty line that is neither key=value nor a [table]
        raise ConfigError(f"line {lineno}: expected key = value or [table], got {stripped!r}")
    return doc


def _parse_toml(text: str) -> Dict[str, Any]:
    """Parse TOML text using tomllib/tomli when available, else the mini parser."""
    lib = _toml_lib()
    if lib is not None:
        try:
            data = lib.loads(text)
        except Exception as exc:  # tomllib.TOMLDecodeError lives inside lib
            raise ConfigError(f"invalid TOML: {exc}") from exc
        return cast(Dict[str, Any], data)
    return _mini_parse_toml(text)


def _resolve(data: Any, env: Union[Mapping[str, str], Callable[[str], Optional[str]], None]) -> Any:
    """Resolve ${VAR} placeholders in the raw document before validation."""
    try:
        from .env import resolve_env
    except ImportError as exc:  # pragma: no cover - stdlib-only failure
        raise ConfigError(f"cannot resolve environment variables: {exc}") from exc
    return resolve_env(data, env=env)


def loads(
    text: str,
    schema: Mapping[str, Union[Spec, type, Sequence[type]]],
    fmt: str = "json",
    env: Optional[Union[Mapping[str, str], Callable[[str], Optional[str]]]] = None,
) -> Config:
    """Parse ``text`` (JSON, YAML or TOML) and validate it against ``schema``.

    ``fmt`` is ``"json"``, ``"yaml"`` or ``"toml"``. When ``env`` is given (a
    mapping or a callable used as the variable lookup; defaults to
    ``os.environ``), ``${VAR}`` and ``${VAR:default}`` placeholders in any
    string value are substituted before validation. Returns a validated,
    immutable :class:`Config`.
    """
    if fmt not in ("json", "yaml", "toml"):
        raise ConfigError(f"unsupported format {fmt!r} (use 'json', 'yaml' or 'toml')")
    if fmt == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("top-level value must be a mapping/object")
    elif fmt == "toml":
        try:
            data = _parse_toml(text)
        except ConfigError as exc:
            raise exc
        except Exception as exc:
            raise ConfigError(f"invalid TOML: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("top-level value must be a mapping/object")
    else:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - depends on env
            raise ConfigError("PyYAML is required to load YAML files") from exc
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("top-level value must be a mapping/object")

    if env is not None:
        data = _resolve(data, env)
    resolved = validate(data, schema, "$")
    return Config(resolved)


def load(
    path: str,
    schema: Mapping[str, Union[Spec, type, Sequence[type]]],
    fmt: Union[str, None] = None,
    env: Optional[Union[Mapping[str, str], Callable[[str], Optional[str]]]] = None,
) -> Config:
    """Load a config file (``.json``, ``.yaml``/``.yml`` or ``.toml``) and validate it.

    The format is inferred from the extension unless ``fmt`` is given
    explicitly. When ``env`` is given, ``${VAR}`` placeholders in string
    values are substituted before validation. Returns a validated, immutable
    :class:`Config`.
    """
    if fmt is None:
        if path.endswith((".yaml", ".yml")):
            fmt = "yaml"
        elif path.endswith(".toml"):
            fmt = "toml"
        else:
            fmt = "json"
    return loads(_read_source(path), schema, fmt=fmt, env=env)
