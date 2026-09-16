"""Schema primitives and value coercion/validation for configkit.

A schema is a plain dict mapping a key name to a *spec*, which may be:

* a type object (``str``, ``int``, ``float``, ``bool``) -- required, must be
  that type;
* a list/tuple of types -- required, must match at least one;
* a ``Spec`` with rich constraints: defaults, numeric bounds, string length
  and regex patterns, allowed choices, nested dict schemas, and list item
  specs; or
* the dict form ``{"type": X, ...}`` with any of those keys -- handy for
  inline nested schemas.

Coercion is deliberately strict: bools are not accepted as ints (a classic
Python gotcha, since ``bool`` subclasses ``int``), and strings are never
auto-converted to numbers.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Type, Union

__all__ = ["Spec", "ValidationError", "coerce_value", "validate"]


class ValidationError(Exception):
    """Raised when a value fails schema validation. ``path`` identifies the key."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


@dataclasses.dataclass(frozen=True)
class Spec:
    """Declarative constraints for a single config key.

    ``types`` lists the accepted Python types (checked via ``isinstance``).
    ``default`` is applied when the key is absent from the source data;
    ``has_default`` distinguishes an explicit ``default=None`` from "no
    default". Numeric bounds use the Python convention: ``minimum``/
    ``maximum`` are inclusive; string bounds use ``min_length``/
    ``max_length`` characters.
    """

    types: Tuple[Type[Any], ...] = (str,)
    default: Optional[Any] = None
    has_default: bool = False
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None
    choices: Optional[Tuple[Any, ...]] = None
    schema: Optional[Dict[str, Union["Spec", Type[Any], Sequence[Type[Any]]]]] = None
    items: Optional["Spec"] = None

    def describe(self) -> str:
        return "|".join(t.__name__ for t in self.types)


_SPEC_DICT_KEYS = {
    "type": "types",
    "default": "default",
    "minimum": "minimum",
    "maximum": "maximum",
    "min_length": "min_length",
    "max_length": "max_length",
    "pattern": "pattern",
    "choices": "choices",
    "schema": "schema",
    "items": "items",
}


def _spec_from_any(raw: Any, path: str = "<schema>") -> Spec:
    """Normalize a schema entry (type, list of types, dict form, or Spec) to a Spec.

    ``list``/``tuple`` here mean a *collection type* whose elements are all the
    same (e.g. a list of strings), so the list/tuple check must run before the
    generic ``type`` branch -- otherwise ``{"type": list}`` is read as
    "the value must be of type ``list``" rather than "a list with these items".
    """
    if isinstance(raw, Spec):
        return raw
    # A list/tuple of types describes a collection type with those item types.
    if isinstance(raw, (list, tuple)):
        if not all(isinstance(t, type) for t in raw):
            raise ValidationError(path, f"expected types or a spec, got {raw!r}")
        return Spec(types=tuple(raw))
    # ``dict`` / ``str`` / etc. as a bare value type (not a schema entry).
    if isinstance(raw, dict) and "type" in raw:
        kwargs: Dict[str, Any] = {}
        for key, value in raw.items():
            if key not in _SPEC_DICT_KEYS:
                raise ValidationError(path, f"unknown spec option {key!r} in {raw!r}")
            kwargs[_SPEC_DICT_KEYS[key]] = value
        types = kwargs.pop("types", None)
        if types is None:
            raise ValidationError(path, f"spec dict requires a 'type' entry: {raw!r}")
        if isinstance(types, type):
            types = (types,)
        kwargs["types"] = tuple(types)
        if "default" in raw and "has_default" not in kwargs:
            kwargs["has_default"] = True
        if "choices" in kwargs and kwargs["choices"] is not None:
            kwargs["choices"] = tuple(kwargs["choices"])
        if "items" in kwargs and kwargs["items"] is not None:
            kwargs["items"] = _spec_from_any(kwargs["items"], path)
        return Spec(**kwargs)
    if isinstance(raw, type):
        # Bare value types like ``dict`` (no items) or a single type.
        return Spec(types=(raw,))
    raise ValidationError(path, f"unsupported schema entry: {raw!r}")


def _default_for(spec: Spec) -> Any:
    """Implicit default for a list spec that has no explicit one.

    A key with an ``items`` element spec (a list of things) reads as an
    *optional* collection, so when it is absent we default to ``[]`` instead of
    requiring it. Nested-dict specs stay required (their own keys decide what's
    optional), and scalar specs without a ``default`` remain required too.
    """
    if spec.items is not None:
        return []
    return None


def coerce_value(value: Any, spec: Spec, path: str) -> Any:
    """Validate ``value`` against ``spec`` and return it unchanged (or raise)."""
    if value is None:
        raise ValidationError(path, "expected %s, got null" % spec.describe())

    # bool is a subclass of int in Python; a numeric field (int/float) must not
    # silently accept True/False. Check before the isinstance gate, because
    # isinstance(True, (int,)) is True.
    if isinstance(value, bool) and not any(t is bool for t in spec.types):
        raise ValidationError(path, "expected %s, got bool" % spec.describe())

    if not isinstance(value, tuple(spec.types)):
        raise ValidationError(
            path, "expected %s, got %s" % (spec.describe(), type(value).__name__)
        )

    # Numeric bounds only apply to int/float values. Without this guard a str
    # value carrying a minimum (e.g. a bounded-by-mistake string field, or a
    # value that passed the type check before bounds were added) would trip the
    # comparison and raise TypeError on Python 3 ("<" between str and int).
    if isinstance(value, (int, float)):
        if spec.minimum is not None and value < spec.minimum:
            raise ValidationError(path, "%r is below minimum %r" % (value, spec.minimum))
        if spec.maximum is not None and value > spec.maximum:
            raise ValidationError(path, "%r is above maximum %r" % (value, spec.maximum))

    if isinstance(value, str):
        if spec.min_length is not None and len(value) < spec.min_length:
            raise ValidationError(
                path, "length %d below min_length %d" % (len(value), spec.min_length)
            )
        if spec.max_length is not None and len(value) > spec.max_length:
            raise ValidationError(
                path, "length %d above max_length %d" % (len(value), spec.max_length)
            )
        if spec.pattern is not None and re.fullmatch(spec.pattern, value) is None:
            raise ValidationError(path, "%r does not match pattern %r" % (value, spec.pattern))

    if spec.choices is not None and value not in spec.choices:
        raise ValidationError(
            path, "%r not in allowed choices %r" % (value, list(spec.choices))
        )

    if spec.schema is not None and isinstance(value, dict):
        return validate(value, spec.schema, path)

    if spec.items is not None and isinstance(value, (list, tuple)):
        item_spec = _spec_from_any(spec.items, path)
        for i, item in enumerate(value):
            coerce_value(item, item_spec, f"{path}[{i}]")

    return value


def validate(
    data: Dict[str, Any],
    schema: Dict[str, Union[Spec, Type[Any], Sequence[Type[Any]]]],
    path: str = "$",
) -> Dict[str, Any]:
    """Validate (and resolve defaults for) a dict against a schema.

    Returns a new dict with every key present (defaults applied). Unknown
    keys raise ``ValidationError`` -- typo protection is the point of this
    library. Nested dicts are validated recursively and returned as plain
    dicts; attribute access is applied later by the loader.
    """
    if not isinstance(data, dict):
        raise ValidationError(path, "expected a mapping, got %s" % type(data).__name__)

    result: Dict[str, Any] = {}
    for key, raw in schema.items():
        spec = _spec_from_any(raw, path)
        child_path = f"{path}.{key}" if path != "$" else key
        if key in data:
            value = data[key]
        elif spec.has_default:
            value = spec.default
        else:
            implicit = _default_for(spec)
            if implicit is None:
                raise ValidationError(child_path, "required key is missing")
            value = implicit

        result[key] = coerce_value(value, spec, child_path)

    unknown = set(data) - set(schema)
    if unknown:
        raise ValidationError(path, "unknown key(s): %s" % ", ".join(sorted(unknown)))
    return result
