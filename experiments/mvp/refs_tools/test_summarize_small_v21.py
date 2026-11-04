"""TEST v2.1 source union, failed populations and accepted-byte preservation."""
import importlib.util
import json
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('v21_reporter',Path(__file__).with_name('summarize_small_v21.py'))
r=importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def expected(n=3):
    return [dict(path=f'test/small/ship/case-{i}.json',sha256=str(i),ship='ship') for i in range(n)]


def record(i, passed=True):
    return dict(instance_id=f'case-{i}', input_sha256=str(i), rung='small',
                result={'repaired': False, 'status': 'ok'}, shippable=passed, report_binding_valid=True,
                source_root=f'epoch-{i}',source_before={'code':str(i)},
                report={'passed':passed,'violations':[] if passed else [{'rule_id':'hard.failure','severity':'hard'}],
                        'parts':{},'count':{'weighted_nonproductive':i}}, timing={'total_s':1})


def save(root,folder,row):
    path=root/folder/'instances'/row['instance_id']/'record.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(row))


def test_epoch_union_stale_journal_failed_and_missing_retention(tmp_path):
    old='test-solver-small-v2'
    extra='test-solver-small-v21-noscreen-extra'
    passed,failed=record(0),record(1,False)
    save(tmp_path,old,passed)
    save(tmp_path,old,failed)
    (tmp_path/old/'records.jsonl').write_text(json.dumps(passed)+'\n')
    save(tmp_path,extra,record(2))
    save(tmp_path,'test-solver-small-v21-screen',record(0))
    summary=r.summarize(tmp_path,expected())
    assert len((tmp_path/old/'records.jsonl').read_text().splitlines())==2
    a=summary['methods']['solver, no securing screen']
    assert a['n_instances']==3 and a['n_passed']==2
    assert a['expected_instances']==3
    a=summary['methods']['solver']
    assert a['n_instances']==1 and a['n_passed']==1
    assert a['own_pass_rate']==pytest.approx(1/3)
    assert len(a['missing_paths'])==2
    assert summary['records']['solver, no securing screen'][1]['report']==failed['report']
    assert summary['source_epochs']['solver, no securing screen'][0]['attempts']==2
    assert summary['source_epochs']['solver, no securing screen'][1]['attempts']==1
    assert 'unavailable' in summary['paired_comparison']
    text=r.markdown(summary)
    assert 'hard.failure' in text and 'unattempted' in text and 'Source confounding' in text
    assert '1/3' in text and '2/3' in text


def test_union_rejects_duplicates_across_epochs(tmp_path):
    for folder,_ in r.SOURCES['solver, no securing screen']:
        save(tmp_path,folder,record(0))
    with pytest.raises(ValueError,match='Duplicate input'):
        r.summarize(tmp_path,expected())


def test_union_rejects_unmatched_id_and_hash(tmp_path):
    folder='test-solver-small-v21-screen'
    save(tmp_path,folder,record(9))
    with pytest.raises(ValueError,match='Unmatched input'):
        r.summarize(tmp_path,expected())
    row=record(9)
    row['input_sha256']='0'
    save(tmp_path,folder,row)
    with pytest.raises(ValueError,match='Unmatched instance ID'):
        r.summarize(tmp_path,expected())


def test_complete_population_pairs_only(tmp_path):
    for i in range(3):
        save(tmp_path,'test-solver-small-v21-screen',record(i))
        folder='test-solver-small-v2' if i<2 else 'test-solver-small-v21-noscreen-extra'
        save(tmp_path,folder,record(i))
    summary=r.summarize(tmp_path,expected())
    assert summary['paired_comparison']['n_pairs']==3


def test_accepted_tiny_and_old_small_prefix_exact_and_repeatable(tmp_path):
    report=tmp_path/'report.md'
    prefix=b'# Tiny\r\n1.234\r\n'+r.v2.MARKER+b'\noriginal ten 157.5\n'+r.v2.END_MARKER+b'\n'
    report.write_bytes(prefix)
    backup=tmp_path/'backup'
    one=r.append_section(report,'new body\n',backup)
    two=r.append_section(report,'newest body\n',backup)
    assert one==two
    assert report.read_bytes()==prefix+r.MARKER+b'\n\nnewest body\n'+r.END_MARKER+b'\n'
    assert (backup/'accepted-pre-v21-report.md').read_bytes()==prefix
    report.write_bytes(b'changed'+report.read_bytes())
    with pytest.raises(ValueError,match='differs'):
        r.append_section(report,'x',backup)


def test_native_instance_id_matches_driver_and_hash_pin(tmp_path):
    selected=expected(1)
    selected[0]['instance_id']='sim.small.28100000'
    row=record(0)
    row['instance_id']='sim.small.28100000'
    save(tmp_path,'test-solver-small-v21-screen',row)
    summary=r.summarize(tmp_path,selected)
    assert summary['methods']['solver']['n_passed']==1
    assert 'fixed seed 0' in r.markdown(summary)
    assert 'instance seeds' in r.markdown(summary)
