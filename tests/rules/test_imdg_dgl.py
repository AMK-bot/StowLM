"""Independent IMDG 42-24 source spot checks and US HMT reconciliation."""
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from stowage.rules.imdg.dgl import (
    DGL_TABLE, PROVISIONS_TABLE, dangerous_goods_list, entries_by_class,
    entries_for_un, get_table, lookup_dgl, stowage_handling_provision,
)


# Quoted cells from MSC.556(108), 3.2: (UN, PG, PDF page, class, category,
# subsidiary hazards, column 16b). Page numbers are one-based PDF pages.
@pytest.mark.parametrize('un,pg,page,cl,cat,sub,seg', [
    (4,None,581,'1.1','04',(),('SGG2','SG27','SG31')),
    (7,None,581,'1.2','03',(),()),
    (10,None,581,'1.3','03',(),()),
    (12,None,581,'1.4','01',(),()),
    (331,None,590,'1.5','03',(),('SG34',)),
    (336,None,590,'1.4','02',(),()),
    (486,None,596,'1.6','03',(),()),
    (1005,None,598,'2.3','D',('8',),('SGG18','SG35','SG46')),
    (1006,None,598,'2.2','A',(),()),
    (1017,None,598,'2.3','D',('5.1','8'),('SG6','SG19')),
    (1057,None,600,'2.1','B',(),()),
    (1090,'II',602,'3','E',(),()),
    (1202,'III',607,'3','A',(),()),
    (1263,'I',610,'3','E',(),()),
    (1263,'II',610,'3','B',(),()),
    (1263,'III',610,'3','A',(),()),
    (1325,'II',614,'4.1','B',(),('SG72',)),
    (1325,'III',614,'4.1','B',(),('SG72',)),
    (1333,'II',614,'4.1','A',(),('SG17','SG25','SG26')),
    (1361,'II',616,'4.2','A',(),()),
    (1361,'III',616,'4.2','A',(),()),
    (1381,'I',617,'4.2','E',('6.1',),()),
    (1402,'I',619,'4.3','B',(),('SG26','SG35')),
    (1402,'II',619,'4.3','B',(),('SG26','SG35')),
    (1428,'I',620,'4.3','D',(),('SG26','SG35')),
    (1689,'I',635,'6.1','B',(),('SGG6','SG35')),
    (1748,'II',639,'5.1','D',(),('SGG8','SG35','SG38','SG49','SG53','SG60')),
    (1748,'III',639,'5.1','D',(),('SGG8','SG35','SG38','SG49','SG53','SG60')),
    (1789,'II',642,'8','C',(),('SGG1','SG36','SG49')),
    (1789,'III',642,'8','C',(),('SGG1','SG36','SG49')),
    (1845,None,647,'9','C',(),()),
    (1978,None,652,'2.1','E',(),()),
    (1993,'I',653,'3','E',(),()),
    (1993,'II',653,'3','B',(),()),
    (1993,'III',653,'3','A',(),()),
    (2211,'III',660,'9','E',(),('SG5','SG14')),
    (2794,None,693,'8','A',(),('SGG1','SG36','SG49')),
    (2810,'I',694,'6.1','B',(),()),
    (2810,'II',694,'6.1','B',(),()),
    (2810,'III',694,'6.1','A',(),()),
    (2814,None,694,'6.2','E',(),('SG50',)),
    (2880,'II',698,'5.1','D',(),('SGG8','SG35','SG38','SG49','SG53','SG60')),
    (2880,'III',698,'5.1','D',(),('SGG8','SG35','SG38','SG49','SG53','SG60')),
    (2910,None,699,'7','A',(),()),  # Column 4 says 'See SP290', separately tested.
    (2915,None,699,'7','A',(),()),  # Column 4 says 'See SP172', separately tested.
    (3077,'III',709,'9','A',(),()),
    (3082,'III',709,'9','A',(),()),
    (3105,None,712,'5.2','D',(),('SG35','SG36','SG72')),
    (3109,None,712,'5.2','D',(),('SG35','SG36','SG72')),
    (3166,None,717,'9','A',(),()),
    (3373,None,736,'6.2','C',(),()),
    (3480,None,746,'9','A',(),()),
    (3481,None,746,'9','A',(),()),
])
def test_dgl_quoted_cells(un, pg, page, cl, cat, sub, seg):
    entry = lookup_dgl(un, pg)
    assert (entry.hazard_class,entry.stowage_category,entry.subsidiary_risks,
            entry.segregation_codes) == (cl,cat,sub,seg)
    assert entry.source.pdf_page == page
    assert entry.source.columns == ('1','2','3','4','5','6','16a','16b')


def test_sample_covers_every_division_and_packing_group():
    entries = dangerous_goods_list()
    classes = {f'1.{i}' for i in range(1,7)} | {'2.1','2.2','2.3','3','4.1','4.2','4.3','5.1','5.2','6.1','6.2','7','8','9'}
    assert len(entries) == 53
    assert len({e.un_number for e in entries}) == 41
    assert {e.hazard_class for e in entries} == classes
    assert {e.packing_group for e in entries} == {None,'I','II','III'}
    for cl in classes:
        assert entries_by_class(cl)
        assert all(e.hazard_class == cl for e in entries_by_class(cl))


def test_conditional_and_special_stowage_fields():
    # PDF 746 col 16a: both lithium entries 'Category A SW19'.
    assert lookup_dgl(3480).stowage_codes == lookup_dgl(3481).stowage_codes == ('SW19',)
    # PDF 485 SW19: 'category C, unless ... a short international voyage'.
    sw19 = stowage_handling_provision('SW19')
    assert 'category C' in sw19.meaning and '376 or 377' in sw19.meaning
    assert sw19.source.pdf_page == 485
    # PDF 693 col16a 'Category A SW16'; SW16 (p485) changes open-unit loads to B.
    assert lookup_dgl(2794).stowage_codes == ('SW16',)
    assert 'category B' in stowage_handling_provision('SW16').meaning
    # PDF 639/698: both hypochlorite PGs have 'SW1 SW11'.
    for un in (1748,2880):
        assert all(e.stowage_codes == ('SW1','SW11') for e in entries_for_un(un))
    # PDF 660 col16a 'SW1 SW6', PDF 694 'SW2 H1 H5'.
    assert lookup_dgl(2211,'III').stowage_codes == ('SW1','SW6')
    assert lookup_dgl(2814).stowage_codes == ('SW2','H1','H5')
    # PDF699 column4 is a conditional cross-reference, not an unconditional dash.
    assert 'SP290' in lookup_dgl(2910).subsidiary_hazard_note
    # PDF610 and653 PG III includes SP223/955, unlike PG I/II.
    assert lookup_dgl(1263,'III').special_provisions == ('163','223','367','955')
    assert lookup_dgl(1993,'III').special_provisions == ('223','274','955')
    assert lookup_dgl(4).compatibility_group == 'D'  # PDF581 col3 '1.1D'.


def test_catalogue_names_lookup_and_readonly_data():
    from stowage.rules.imdg.tables import get_table as registry
    assert registry(DGL_TABLE) is get_table(DGL_TABLE)
    assert registry(PROVISIONS_TABLE) is get_table(PROVISIONS_TABLE)
    assert len(get_table(PROVISIONS_TABLE)) == 14
    for e in dangerous_goods_list():
        assert lookup_dgl(e.un_number,e.packing_group) is e
        assert all(code in get_table(PROVISIONS_TABLE) for code in e.source_stowage_codes)
    with pytest.raises(ValueError, match='packing group'):
        lookup_dgl(1263)
    with pytest.raises(KeyError):
        lookup_dgl(9999)
    with pytest.raises(KeyError):
        lookup_dgl(1263,'IV')
    with pytest.raises(KeyError):
        get_table('unrecognized')
    with pytest.raises(TypeError):
        get_table(DGL_TABLE)['bad'] = lookup_dgl(3480)
    assert entries_by_class('invalid') == entries_for_un(9999) == ()


def test_cross_checks_trace_to_cfr_xml_snapshot():
    """Validate every retained CFR row against actual XML cells + continuations."""
    path = Path(__file__).resolve().parents[2] / 'data/raw/ecfr-172-101/172.101.xml'
    table_rows = list(ET.parse(path).getroot().iter('TR'))
    # XML source pointers count all TR elements, including header rows (one-based).
    for entry in dangerous_goods_list():
        check = entry.cfr_cross_check
        assert check.rows, f'No US comparison for {entry.id}'
        assert check.authority == 'IMDG 42-24 wins for this model'
        for row in check.rows:
            cells = [' '.join(''.join(c.itertext()).split()) for c in table_rows[row.xml_row-1].findall('TD')]
            assert len(cells) == 14
            assert (cells[4] or None, cells[5], cells[12], cells[13]) == (
                row.packing_group,row.labels,row.stowage_category,row.vessel_provisions)
            if cells[3]:
                assert cells[3] == f'UN{entry.un_number:04d}'
                assert cells[2] == row.hazard_class
            else:
                # Blank continuation cells inherit the closest preceding explicit UN.
                preceding = table_rows[:row.xml_row-1]
                parent = next(tr for tr in reversed(preceding)
                              if len(tr.findall('TD')) == 14 and ''.join(tr.findall('TD')[3].itertext()).strip())
                assert ''.join(parent.findall('TD')[3].itertext()).strip() == f'UN{entry.un_number:04d}'
        comparable = [r for r in check.rows if r.packing_group == entry.packing_group
                      and r.hazard_class == entry.hazard_class + (entry.compatibility_group or '')]
        assert comparable, entry.id
        # 49 CFR 172.101(g), local 172.101.txt: "The first code is
        # indicative of the primary hazard"; additional codes indicate
        # subsidiary hazards. Compare only class/PG-compatible US variants,
        # never ammonia's domestic 2.2 row against IMDG 2.3 (PDF598 col4).
        label_subsidiaries = {
            tuple(code.strip() for code in row.labels.split(',')[1:])
            for row in comparable
        }
        if entry.un_number in (2910, 2915):
            # IMDG PDF699 col4 says "See SP290" / "See SP172". Neither
            # CFR "None" nor primary label 7 resolves contents-dependent
            # subsidiary hazards; this is a comparison of fixed labels only.
            assert entry.subsidiary_risks == ()
            assert f'SP{290 if entry.un_number == 2910 else 172}' in entry.subsidiary_hazard_note
            assert label_subsidiaries == {()}
        else:
            assert entry.subsidiary_hazard_note is None, entry.id
            assert entry.subsidiary_risks in label_subsidiaries, entry.id
        if entry.un_number in (1090,3373):
            # IMDG PDF602 E / PDF736 C; US10A B / A respectively.
            assert entry.stowage_category == {1090:'E',3373:'C'}[entry.un_number]
            assert {r.stowage_category for r in comparable} == {{1090:'B',3373:'A'}[entry.un_number]}
            assert check.differences == ('vessel stowage category differs',)
        else:
            assert check.status == 'matched' and not check.differences
            assert any(r.stowage_category == entry.stowage_category for r in comparable)
    # US ammonia includes a domestic 2.2 variant; the IMDG row remains 2.3 + 8.
    assert {'2.2','2.3'} <= {r.hazard_class for r in lookup_dgl(1005).cfr_cross_check.rows}


@pytest.mark.parametrize('un,pg,page,stow,sp', [
    (336,None,590,('SW1',),()),
    (1978,None,652,('SW2',),('392',)),
    (1333,'II',614,('H1',),()),
    (1361,'II',616,('SW1','SW27','H2'),('978',)),
    (1361,'III',616,('SW1','SW27','H2'),('978',)),
    (1402,'I',619,('H1',),()),
    (1402,'II',619,('H1',),()),
    (3109,None,712,('SW1',),('122','274')),
    (2810,'I',694,('SW2',),('274','315')),
    (2810,'II',694,('SW2',),('274',)),
    (2810,'III',694,('SW2',),('223','274')),
    (3373,None,736,('SW2','SW18'),('319','341')),
    (2915,None,699,('SW20','SW21'),('172','317','325')),
])
def test_generator_followup_source_columns_6_and_16a(un, pg, page, stow, sp):
    """Quoted code cells from the additional generator UN source rows."""
    entry = lookup_dgl(un,pg)
    assert entry.source.pdf_page == page
    assert entry.source_stowage_codes == stow
    assert entry.special_provisions == sp
    if un == 336:
        assert entry.compatibility_group == 'G'  # p590 col3 '1.4G'.
    if un == 2915:
        assert 'SP172' in entry.subsidiary_hazard_note  # p699 col4 'See SP172'.
    if un == 3373:
        assert entry.stowage_category == 'C'  # p736 col16a, not unconditionally A.
        sw18 = stowage_handling_provision('SW18')
        assert sw18.source.pdf_page == 485 and 'P650' in sw18.meaning
