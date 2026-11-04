"""Standard-library-only snapshot checks; never import numerical/native modules."""
import ast
import hashlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / 'src/stowage').is_dir())
PACKAGES = ('frozen_frontier', 'frozen_securing', 'frozen_engine12', 'frozen_reporting')


class SnapshotTests(unittest.TestCase):
    def test_manifest_bytes(self):
        manifest = json.loads((HERE / 'source-manifest.json').read_text())
        sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        for row in manifest['files']:
            # Original bytes were verified during freezing. The original lab
            # can now change or disappear without affecting this runtime.
            self.assertIsInstance(row['source'], str)
            self.assertFalse(Path(row['source']).is_absolute())
            self.assertNotIn('..', Path(row['source']).parts)
            self.assertRegex(row['source_sha256'], r'^[0-9a-f]{64}$')
            self.assertTrue((HERE / row['destination']).resolve().is_relative_to(HERE))
            self.assertEqual(sha(HERE / row['destination']), row['copied_sha256'], row['destination'])
        for name, digest in manifest['generated'].items():
            self.assertTrue((HERE / name).resolve().is_relative_to(HERE))
            self.assertEqual(sha(HERE / name), digest, name)
        payload = json.dumps(manifest['files'], sort_keys=True).encode()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), manifest['snapshot_sha256'])

    def test_import_closure(self):
        allowed = set(sys.stdlib_module_names) | set(PACKAGES) | {
            'frozen_env', 'frozen_checks', 'stowage', 'numpy', 'scipy', 'ortools'}
        for package in PACKAGES:
            for path in (HERE / package).rglob('*.py'):
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertIn(alias.name.split('.')[0], allowed, str(path))
                    if not isinstance(node, ast.ImportFrom):
                        continue
                    if node.level:
                        base = path.parent
                        for _ in range(node.level - 1):
                            base = base.parent
                        if node.module:
                            target = base.joinpath(*node.module.split('.'))
                            self.assertTrue(target.with_suffix('.py').exists()
                                            or (target / '__init__.py').exists(), str(path))
                    else:
                        self.assertIn(node.module.split('.')[0], allowed, str(path))
                        if node.module in {'stowage.checker', 'stowage.checker.full'}:
                            self.assertNotIn('check', [a.name for a in node.names], str(path))

    def test_observer_identity_and_error_propagation(self):
        checker = types.ModuleType('stowage.checker')
        report, instance, plan = object(), object(), object()
        calls = []
        checker.check = lambda i, p: calls.append(('native', i, p)) or report
        fake = {'frozen_env': types.ModuleType('frozen_env'),
                'stowage': types.ModuleType('stowage'), 'stowage.checker': checker}
        namespace = {}
        with patch.dict(sys.modules, fake):
            exec(compile((HERE / 'frozen_checks.py').read_text(), 'frozen_checks.py', 'exec'), namespace)
        namespace['set_observer'](lambda i, p, r: calls.append(('observer', i, p, r)))
        self.assertIs(namespace['check'](instance, plan), report)
        self.assertEqual(calls, [('native', instance, plan), ('observer', instance, plan, report)])
        def fail(*args):
            raise RuntimeError('observer failure')
        namespace['set_observer'](fail)
        with self.assertRaisesRegex(RuntimeError, 'observer failure'):
            namespace['check'](instance, plan)
        namespace['set_observer'](None)
        self.assertIs(namespace['check'](instance, plan), report)


if __name__ == '__main__':
    unittest.main()
