"""Tiny standard-library promotion checks; never calls installer main()."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

spec=importlib.util.spec_from_file_location('d120_promote',Path(__file__).with_name('promote.py'))
promote=importlib.util.module_from_spec(spec)
spec.loader.exec_module(promote)


class PromotionGuards(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix='d120-promote-guards-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'src/stowage').mkdir(parents=True)
        (self.root/'params').mkdir()
        self.source=self.root/'src/stowage/file.py'
        self.source.write_text('value=1\n')
        (self.root/'params/file.toml').write_text('value=1\n')
        (self.root/'pyproject.toml').write_text('[project]\nname="synthetic"\n')
        self.pins=promote.live_manifest(self.root)

    def test_unchanged_manifest_passes(self):
        promote.require_live_epoch(self.root,self.pins)

    def test_new_source_file_is_rejected(self):
        (self.root/'src/stowage/foreign.py').write_text('foreign=True\n')
        with self.assertRaises(RuntimeError):promote.require_live_epoch(self.root,self.pins)

    def test_new_parameter_file_is_rejected(self):
        (self.root/'params/foreign.toml').write_text('foreign=true\n')
        with self.assertRaises(RuntimeError):promote.require_live_epoch(self.root,self.pins)

    def test_changed_source_is_rejected(self):
        self.source.write_text('value=2\n')
        with self.assertRaises(RuntimeError):promote.require_live_epoch(self.root,self.pins)

    def test_deleted_source_is_rejected(self):
        self.source.unlink()
        with self.assertRaises(RuntimeError):promote.require_live_epoch(self.root,self.pins)

    def test_pycache_is_ignored_like_decode(self):
        cache=self.root/'src/stowage/__pycache__'
        cache.mkdir()
        (cache/'file.pyc').write_bytes(b'ignored')
        promote.require_live_epoch(self.root,self.pins)

    def test_checker_decode_binding(self):
        pins={'src/stowage/file.py':self.pins['src/stowage/file.py']}
        promote.bind_checker_sources(dict(source_pins=pins),self.pins)
        with self.assertRaises(RuntimeError):
            promote.bind_checker_sources(dict(source_pins={'src/stowage/file.py':'wrong'}),self.pins)
        with self.assertRaises(RuntimeError):
            promote.bind_checker_sources(dict(source_pins={'missing':'wrong'}),self.pins)
        with self.assertRaises(RuntimeError):
            promote.bind_checker_sources(dict(source_pins={}),self.pins)

    def test_target_change_is_rejected(self):
        expected=promote.sha(self.source)
        promote.require_target(self.source,expected)
        self.source.write_text('foreign=True\n')
        with self.assertRaises(RuntimeError):promote.require_target(self.source,expected)

    def test_new_target_collision_is_rejected(self):
        target=self.root/'new.py'
        promote.require_target(target,None)
        target.write_text('foreign=True\n')
        with self.assertRaises(RuntimeError):promote.require_target(target,None)

    def test_new_target_dangling_symlink_is_rejected(self):
        target=self.root/'new.py'
        target.symlink_to(self.root/'does-not-exist')
        with self.assertRaises(RuntimeError):promote.require_target(target,None)

    def test_existing_target_symlink_is_rejected(self):
        target=self.root/'link.py'
        target.symlink_to(self.source)
        with self.assertRaises(RuntimeError):promote.require_target(target,promote.sha(self.source))


if __name__=='__main__':unittest.main(verbosity=2)
