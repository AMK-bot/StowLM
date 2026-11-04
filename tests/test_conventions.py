"""Conventions: BBBRRTT slots, yard addresses, ISO 6346, UN/LOCODE, rule ids."""

from __future__ import annotations

import itertools

import pytest

from stowage import conventions as c


def test_slot_round_trip_exhaustive_sample() -> None:
    for bay, row, tier in itertools.product([1, 2, 3, 40, 41, 999], [0, 1, 2, 15, 16], [2, 4, 18, 80, 82, 98]):
        s = c.format_slot(bay, row, tier)
        assert len(s) == 7
        p = c.parse_slot(s)
        assert (p.bay, p.row, p.tier) == (bay, row, tier)
        assert str(p) == s
        assert p.on_deck == (tier >= 80)


@pytest.mark.parametrize("bad", ["", "001018", "00101820", "0a10182", "0000102", "0010103"])
def test_slot_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        c.parse_slot(bad)


def test_forty_foot_cells() -> None:
    assert c.cells_of_slot("0020182") == ("0010182", "0030182")
    assert c.cells_of_slot("0030182") == ("0030182",)
    assert c.odd_bays_of(14) == (13, 15)
    assert c.forty_bay(3) == (2, 4)


def test_rows_and_stacks() -> None:
    assert c.row_side(0) == "centre" and c.row_side(1) == "starboard" and c.row_side(2) == "port"
    assert c.rows_port_to_starboard([0, 1, 2, 3, 4]) == [4, 2, 0, 1, 3]
    assert c.stack_id(1, 1, True) == "00101D" and c.stack_id(3, 0, False) == "00300H"
    with pytest.raises(ValueError):
        c.stack_id(2, 1, True)


def test_yard_round_trip() -> None:
    for block, bay, row, tier in [("A1", 1, 1, 1), ("R02", 24, 6, 5), ("Z", 999, 99, 99)]:
        s = c.format_yard(block, bay, row, tier)
        a = c.parse_yard(s)
        assert (a.block, a.bay, a.row, a.tier) == (block, bay, row, tier) and str(a) == s
        assert a.ground == (block, bay, row)
    assert c.format_yard("A1", 12, 3, 2) == "A1-012-03-02"
    assert c.yard_stack_key("A1", 12, 3) == "A1-012-03"


@pytest.mark.parametrize("bad", ["a1-012-03-02", "A1-12-03-02", "A1-012-03-00", "ABCDE-001-01-01", "A1-000-01-01"])
def test_yard_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        c.parse_yard(bad)


def test_iso6346_check_digit_known_example() -> None:
    # ISO 6346 worked example: CSQU3054383 (Wikipedia "ISO 6346", check digit 3)
    assert c.iso6346_check_digit("CSQU305438") == 3
    assert c.is_valid_container_id("CSQU3054383")
    assert not c.is_valid_container_id("CSQU3054384")
    cid = c.make_container_id(c.SYNTHETIC_OWNER_CODES[0], 42)
    assert c.is_valid_container_id(cid) and cid.startswith("QZXU000042")


def test_locode_and_rule_id() -> None:
    assert c.is_valid_locode("THLCH") and c.is_valid_locode("SGSIN") and not c.is_valid_locode("thlch")
    assert c.is_valid_rule_id("imdg.segregation.7_4_3") and not c.is_valid_rule_id("IMDG.x")
    assert not c.is_valid_rule_id("nodots")
