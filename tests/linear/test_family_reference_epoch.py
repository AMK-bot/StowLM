"""A changed on-disk reference must not retain an older checker lookup memo."""
from types import SimpleNamespace


def test_reference_epoch_clears_stale_table_and_manifest_memos(monkeypatch):
    import stowage.linear.family_calibration as family
    import stowage.checker.stability_liquid as liquid
    import stowage.checker.stability_liquid_tables as tables
    calls=[]
    monkeypatch.setattr(family,'_REFERENCE_EPOCH',None)
    monkeypatch.setattr(liquid,'_read_manifest',SimpleNamespace(cache_clear=lambda:calls.append('manifest')))
    monkeypatch.setattr(tables,'_lookup',SimpleNamespace(cache_clear=lambda:calls.append('table')))
    first={'method':1,'direct':False,'files':{'manifest':None}}
    family.synchronize_reference(first)
    assert calls==['manifest','table']
    family.synchronize_reference(first.copy())
    assert calls==['manifest','table']
    family.synchronize_reference({'method':1,'direct':False,'files':{'manifest':'new-qualified-bytes'}})
    assert calls==['manifest','table','manifest','table']
