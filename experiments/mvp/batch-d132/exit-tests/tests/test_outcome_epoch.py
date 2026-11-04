"""CPU-only staged D132 policy tests; never import MLX or alter live source."""
import ast
import importlib.util
from pathlib import Path

import pytest

STAGE=Path('/private/tmp/stowlm-d132-exit-qualified-runtime-20261030/src/llm/exit')


def policy():
    spec=importlib.util.spec_from_file_location('_d132_outcome_test',STAGE/'outcome_epoch.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_ast_closure_ignores_orchestration_but_pins_used_helpers():
    module=policy()
    original='import math\nLIMIT=2\ndef helper(x): return x+LIMIT\ndef evaluate(x): return helper(x)\ndef train(): return 1\n'
    before=module.ast_closure(original,['evaluate'])
    assert module.ast_closure(original.replace('def train(): return 1','def train(): return 100'),['evaluate'])==before
    assert module.ast_closure(original.replace('LIMIT=2','LIMIT=3'),['evaluate'])!=before
    assert module.ast_closure(original.replace('x+LIMIT','x-LIMIT'),['evaluate'])!=before
    assert module.ast_closure('# comment\n'+original,['evaluate'])==before


def test_missing_or_ambiguous_ast_entry_fails_closed():
    module=policy()
    with pytest.raises(ValueError,match='entry point missing'):module.ast_closure('def train():pass',['evaluate'])
    with pytest.raises(ValueError,match='ambiguous'):module.ast_closure('def evaluate():pass\nevaluate=other',['evaluate'])


def test_module_initialization_cannot_silently_replace_evaluator():
    module=policy();source='def evaluate():return 1\ndef replacement():return 2\n'
    assert module.ast_closure(source,['evaluate'])!=module.ast_closure(source+'if True:\n evaluate=replacement\n',['evaluate'])
    assert module.ast_closure(source,['evaluate'])!=module.ast_closure(
        source+"unused = globals().__setitem__('evaluate', lambda: 2)\n",['evaluate'])


@pytest.mark.parametrize('initializer',[
    '@patch_evaluate\ndef unused(): pass\n',
    'def unused(default=patch_evaluate()): pass\n',
    'class Unused:\n patch_evaluate()\n',
])
def test_unused_definition_initializers_are_pinned(initializer):
    module=policy();source='def evaluate():return 1\n'
    assert module.ast_closure(source,['evaluate'])!=module.ast_closure(source+initializer,['evaluate'])


def test_worker_must_match_loaded_epoch_not_just_current_disk():
    tree=ast.parse((STAGE/'runner.py').read_text())
    function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='audit_verify_job')
    namespace={'_LOADED_AUDIT_EPOCH':{'loaded':'old'},'assert_audit_epoch':lambda pins:pytest.fail('disk check hid mismatched worker')}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'staged_worker','exec'),namespace)
    with pytest.raises(RuntimeError,match='worker loaded outcome epoch differs'):
        namespace['audit_verify_job']('no-map',{},'no-output',90,{'loaded':'new'})


def test_catalogue_resources_and_finisher_are_in_policy():
    module=policy()
    assert 'src/llm/exit/finisher_outcomes.py' in module.WHOLE_FILES
    assert '.toml' in (STAGE/'outcome_epoch.py').read_text()
    assert 'src/llm/stowlm/train.py' in module.EXCLUDED_FILES
    assert not any('resources.py' in p or 'reverify.py' in p or 'capacity.py' in p for p in module.WHOLE_FILES)


@pytest.mark.parametrize('key',['STOWAGE_STABILITY_DIRECT','STOWAGE_STABILITY_FREE_SURFACE_METHOD','STOWAGE_STABILITY_LIQUID_DIRECT'])
def test_numeric_environment_switch_changes_pin(monkeypatch,key):
    module=policy();module.PACKAGES=()
    monkeypatch.setenv(key,'0')
    original=module._environment()
    monkeypatch.setenv(key,'1')
    assert module._environment()!=original


def test_case_sensitive_import_inventory_does_not_mistake_exported_class(tmp_path):
    module=policy();path=tmp_path/'src/stowage/model';path.mkdir(parents=True)
    (path/'instance.py').write_text('class Instance:pass')
    pinned={'src/stowage/model/__init__.py':'hash','src/stowage/model/instance.py':'hash'}
    tree=ast.parse('from stowage.model import Instance')
    module._project_imports_covered(tree,tmp_path,pinned,{'src/stowage/model/instance'})


def test_unknown_operational_import_in_outcome_closure_refused(tmp_path):
    module=policy();tree=ast.parse('from llm.exit.resources import reservation')
    with pytest.raises(RuntimeError,match='unresolved audit outcome project import'):
        module._project_imports_covered(tree,tmp_path,{},set())
