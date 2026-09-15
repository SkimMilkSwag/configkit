# configkit

A tiny **type-safe config loader** for YAML and JSON files, with declarative validation.

Load a config file, validate it against a schema, and get an immutable,
attribute-accessible object back. Unknown keys are rejected by default so
typos fail loudly instead of silently producing `None`.

- **Zero hard dependencies** — stdlib only; PyYAML is used for `.yaml`/`.yml` files.
- **Declarative schemas** — required types, defaults, min/max bounds, string
  patterns, allowed choices, nested dict schemas, list item types.
- **Env-var substitution** — `${VAR}` and `${VAR:default}` in any string value,
  resolved before validation so secrets can live outside the file.
- **CLI checker** — `python -m configkit config.yaml --schema schema.yaml` for
  CI-friendly validation with exit codes.
- **Strict by design** — `True` is not an `int`, unknown keys raise, missing
  required keys raise. Fail at load time, not at runtime.

## Install

```bash
pip install .            # JSON only (pure stdlib)
pip install .[yaml]      # + PyYAML for YAML support
```

## Usage

```python
from configkit import load, field

cfg = load(
    "config.yaml",
    {
        "host": str,                                  # required, must be a string
        "port": field(int, minimum=1, maximum=65535), # required, bounded int
        "workers": field(int, default=2),             # optional, defaults to 2
        "mode": field(str, choices=("fast", "safe")), # enum-like constraint
        "tags": {"type": list, "items": str},         # list of strings
        "db": {                                       # nested dict with its own schema
            "type": dict,
            "schema": {
                "user": str,
                "password": str,
                "pool_size": field(int, minimum=1, maximum=128, default=5),
            },
        },
    },
)

cfg.host       # 'localhost'
cfg.db.user    # nested Config objects
cfg.workers    # 2 (default applied)
cfg.missing    # AttributeError listing valid keys
```

A schema entry can be:

- a type (`str`, `int`, `float`, `bool`, or a tuple of them) — required,
- `field(...)` for anything with constraints,
- or the dict form `{"type": ..., "schema": {...}}` for nested dicts.

`field()` arguments: `types`, `default`, `minimum`, `maximum`, `min_length`,
`max_length`, `pattern` (regex, fullmatch), `choices`, `schema` (nested),
`items` (element spec for lists).

## Environment-variable substitution

String values may contain `${VAR}` placeholders, resolved from the process
environment (or any mapping/callable passed as `env`) before validation runs:

```python
import os
from configkit import loads

cfg = loads('{"dsn": "${DB_USER}:${DB_PASS}@${HOST:localhost}"}',
            {"dsn": str}, env=os.environ)
# ${DB_USER} and ${DB_PASS} must be set; ${HOST:localhost} falls back if unset
```

Unset variables without a default raise `EnvError` — a typo'd name fails loudly
rather than leaking through as an empty string.

## CLI checker

Validate a config file against a schema from the command line (CI-friendly):

```bash
python -m configkit config.yaml --schema schema.yaml
# exit 0 + "config.yaml: OK" on success
# exit 1 + one line per problem on stderr on failure
```

The schema file is JSON or YAML; type names are written as strings (`"int"`,
`"str"`, ...) and resolved automatically.

## Errors

- `ConfigError` — file missing, bad JSON/YAML syntax, wrong top-level type.
- `ValidationError` — value failed a constraint; carries the dotted `path`
  (e.g. `"db.pool_size"`) and a human-readable message.

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

## Why another config lib?

Because `os.environ.get("PORT")` and `int(cfg.get("port"))` are a one-line
bug away from a 3am deploy. configkit is deliberately small: one idea —
*validate once at the door, then never doubt your values again*.
