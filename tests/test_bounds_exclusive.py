"""Edge-case pins for exclusive numeric bounds (minimum/maximum).

A value *equal* to a bound is rejected; interior values pass. Covers ints,
floats, list items, and nested dict sections — the four places the bound
check can fire.
"""

import json

import pytest

from configkit import field, loads
from configkit.schema import ValidationError


def test_int_at_exact_minimum_rejected():
    schema = {"n": field(int, minimum=1)}
    with pytest.raises(ValidationError, match="does not exceed minimum"):
        loads(json.dumps({"n": 1}), schema)


def test_int_at_exact_maximum_rejected():
    schema = {"n": field(int, maximum=10)}
    with pytest.raises(ValidationError, match="does not fall below maximum"):
        loads(json.dumps({"n": 10}), schema)


def test_int_interior_values_pass():
    schema = {"n": field(int, minimum=1, maximum=10)}
    assert loads(json.dumps({"n": 2}), schema)["n"] == 2
    assert loads(json.dumps({"n": 9}), schema)["n"] == 9


def test_float_at_exact_bounds_rejected():
    schema = {"x": field((int, float), minimum=0.5, maximum=1.5)}
    with pytest.raises(ValidationError, match="does not exceed minimum"):
        loads(json.dumps({"x": 0.5}), schema)
    with pytest.raises(ValidationError, match="does not fall below maximum"):
        loads(json.dumps({"x": 1.5}), schema)


def test_float_interior_passes():
    schema = {"x": field((int, float), minimum=0.5, maximum=1.5)}
    assert loads(json.dumps({"x": 1.0}), schema)["x"] == 1.0


def test_list_items_at_bound_rejected():
    schema = {"ports": field(list, items=field(int, minimum=1, maximum=65535))}
    # interior values fine
    assert loads(json.dumps({"ports": [80, 443]}), schema)["ports"] == [80, 443]
    # one item at the exact minimum trips the bound
    with pytest.raises(ValidationError, match=r"\[1\].*does not exceed minimum"):
        loads(json.dumps({"ports": [80, 1, 443]}), schema)
    # one item at the exact maximum trips it too
    with pytest.raises(ValidationError, match=r"\[2\].*does not fall below maximum"):
        loads(json.dumps({"ports": [80, 443, 65535]}), schema)


def test_nested_dict_bound_rejected():
    schema = {
        "db": {
            "type": dict,
            "schema": {"pool_size": field(int, minimum=1, maximum=128)},
        }
    }
    assert loads(json.dumps({"db": {"pool_size": 5}}), schema)["db"]["pool_size"] == 5
    with pytest.raises(ValidationError, match="db.pool_size.*does not exceed minimum"):
        loads(json.dumps({"db": {"pool_size": 1}}), schema)
    with pytest.raises(ValidationError, match="db.pool_size.*does not fall below maximum"):
        loads(json.dumps({"db": {"pool_size": 128}}), schema)


def test_bool_not_confused_with_int_bound():
    # bool is a subclass of int; True == 1 would equal minimum=1. The type
    # gate must reject it before the bound check (bool not in int-only types).
    schema = {"n": field(int, minimum=1)}
    with pytest.raises(ValidationError, match="expected int"):
        loads(json.dumps({"n": True}), schema)


def test_default_at_bound_is_rejected_on_application():
    # A default equal to the bound fails when applied (default flows through
    # the same coerce_value path).
    schema = {"n": field(int, minimum=1, maximum=10, default=1)}
    with pytest.raises(ValidationError, match="does not exceed minimum"):
        loads(json.dumps({}), schema)
