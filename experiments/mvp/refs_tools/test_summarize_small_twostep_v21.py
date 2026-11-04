"""Two-step matched-pass reporting keeps failed cases in frozen denominators."""
import importlib.util
import json
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('twostep_v21_reporter',Path(__file__).with_name('summarize_small_twostep_v21.py'))
r=importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def expected(n=3):
    return [dict(path=f'test/small/ship/case-{i}.json',sha256=str(i),ship='feeder_500',instance_id=f'native-{i}') for i in range(n)]


def record(i,passed=True,shippable=None):
    return dict(instance_id=f'native-{i}',input_sha256=str(i),rung='small',
                result={'repaired':False,'status':'ok'},shippable=passed if shippable is None else shippable,
                report_binding_valid=True,source_before={'src/stowage/solver/twostep.py':'sourcehash'},
                report={'passed':passed,'violations':[] if passed else [{'rule_id':'hard.failure','severity':'hard'}],
                        'parts':{},'count':{'weighted_nonproductive':i+2}},
                timing={'total_s':1},lower_bound={'weighted':0})


def save(root,method,row,name=None):
    folder=r.SOURCES[method][0][0]
    path=root/folder/'instances'/(name or row['instance_id'])/'record.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(row))


def test_mutual_passes_conditional_with_failures_outside_population(tmp_path):
    for i in range(3):
        save(tmp_path,'solver',record(i,passed=i!=2))
        save(tmp_path,'twostep',record(i,passed=i!=1))
        save(tmp_path,'bounds',record(i))
    s=r.summarize(tmp_path,expected())
    overall=s['paired_mutual_pass'][-1]
    assert overall['frozen_n']==3 and overall['mutual_n']==1
    assert overall['twostep_pass']==2 and overall['solver_pass']==2
    assert overall['twostep']['sum']==overall['solver']['sum']==2
    assert overall['bound']['sum']==0 and overall['mean_delta_twostep_minus_solver']==0
    assert overall['ties']==1 and overall['excluded_from_mutual_n']==2
    assert s['methods']['twostep']['own_pass_rate']==pytest.approx(2/3)
    assert 'Conditional matched mutual passes' in r.markdown(s)
    assert s['twostep_source_evidence'][0]['twostep_source']['src/stowage/solver/twostep.py']=='sourcehash'


def test_checker_pass_but_unshippable_is_not_mutual(tmp_path):
    save(tmp_path,'solver',record(0))
    save(tmp_path,'twostep',record(0,shippable=False))
    s=r.summarize(tmp_path,expected(1))
    pair=s['paired_mutual_pass'][-1]
    assert pair['twostep_pass']==1 and pair['twostep_shippable']==0
    assert pair['mutual_n']==0 and pair['twostep']['mean'] is None
    assert pair['twostep']['sum'] is None and pair['mean_delta_twostep_minus_solver'] is None


def test_missing_attempts_keep_full_denominator_and_empty_groups(tmp_path):
    selected=expected(2)
    selected[1]['ship']='feeder_800'
    save(tmp_path,'twostep',record(0))
    s=r.summarize(tmp_path,selected)
    assert s['methods']['twostep']['own_pass_rate']==pytest.approx(.5)
    assert len(s['methods']['twostep']['missing_paths'])==1
    empty=s['paired_mutual_pass'][1]
    assert empty['ship']=='feeder_800' and empty['frozen_n']==1
    assert empty['mutual_n']==0 and empty['bound']['sum'] is None
    assert 'unattempted' in r.markdown(s)


def test_duplicate_and_unknown_inputs_rejected(tmp_path):
    row=record(0)
    save(tmp_path,'twostep',row)
    save(tmp_path,'twostep',row,'duplicate')
    with pytest.raises(ValueError,match='Duplicate'):
        r.summarize(tmp_path,expected(1))
    (tmp_path/r.SOURCES['twostep'][0][0]/'instances'/'duplicate'/'record.json').unlink()
    row=record(9)
    save(tmp_path,'solver',row)
    with pytest.raises(ValueError,match='Unmatched input'):
        r.summarize(tmp_path,expected(1))


def test_missing_bound_for_mutual_pair_rejected(tmp_path):
    for method in ('solver','twostep'):
        save(tmp_path,method,record(0))
    with pytest.raises(ValueError,match='lacks measured bound'):
        r.summarize(tmp_path,expected(1))


def test_prefix_guard_and_idempotent_append(tmp_path):
    report=tmp_path/'report.md'
    approved=tmp_path/'approved.md'
    prefix=b'Tiny accepted\r\nSmall accepted\n'
    report.write_bytes(prefix)
    approved.write_bytes(prefix)
    first=r.append_section(report,'first\n',approved)
    assert first==r.append_section(report,'latest\n',approved)
    assert report.read_bytes()==prefix+r.MARKER+b'\n\nlatest\n'+r.END_MARKER+b'\n'
    report.write_bytes(b'changed'+report.read_bytes())
    with pytest.raises(ValueError,match='Accepted prefix differs'):
        r.append_section(report,'body',approved)


def test_native_id_and_effective_seed_alignment(tmp_path):
    row=record(0)
    row['instance_id']='case-0'
    save(tmp_path,'twostep',row)
    with pytest.raises(ValueError,match='Native instance ID mismatch'):
        r.summarize(tmp_path,expected(1))
    (tmp_path/r.SOURCES['twostep'][0][0]/'instances'/'case-0'/'record.json').unlink()
    # Remove the canonical journal too: journal-only evidence must never disappear silently.
    (tmp_path/r.SOURCES['twostep'][0][0]/'records.jsonl').unlink()
    row=record(0)
    row['effective_seed']=1
    save(tmp_path,'twostep',row)
    row=record(0)
    row['effective_seed']=2
    save(tmp_path,'solver',row)
    with pytest.raises(ValueError,match='effective seed mismatch'):
        r.summarize(tmp_path,expected(1))


def test_no_plan_dead_end_diagnostic_does_not_invent_checker_rule(tmp_path):
    row=record(0,passed=False)
    row.update(report=None,failed_stage='solve',error='RuntimeError: No solver plan: dead_end')
    row['result'].update(status='dead_end',message='Every candidate breaks a placement rule or a completion bound')
    save(tmp_path,'twostep',row)
    s=r.summarize(tmp_path,expected(1))
    text=r.markdown(s)
    assert 'Failed or excluded two-step attempts' in text
    assert 'dead_end' in text and 'RuntimeError: No solver plan' in text
    assert 'Every candidate breaks a placement rule or a completion bound' in text
    assert s['methods']['twostep']['n_passed']==0
    assert r.h.first_failure(s['records']['twostep'][0]) is None
    assert s['paired_mutual_pass'][-1]['twostep']['sum'] is None


def test_optional_production_drift_receipt_is_hashed_and_disclosed(tmp_path):
    s=r.summarize(tmp_path,expected(1))
    path=tmp_path/'production-source-at-end.json'
    r.attach_production_receipt(s,path)
    assert 'production_source_at_end' not in s
    receipt=dict(frozen_version='4.3.0',live_version='4.4.0',checked_utc='now',
                 changed=['src/stowage/solver/twostep.py','src/stowage/solver/twostep_terminal.py'],
                 frozen={'src/stowage/solver/twostep.py':'f'*64},
                 live={'src/stowage/solver/twostep.py':'a'*64})
    path.write_text(json.dumps(receipt))
    r.attach_production_receipt(s,path)
    assert s['production_source_at_end']['receipt']==receipt
    assert s['production_source_at_end']['sha256']==r.h.digest(path)
    text=r.markdown(s)
    assert 'measurements are pinned to two-step `4.3.0`' in text
    assert 'live production was `4.4.0`' in text
    assert 'do not evaluate live 4.4.0' in text
    assert 'separate rerun request' in text and 'f'*64 in text and 'a'*64 in text
