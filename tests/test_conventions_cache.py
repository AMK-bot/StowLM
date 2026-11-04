"""Pure string memoization preserves conventions validation and public call forms."""

from dataclasses import FrozenInstanceError
import inspect

import pytest

from stowage import conventions as c


CASES = [
    (c.parse_slot, "slot_id", "0020182", "0030182"),
    (c.parse_yard, "address", "A1-012-03-02", "A1-013-03-02"),
    (c.cells_of_slot, "slot_id", "0020182", "0030182"),
    (c.is_valid_container_id, "cid", "CSQU3054383", "CSQU3054384"),
]


class _UnhashableString(str):
    __hash__ = None


class _CollidingString(str):
    def __hash__(self):
        return 1

    def __eq__(self, other):
        return True


@pytest.mark.parametrize("function,parameter,value,other", CASES)
@pytest.mark.parametrize("string_type", [_UnhashableString, _CollidingString])
@pytest.mark.parametrize("keyword", [False, True])
def test_string_subclasses_bypass_every_cache(function, parameter, value, other, string_type, keyword):
    # Compute content-based answers before clearing the nested parse_slot cache.
    expected = [function.__wrapped__(text) for text in (value, other)]
    for cached_function, *_ in CASES:
        cached_function.cache_clear()
    before = [cached_function.cache_info() for cached_function, *_ in CASES]

    actual = []
    for text in (value, other):
        subclass_value = string_type(text)
        actual.append(function(**{parameter: subclass_value}) if keyword else function(subclass_value))

    assert actual == expected
    # cells_of_slot also delegates to parse_slot: neither level may cache a subclass.
    assert [cached_function.cache_info() for cached_function, *_ in CASES] == before


@pytest.mark.parametrize("function,parameter,value,other", CASES)
def test_complete_string_keys_and_call_forms(function, parameter, value, other):
    function.cache_clear()
    first = function(value)
    # Equal content from another string object and keyword calls share one key.
    assert function((value + "!")[:-1]) is first
    assert function(**{parameter: value}) is first
    assert function(other) == function.__wrapped__(other)
    info = function.cache_info()
    assert (info.hits, info.misses, info.currsize, info.maxsize) == (2, 2, 2, 65536)
    assert inspect.signature(function) == inspect.signature(function.__wrapped__)


@pytest.mark.parametrize("function", [case[0] for case in CASES])
@pytest.mark.parametrize("value", [[], {}, None, 12, b"0020182"])
def test_non_strings_use_original_failure_path(function, value):
    function.cache_clear()
    with pytest.raises(Exception) as original:
        function.__wrapped__(value)
    before = function.cache_info()
    with pytest.raises(type(original.value)) as actual:
        function(value)
    assert str(actual.value) == str(original.value)
    assert function.cache_info() == before


@pytest.mark.parametrize("function", [c.parse_slot, c.parse_yard, c.cells_of_slot])
def test_parse_failures_are_not_cached(function):
    function.cache_clear()
    errors = []
    for _ in range(2):
        with pytest.raises(ValueError) as error:
            function("bad-address")
        errors.append(error.value)
    assert errors[0] is not errors[1]
    assert str(errors[0]) == str(errors[1])
    assert function.cache_info().currsize == 0
    assert function.cache_info().misses == 2


def test_cached_values_are_immutable():
    slot = c.parse_slot("0020182")
    yard = c.parse_yard("A1-012-03-02")
    with pytest.raises(FrozenInstanceError):
        slot.row = 4
    with pytest.raises(FrozenInstanceError):
        yard.row = 4
    cells = c.cells_of_slot("0020182")
    assert isinstance(cells, tuple)
    assert isinstance(c.is_valid_container_id("CSQU3054383"), bool)


def test_cache_bound_evicts_old_strings():
    c.is_valid_container_id.cache_clear()
    for index in range(65537):
        assert c.is_valid_container_id(f"invalid-{index}") is False
    assert c.is_valid_container_id.cache_info().currsize == 65536
    c.is_valid_container_id("invalid-0")
    assert c.is_valid_container_id.cache_info().misses == 65538


@pytest.mark.parametrize("function,parameter,value,other", CASES)
def test_invalid_call_signatures_keep_original_type_errors(function, parameter, value, other):
    function.cache_clear()
    for args, kwargs in [((), {}), ((value, other), {}), ((value,), {parameter: value})]:
        with pytest.raises(TypeError) as original:
            function.__wrapped__(*args, **kwargs)
        with pytest.raises(TypeError) as actual:
            function(*args, **kwargs)
        assert str(actual.value) == str(original.value)
    assert function.cache_info().currsize == 0
