"""configkit -- a tiny type-safe config loader for YAML and JSON files.

Load a config file, validate it against a declarative schema, and get an
immutable, attribute-accessible object back. Unknown keys are rejected by
default so typos fail loudly instead of silently producing ``None``.

Example::

    from configkit import load, field

    cfg = load(
        "config.yaml",
        {
            "host": str,
            "port": field(int, minimum=1, maximum=65535),
            "workers": {"type": int, "default": 2},
        },
    )
    print(cfg.host, cfg.port)

Only the standard library is required; PyYAML is used for YAML files.
"""

from .core import Config, ConfigError, field, load, loads
from .env import EnvError, resolve_env
from .schema import Spec, ValidationError

__all__ = [
    "Config",
    "ConfigError",
    "EnvError",
    "Spec",
    "ValidationError",
    "field",
    "load",
    "loads",
    "resolve_env",
]
