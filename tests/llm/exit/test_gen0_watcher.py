import importlib.util
import json
from pathlib import Path
import sys


def module():
    path=Path(__file__).resolve().parents[3]/'experiments/mvp/exit/watch_gen0.py'
    spec=importlib.util.spec_from_file_location('test_gen0_watcher_module',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def test_once_never_creates_started_or_reads_corpus(tmp_path,monkeypatch,capsys):
    watch=module()
    (tmp_path/'deployment-status.json').write_text('{"state":"waiting_for_time_or_pilot2"}')
    monkeypatch.setattr(sys,'argv',['watch','--out',str(tmp_path),'--once'])
    assert watch.main()==0
    assert not (tmp_path/'STARTED').exists()
    report=json.loads((tmp_path/'generation-0-report.json').read_text())
    assert report['status']=='not_started' and report['phase_wall_s']['decode_s'] is None
    assert 'waiting_for_time_or_pilot2' in capsys.readouterr().out


def test_completed_generation_reports_sizing_and_source_counts(tmp_path,monkeypatch,capsys):
    watch=module();case=tmp_path/'gen-000';case.mkdir()
    (case/'summary.json').write_text(json.dumps(dict(instance_count=3,winners_by_source_detail={
        'model':1,'polished_from_model':1,'baseline_twostep':1},preference_pairs=2,
        timings={'wall_s':25.,'decode_wall_s':10.,'fitness_s':.01})))
    (tmp_path/'sizing-after-gen0.json').write_text('{"maps":12,"k":6}')
    monkeypatch.setattr(sys,'argv',['watch','--out',str(tmp_path)])
    assert watch.main()==0
    report=json.loads((tmp_path/'generation-0-report.json').read_text())
    assert report['winners']['model_alone']==1 and report['pairs']['admitted']==2
    assert 'generation0_finished' in capsys.readouterr().out
