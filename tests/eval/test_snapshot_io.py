"""Every parsed claim and digest comes from one immutable file read."""
import hashlib
import json
from pathlib import Path

import pytest

from eval.snapshot_io import read_snapshot


def test_replacement_cannot_change_snapshot_claims_or_digest(tmp_path, monkeypatch):
    path=tmp_path/'evidence.json'
    good=json.dumps({'official':True}).encode()
    bad=json.dumps({'diagnostic':True}).encode()
    path.write_bytes(good)
    read=Path.read_bytes
    calls=[]
    def replace_after_read(file):
        data=read(file)
        if file==path:
            calls.append(file)
            file.write_bytes(bad)
        return data
    monkeypatch.setattr(Path,'read_bytes',replace_after_read)
    snapshot=read_snapshot(path)
    assert calls==[path]
    assert snapshot.data==good and snapshot.json()=={'official':True}
    assert snapshot.sha256==hashlib.sha256(good).hexdigest()
    with pytest.raises(ValueError,match='changed since validated read'):
        snapshot.require_fresh()


def test_expected_digest_is_checked_against_the_returned_bytes(tmp_path):
    path=tmp_path/'one.json'
    path.write_bytes(b'{}')
    with pytest.raises(ValueError,match='checksum mismatch'):
        read_snapshot(path,'0'*64)
