"""Environment-variable substitution for config values.

Values may contain ``${VAR}`` placeholders that are resolved from the process
environment before validation runs, so a secret or a machine-specific setting
can live in the environment instead of the file:

* ``${VAR}`` -- replaced with the value of ``VAR``; raises
  :class:`EnvError` when the variable is unset (a typo'd name should fail
  loudly rather than leak through as an empty string).
* ``${VAR:default}`` -- replaced with the value of ``VAR`` when set, otherwise
  with ``default``. The default is a literal string; nested or multiple
  placeholders in one value are each resolved left to right.

Resolution is applied recursively to every value in the raw document (string
values in lists and nested mappings included) before any schema validation,
so a substituted value must pass the same type/bound checks as a literal one.
Non-string values (numbers, booleans, ``null``) pass through untouched.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Mapping, Optional, Union

__all__ = ["EnvError", "resolve_env"]

# A placeholder is ${ NAME } or ${ NAME : default }. The name must start with a
# letter/underscore (mirroring shell convention) and may continue with letters,
# digits, or underscores. The default runs to the matching closing brace; one
# level of nesting is allowed inside it so that ${HOST:${FALLBACK}} parses as a
# placeholder whose default is the string "${FALLBACK}" -- enough for the common
# "fall back to another variable's value" pattern without full bracket matching.
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^{}]*(?:\$\{[^{}]*\})?[^{}]*))?\}")


class EnvError(Exception):
    """Raised when a ``${VAR}`` placeholder references an unset variable."""

    def __init__(self, var: str, value: str) -> None:
        super().__init__(f"environment variable {var!r} is not set (in value {value!r})")
        self.var = var
        self.value = value


def _expand(value: str, env: Callable[[str], "str | None"]) -> str:
    """Replace every placeholder in ``value`` using the ``env`` lookup.

    Placeholders are resolved outermost-first; when an unset variable falls
    back to a default, that default is re-scanned for further placeholders so
    nested forms like ``${HOST:${FALLBACK}}`` resolve in one pass.
    """
    # Walk the string left-to-right, expanding the first placeholder we find
    # and continuing after its substitution. This lets a substituted default
    # introduce new placeholders that are picked up on the next iteration.
    out: list[str] = []
    pos = 0
    while True:
        match = _PLACEHOLDER.search(value, pos)
        if match is None:
            out.append(value[pos:])
            break
        out.append(value[pos:match.start()])
        var, default = match.group(1), match.group(2)
        raw = env(var)
        if raw is None:
            if default is None:
                raise EnvError(var, value)
            # Re-scan the default so nested placeholders resolve too.
            replacement = _expand(default, env)
        else:
            replacement = raw
        out.append(replacement)
        pos = match.span()[1]  # end of the full ${...} match, not group 1
    return "".join(out)


def resolve_env(
    data: Any,
    env: Union[Mapping[str, str], Callable[[str], Optional[str]], None] = os.environ,
) -> Any:
    """Return a copy of ``data`` with every ``${VAR}`` placeholder resolved.

    Strings are scanned for placeholders; dicts and lists are walked
    recursively; other values are returned unchanged. ``env`` is a mapping
    (anything supporting ``__getitem__``-style lookups via ``get``) or a
    callable used as the variable lookup, defaulting to the process
    environment.

    Note: an *empty* value for ``${VAR}`` substitutes an empty string rather
    than raising -- only a genuinely unset variable does.
    """

    def lookup(var: str) -> Optional[str]:
        if callable(env):
            return env(var)
        if env is None:
            return None
        return env.get(var)

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            return _expand(node, lookup)
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(data)
