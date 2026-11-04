"""Public typed API checks; source cell transcriptions are tested separately."""

import pytest

from stowage.rules.imdg.tables import (
    CONTAINERSHIP_TABLE_NAME,
    GENERAL_TABLE_NAME,
    containership_cells,
    containership_segregation,
    general_cells,
    general_segregation,
    get_table,
)


def test_catalogue_identifiers_and_complete_typed_tables():
    assert get_table(GENERAL_TABLE_NAME) is general_cells()
    assert get_table(CONTAINERSHIP_TABLE_NAME) is containership_cells()
    assert len(general_cells()) == 289
    assert len(containership_cells()) == 120
    with pytest.raises(KeyError):
        get_table("other_edition.table")


def test_general_symbols_cannot_masquerade_as_no_segregation():
    # MSC.556(108), PDF p487: X requires DGL, * requires 7.2.7.1.
    assert general_segregation("9", "3").value == "X"
    assert general_segregation("9", "3").level is None
    assert general_segregation("1.1", "1.3").value == "*"
    assert general_segregation("1.2", "3").level == 4
    with pytest.raises(KeyError):
        general_segregation("1.4S", "3")


def test_closed_open_vertical_order_and_deck_exception_are_retained():
    # PDF p506: open on closed allowed; otherwise OO, including deck exception.
    closed = containership_segregation(
        1, ship_type="closed_holds", orientation="vertical", container_pair="closed_open"
    )
    assert closed.alternatives[0].open_above_closed
    assert any(a.segregated_by_deck for a in closed.alternatives)
    # PDF p507 hatchless table does not offer a separating-deck exception.
    hatchless = containership_segregation(
        1, ship_type="hatchless", orientation="vertical", container_pair="closed_open"
    )
    assert not any(a.segregated_by_deck for a in hatchless.alternatives)


def test_bulkhead_alternative_is_not_collapsed_to_distance():
    # PDF p506, .2 CC under deck fore/aft: one space OR one bulkhead.
    cell = containership_segregation(
        2, ship_type="closed_holds", orientation="fore_aft",
        container_pair="closed_closed", deck="under",
    )
    assert len(cell.alternatives) == 2
    assert {a.min_bulkheads for a in cell.alternatives} == {0, 1}
    bulkhead = next(a for a in cell.alternatives if a.min_bulkheads)
    assert bulkhead.barriers_resistant_to_fire_and_liquids
    assert bulkhead.min_distance_from_intervening_bulkhead_m == 6


def test_cached_data_is_immutable():
    with pytest.raises(TypeError):
        general_segregation("3", "5.1").source["pdf_page"] = 0
    with pytest.raises(TypeError):
        general_cells()[("3", "5.1")] = None


def test_segregation_code_cross_references_remain_explicit():
    from stowage.rules.imdg.tables import segregation_codes
    codes = segregation_codes()
    # PDF p492, 7.2.8: SG6 "Segregation as for class 5.1."
    assert codes['SG6'].meaning == 'Segregation as for class 5.1.'
    assert codes['SG6'].source['pdf_page'] == 492
    # PDF p493, SG26 adds two spaces athwartship for classes 2.1 and 3.
    assert 'two container spaces athwartship' in codes['SG26'].meaning
    assert codes['SG26'].source['pdf_page'] == 493
    # PDF p494 SG72: "See tables in 7.2.6.3."
    assert codes['SG72'].meaning == 'See tables in 7.2.6.3.'
    assert codes['SG72'].source['pdf_page'] == 494


def test_dgl_catalogue_tables_and_segregation_code_coverage():
    from stowage.rules.imdg.dgl import dangerous_goods_list
    from stowage.rules.imdg.tables import DGL_TABLE_NAME, STOWAGE_PROVISIONS_NAME, segregation_codes
    assert len(get_table(DGL_TABLE_NAME)) == 53
    assert get_table(STOWAGE_PROVISIONS_NAME)['SW19'].source.pdf_page == 485
    assert {c for entry in dangerous_goods_list() for c in entry.segregation_codes} <= segregation_codes().keys()
