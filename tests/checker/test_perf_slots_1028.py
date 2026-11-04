"""Scoped immutable slot reuse, original errors and live helper overrides."""
from dataclasses import FrozenInstanceError
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
import re
from threading import Event

import pytest

from stowage import conventions
from stowage.checker import slots
from stowage.checker.perf import current_cache, evaluation_cache, without_reuse


def test_slot_reuse_is_scoped_and_reference_remains_live():
    with evaluation_cache():
        parsed = slots.parse_slot('0020182')
        cells = slots.cells_of_slot('0020182')
        assert slots.parse_slot('0020182') is parsed
        assert slots.cells_of_slot('0020182') is cells
        assert parsed == conventions.parse_slot('0020182')
        assert cells == ('0010182', '0030182')
        assert len(current_cache()) == 2
        with pytest.raises(FrozenInstanceError):
            parsed.tier = 84
        with without_reuse():
            assert slots.parse_slot('0020182') is not parsed
            assert slots.cells_of_slot('0020182') is not cells
    with evaluation_cache():
        assert slots.parse_slot('0020182') is not parsed
    assert slots.parse_slot('0020182') is conventions.parse_slot('0020182')


def test_new_evaluation_scopes_do_not_share_global_memoized_values():
    public_parse = conventions.parse_slot('0020182')
    public_cells = conventions.cells_of_slot('0020182')
    with evaluation_cache():
        first_parse, first_cells = slots.parse_slot('0020182'), slots.cells_of_slot('0020182')
    with evaluation_cache():
        second_parse, second_cells = slots.parse_slot('0020182'), slots.cells_of_slot('0020182')
    assert first_parse == second_parse == public_parse
    assert first_cells == second_cells == public_cells
    assert first_parse is not second_parse and second_parse is not public_parse
    assert first_cells is not second_cells and second_cells is not public_cells
    assert conventions.parse_slot('0020182') is public_parse
    assert conventions.cells_of_slot('0020182') is public_cells


def test_reference_bypasses_global_memos_while_another_thread_refills(monkeypatch):
    requests, filled, values = Queue(), Event(), {}
    original_clear = conventions.parse_slot.cache_clear
    clears = []

    def refill():
        while requests.get() is not None:
            values['parse'] = conventions.parse_slot('0020182')
            values['cells'] = conventions.cells_of_slot('0020182')
            filled.set()

    def request_refill():
        filled.clear()
        requests.put(True)
        assert filled.wait(2), 'refill worker did not finish'

    def clear_then_refill():
        # The former clear+wrapper approach races with this real producer.
        clears.append(True)
        original_clear()
        request_refill()

    monkeypatch.setattr(conventions.parse_slot, 'cache_clear', clear_then_refill)
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(refill)
        try:
            request_refill()
            with without_reuse():
                assert slots.parse_slot('0020182') is not values['parse']
                assert slots.cells_of_slot('0020182') is not values['cells']
            assert not clears
            assert conventions.parse_slot('0020182') is values['parse']
            assert conventions.cells_of_slot('0020182') is values['cells']
        finally:
            requests.put(None)
            worker.result(timeout=2)


@pytest.mark.parametrize('slot', ['invalid', '0000002', '0010003', '0010000', None, 123, []])
@pytest.mark.parametrize('name', ['parse_slot', 'cells_of_slot'])
def test_invalid_inputs_keep_original_exceptions_and_are_never_cached(slot, name):
    original, memo = getattr(conventions, name), getattr(slots, name)
    with pytest.raises(Exception) as expected:
        original(slot)
    with evaluation_cache():
        for _ in range(2):
            with pytest.raises(type(expected.value)) as actual:
                memo(slot)
            assert str(actual.value) == str(expected.value)
        assert current_cache() == {}


def test_string_subclass_bypasses_hashing():
    class UnhashableString(str):
        __hash__ = None
    value = UnhashableString('0010182')
    with evaluation_cache():
        assert slots.parse_slot(value) == conventions.parse_slot(value)
        assert slots.cells_of_slot(value) == conventions.cells_of_slot(value)
        assert current_cache() == {}


def test_live_parser_replacement_always_delegates(monkeypatch):
    calls = []
    with evaluation_cache():
        slots.parse_slot('0010182')

        def replacement(value):
            calls.append(value)
            return conventions.SlotAddress(3, 2, 84)

        monkeypatch.setattr(conventions, 'parse_slot', replacement)
        assert slots.parse_slot('0010182') == conventions.SlotAddress(3, 2, 84)
        assert slots.parse_slot('0010182') == conventions.SlotAddress(3, 2, 84)
        assert len(calls) == 2
        assert slots.cells_of_slot('0010182') == ('0030284',)


@pytest.mark.parametrize('dependency', ['format_slot', 'odd_bays_of', 'regex', 'validator', 'parser_code', 'cells_code'])
def test_nested_source_dependencies_invalidate_populated_cache(monkeypatch, dependency):
    with evaluation_cache():
        slots.parse_slot('0020182')
        slots.cells_of_slot('0020182')
        if dependency == 'format_slot':
            monkeypatch.setattr(conventions, 'format_slot', lambda *_: 'different')
            assert slots.cells_of_slot('0020182') == ('different', 'different')
        elif dependency == 'odd_bays_of':
            monkeypatch.setattr(conventions, 'odd_bays_of', lambda _: (9,))
            assert slots.cells_of_slot('0020182') == ('0090182',)
        elif dependency == 'regex':
            monkeypatch.setattr(conventions, '_SLOT_RE', re.compile(r'never-matches'))
            with pytest.raises(ValueError, match='not a BBBRRTT'):
                slots.parse_slot('0020182')
        elif dependency == 'validator':
            def invalid(self):
                raise ValueError('changed validator')
            monkeypatch.setattr(conventions.SlotAddress, '__post_init__', invalid)
            with pytest.raises(ValueError, match='changed validator'):
                slots.parse_slot('0020182')
        elif dependency == 'parser_code':
            def changed(slot_id):
                return SlotAddress(7, 2, 84)  # resolved in conventions' globals
            # The public helper now wraps a string LRU; mutate its actual
            # parser implementation, preserving the intended live-code probe.
            parser = getattr(conventions.parse_slot, '__wrapped__', conventions.parse_slot)
            monkeypatch.setattr(parser, '__code__', changed.__code__)
            assert slots.parse_slot('0020182') == conventions.SlotAddress(7, 2, 84)
        else:
            def changed(slot_id):
                return ('changed cells body',)
            cells = getattr(conventions.cells_of_slot, '__wrapped__', conventions.cells_of_slot)
            monkeypatch.setattr(cells, '__code__', changed.__code__)
            assert slots.cells_of_slot('0020182') == ('changed cells body',)


def test_representative_valid_addresses_match_originals():
    with evaluation_cache():
        for bay in range(1, 40):
            for row in range(6):
                for tier in (2, 4, 6, 80, 82, 84):
                    value = f'{bay:03d}{row:02d}{tier:02d}'
                    assert slots.parse_slot(value) == conventions.parse_slot(value)
                    assert slots.cells_of_slot(value) == conventions.cells_of_slot(value)
