"""Scoped reuse of immutable slot syntax, without changing conventions.

Only successful calls with exact strings and the original pure helper
implementations are memoized. Reference calls bypass the known transparent
string LRUs without clearing shared state; unknown providers stay live.
"""
from __future__ import annotations

from types import FunctionType

from stowage import conventions
from stowage.checker.perf import current_cache, reuse_enabled


def _dependencies():
    cls = conventions.SlotAddress
    functions = (conventions.parse_slot, conventions.cells_of_slot,
                 conventions.format_slot, conventions.odd_bays_of,
                 cls.__init__, cls.__post_init__)
    return (*functions, *(getattr(fn, '__code__', None) for fn in functions),
            conventions._SLOT_RE, cls, cls.__new__)


_ORIGINAL_DEPENDENCIES = _dependencies()
# Strong references above prevent identity reuse. Hash integer identities
# instead of code objects (whose hashes revisit their bytecode/constants).
_DEPENDENCY_IDENTITIES = tuple(map(id, _ORIGINAL_DEPENDENCIES))
(_PARSE_FUNCTION, _CELLS_FUNCTION, _FORMAT_FUNCTION, _ODD_FUNCTION,
 _INIT_FUNCTION, _VALIDATE_FUNCTION, _PARSE_CODE, _CELLS_CODE, _FORMAT_CODE,
 _ODD_CODE, _INIT_CODE, _VALIDATE_CODE, _REGEX, _ADDRESS, _NEW) = _ORIGINAL_DEPENDENCIES
_PARSE = object()
_CELLS = object()
_RAW_PARSE = getattr(_PARSE_FUNCTION, '__wrapped__', _PARSE_FUNCTION)
_RAW_CELLS = getattr(_CELLS_FUNCTION, '__wrapped__', _CELLS_FUNCTION)
_RAW_PARSE_CODE = _RAW_PARSE.__code__
_RAW_CELLS_CODE = _RAW_CELLS.__code__


def _uncached_parse(slot_id):
    helper = conventions.parse_slot
    if (helper is _PARSE_FUNCTION and helper.__code__ is _PARSE_CODE
            and getattr(helper, '__wrapped__', helper) is _RAW_PARSE):
        return _RAW_PARSE(slot_id)
    return helper(slot_id)


def _uncached_cells(slot_id):
    helper = conventions.cells_of_slot
    if (helper is not _CELLS_FUNCTION or helper.__code__ is not _CELLS_CODE
            or getattr(helper, '__wrapped__', helper) is not _RAW_CELLS):
        return helper(slot_id)
    # Execute the actual current body, with only its nested parser made
    # uncached. A private globals snapshot avoids rebinding module state or
    # duplicating the address algorithm, and respects in-place body changes.
    function = FunctionType(_RAW_CELLS.__code__,
        {**_RAW_CELLS.__globals__, 'parse_slot': _uncached_parse},
        _RAW_CELLS.__name__, _RAW_CELLS.__defaults__, _RAW_CELLS.__closure__)
    function.__kwdefaults__ = _RAW_CELLS.__kwdefaults__
    return function(slot_id)


def _original_helpers_live():
    # Avoid rebuilding/hashing a dependency tree per syntax lookup. Each
    # identity below is also part of the immutable cache-key namespace.
    return (conventions.parse_slot is _PARSE_FUNCTION
            and conventions.cells_of_slot is _CELLS_FUNCTION
            and conventions.format_slot is _FORMAT_FUNCTION
            and conventions.odd_bays_of is _ODD_FUNCTION
            and conventions._SLOT_RE is _REGEX
            and conventions.SlotAddress is _ADDRESS
            and _ADDRESS.__init__ is _INIT_FUNCTION
            and _ADDRESS.__post_init__ is _VALIDATE_FUNCTION
            and _ADDRESS.__new__ is _NEW
            and _PARSE_FUNCTION.__code__ is _PARSE_CODE
            and _CELLS_FUNCTION.__code__ is _CELLS_CODE
            and _FORMAT_FUNCTION.__code__ is _FORMAT_CODE
            and _ODD_FUNCTION.__code__ is _ODD_CODE
            and _INIT_FUNCTION.__code__ is _INIT_CODE
            and _VALIDATE_FUNCTION.__code__ is _VALIDATE_CODE
            and getattr(conventions.parse_slot, '__wrapped__', conventions.parse_slot) is _RAW_PARSE
            and getattr(conventions.cells_of_slot, '__wrapped__', conventions.cells_of_slot) is _RAW_CELLS
            and _RAW_PARSE.__code__ is _RAW_PARSE_CODE
            and _RAW_CELLS.__code__ is _RAW_CELLS_CODE)


def parse_slot(slot_id: str) -> conventions.SlotAddress:
    cache = current_cache()
    original = _original_helpers_live()
    if cache is None and reuse_enabled() and original:
        # Normal standalone checker parts retain the public global string LRU.
        return conventions.parse_slot(slot_id)
    if (cache is None or type(slot_id) is not str
            or not original):
        return _uncached_parse(slot_id)
    key = (_PARSE, _DEPENDENCY_IDENTITIES, slot_id)
    if key not in cache:
        cache[key] = _uncached_parse(slot_id)
    return cache[key]


def cells_of_slot(slot_id: str) -> tuple[str, ...]:
    cache = current_cache()
    original = _original_helpers_live()
    if cache is None and reuse_enabled() and original:
        return conventions.cells_of_slot(slot_id)
    if (cache is None or type(slot_id) is not str
            or not original):
        return _uncached_cells(slot_id)
    key = (_CELLS, _DEPENDENCY_IDENTITIES, slot_id)
    if key not in cache:
        cache[key] = _uncached_cells(slot_id)
    return cache[key]
