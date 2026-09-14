"""File loading, schema sugar, and the immutable Config container."""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, List, Sequence, Union

from .schema import Spec, ValidationError, validate

__all__ = ["ConfigError", "Config", "load", "loads", "field"]


class ConfigError(Exception):
    """Raised for I/O and format problems (missing file, bad JSON, ...)."""


_UNSET = object()


def field(
    types=_UNSET,
    default: Any = None,
    minimum: Any = None,
    maximum: Any = None,
    min_length: Any = None,
    max_length: Any = None,
    pattern: Any = None,
    choices: Any = None,
    schema: Any = None,
    items: Any = None,
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
        type_tuple: tuple = (str, int, float, dict, list, tuple)
    elif isinstance(types, type):
        type_tuple = (types,)  # type: ignore[assignment]
    else:
        type_tuple = tuple(types)  # type: ignore[arg-type]
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
        return _unwrap(object.__getattribute__(self, "_data"))

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


def loads(
    text: str,
    schema: Dict[str, Union[Spec, type, Sequence[type]]],
    fmt: str = "json",
) -> Config:
    """Parse ``text`` (JSON or YAML) and validate it against ``schema``.

    ``fmt`` is ``"json"`` or ``"yaml"``. Returns a validated, immutable
    :class:`Config`.
    """
    if fmt not in ("json", "yaml"):
        raise ConfigError(f"unsupported format {fmt!r} (use 'json' or 'yaml')")
    if fmt == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid JSON: {exc}") from exc
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

    resolved = validate(data, schema, "$")
    return Config(resolved)


def load(
    path: str,
    schema: Dict[str, Union[Spec, type, Sequence[type]]],
    fmt: Union[str, None] = None,
) -> Config:
    """Load a config file (``.json`` or ``.yaml``/``.yml``) and validate it.

    The format is inferred from the extension unless ``fmt`` is given
    explicitly. Returns a validated, immutable :class:`Config`.
    """
    if fmt is None:
        if path.endswith((".yaml", ".yml")):
            fmt = "yaml"
        else:
            fmt = "json"
    return loads(_read_source(path), schema, fmt=fmt)
