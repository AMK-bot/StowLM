"""D132 AUDIT-ONLY outcome dependencies; full training epochs remain separate.

Mixed orchestrator modules are pinned by the AST closure of evaluation entry
points and the globals/imports they use. Training/reverification/queue edits do
not void measured audits. Missing dependencies and changed outcome code fail
closed; existing full-source training receipts are never weakened by this API.
"""
from __future__ import annotations

import ast
import builtins
from contextlib import contextmanager
from copy import deepcopy
import hashlib
from importlib import metadata
import json
import importlib.machinery
import io
import os
from pathlib import Path
import platform
import sys
import tomllib


ROOT=next(parent for parent in Path(__file__).resolve().parents if (parent/'pyproject.toml').is_file())
SCHEMA='exit-audit-outcome-epoch-v1'
WHOLE_TREES=('src/stowage','src/llm/stowlm','src/llm/securing','src/llm/lookahead',
             'src/llm/instructions','src/llm/maskspeed','src/llm/spark')
EXCLUDED_FILES=frozenset(('src/llm/stowlm/train.py',))
WHOLE_FILES=('src/llm/__init__.py','src/llm/exit/__init__.py',
    'src/llm/exit/selection.py','src/llm/exit/audit_runtime.py',
    'src/llm/exit/securing_guard.py','src/llm/exit/checker_watchdog.py',
    'src/llm/exit/outcomes.py','src/llm/exit/final_recipe.py','src/llm/exit/identity.py',
    'src/llm/exit/pruning.py','src/llm/exit/finisher_outcomes.py')
MIXED={
    'src/llm/exit/runner.py':('adapter_identity','write_json','read_json','audit_verify_job','evaluate','ensure_baseline'),
    'src/llm/exit/runtime.py':('decoder_factory','_resident_for','_sample_batches','generate'),
    'src/llm/exit/replay.py':('checker_epoch',),
    'src/llm/exit/audit.py':('audit_decision','frozen_audit_paths'),
    'src/llm/exit/metrics.py':('_rows','compare_validation'),
    'experiments/mvp/gold_tools/driver.py':('scoped_ballast_v4',),
}
PACKAGES=('mlx','mlx-lm','tokenizers','numpy','scipy','pydantic','ortools')
MODEL_METADATA=('config.json','generation_config.json','tokenizer.json','tokenizer_config.json',
                'model.safetensors.index.json')
_PROCESS=None


def _sha(value):return hashlib.sha256(value).hexdigest()
def _canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def _closure_tree(source,entrypoints):
    """Hash executable AST and transitive module globals, excluding other jobs.

    New helpers called by an entry point are automatically included. A global
    redefinition/monkeypatch is ambiguous and refused, rather than silently using
    the first definition. Comments/line-number shifts do not change the epoch.
    """
    tree=ast.parse(source);bindings={};nodes={}
    for index,node in enumerate(tree.body):
        names=[]
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
            names=[node.name]
        elif isinstance(node,(ast.Import,ast.ImportFrom)):
            names=[alias.asname or (alias.name.split('.')[0] if isinstance(node,ast.Import) else alias.name)
                   for alias in node.names]
            if '*' in names:raise ValueError('audit outcome dependency cannot use star imports')
        elif isinstance(node,(ast.Assign,ast.AnnAssign)):
            targets=node.targets if isinstance(node,ast.Assign) else [node.target]
            names=[item.id for target in targets for item in ast.walk(target) if isinstance(item,ast.Name)]
        for name in names:
            if name in bindings:raise ValueError('ambiguous module binding in audit dependency: '+name)
            bindings[name]=index
        nodes[index]=node
    missing=set(entrypoints)-bindings.keys()
    if missing:raise ValueError('audit outcome entry point missing: '+repr(sorted(missing)))
    def inert(value):
        if value is None or isinstance(value,(ast.Constant,ast.Name)):return True
        if isinstance(value,(ast.Tuple,ast.List,ast.Set)):return all(inert(v) for v in value.elts)
        if isinstance(value,ast.Dict):return all(k is not None and inert(k) and inert(v) for k,v in zip(value.keys,value.values))
        return False

    selected={bindings[name] for name in entrypoints}
    for index,node in nodes.items():
        declaration=isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,
                                    ast.Import,ast.ImportFrom,ast.Assign,ast.AnnAssign))
        doc=isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str)
        main_guard=(isinstance(node,ast.If) and not node.orelse
            and ast.dump(node.test,include_attributes=False)==ast.dump(
                ast.parse("__name__ == '__main__'",mode='eval').body,include_attributes=False))
        # Module initialization can change physics/BLAS settings or replace an
        # evaluator. Pin executable top-level statements and their dependencies.
        if not declaration and not doc and not main_guard:selected.add(index)
        # An unused assignment may execute a call that mutates globals or a
        # numerical library; only inert declarations can safely stay outside.
        if isinstance(node,(ast.Assign,ast.AnnAssign)):
            if not inert(node.value) or isinstance(node,ast.AnnAssign) and not inert(node.annotation):
                selected.add(index)
        if isinstance(node,ast.ClassDef):selected.add(index)  # Class bodies/metaclasses execute at import.
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            arguments=[*node.args.posonlyargs,*node.args.args,*node.args.kwonlyargs]
            arguments+=[arg for arg in (node.args.vararg,node.args.kwarg) if arg is not None]
            initializers=[*node.args.defaults,*node.args.kw_defaults,
                          *(arg.annotation for arg in arguments),node.returns]
            if node.decorator_list or any(not inert(value) for value in initializers):
                selected.add(index)
    pending=list(selected)
    while pending:
        index=pending.pop()
        references={item.id for item in ast.walk(nodes[index]) if isinstance(item,ast.Name) and isinstance(item.ctx,ast.Load)}
        for name in references & bindings.keys():
            dependency=bindings[name]
            if dependency not in selected:selected.add(dependency);pending.append(dependency)
    selected_nodes=[nodes[index] for index in sorted(selected)]
    return ast.Module(body=selected_nodes,type_ignores=[])


def ast_closure(source,entrypoints):
    return _sha(ast.dump(_closure_tree(source,entrypoints),include_attributes=False).encode())


def _project_imports_covered(tree,root,pinned,actual_modules):
    for node in ast.walk(tree):
        modules=[]
        if isinstance(node,ast.Import):modules=[a.name for a in node.names]
        elif isinstance(node,ast.ImportFrom) and node.module and not node.level:
            modules=[node.module]
            for alias in node.names:
                candidate=node.module+'.'+alias.name
                # APFS is case-insensitive: model.Instance is an exported class,
                # not the lowercase model/instance.py module. Match inventory
                # strings exactly, never infer modules via is_file().
                if 'src/'+candidate.replace('.','/') in actual_modules:modules.append(candidate)
        for module in modules:
            if not module.startswith(('llm','stowage')):continue
            if module=='llm.exit.outcome_epoch':continue  # @policy hashes this complete module.
            base='src/'+module.replace('.','/')
            candidates=(base+'.py',base+'/__init__.py')
            if not any(name in pinned or name+'#ast' in pinned for name in candidates):
                raise RuntimeError('unresolved audit outcome project import: '+module)


def _environment():
    result={'python':sys.version,'machine':platform.machine(),'system':platform.system()}
    result['numerical_environment']={key:os.environ.get(key) for key in (
        'STOWAGE_STABILITY_DIRECT','STOWAGE_STABILITY_FREE_SURFACE_METHOD','STOWAGE_STABILITY_LIQUID_DIRECT',
        'OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS')}
    for name in PACKAGES:
        try:
            distribution=metadata.distribution(name)
        except metadata.PackageNotFoundError as error:
            raise RuntimeError('audit outcome package metadata unavailable: '+name) from error
        record=distribution.read_text('RECORD')
        if not record:raise RuntimeError('audit outcome package build manifest unavailable: '+name)
        result[name]={'version':distribution.version,'wheel_record_sha256':_sha(record.encode())}
    return result


def snapshot(root=None):
    """Stable audit source/config/build pins; never load weights or initialize MLX.

    Exact base/adapter content identity remains an independent cache binding in
    the existing planner. Hashing enormous weight shards on every check would
    duplicate that admission. Tokenizer/config bytes are small primary inputs.
    """
    root=Path(root or ROOT).resolve();pins={};trees=[]
    for directory in WHOLE_TREES:
        base=root/directory
        if not base.is_dir():raise RuntimeError('audit outcome source tree missing: '+directory)
        paths=[p for p in base.rglob('*') if p.is_file() and p.suffix in ('.py','.json','.toml','.csv','.npz','.npy','.fix') and '__pycache__' not in p.parts]
        if not paths:raise RuntimeError('audit outcome source tree empty: '+directory)
        for path in sorted(paths):
            name=str(path.relative_to(root))
            if name not in EXCLUDED_FILES:
                pins[name]=_sha(path.read_bytes())
                if path.suffix=='.py':trees.append(ast.parse(path.read_text()))
    for name in WHOLE_FILES:
        pins[name]=_sha((root/name).read_bytes())
        trees.append(ast.parse((root/name).read_text()))
    for name,entries in MIXED.items():
        tree=_closure_tree((root/name).read_text(),entries);trees.append(tree)
        pins[name+'#ast']=_sha(ast.dump(tree,include_attributes=False).encode())
    modules={str(p.relative_to(root)).removesuffix('.py')
             for p in (root/'src').rglob('*.py') if '__pycache__' not in p.parts}
    for tree in trees:_project_imports_covered(tree,root,pins,modules)
    config=root/'params'
    paths=[p for p in config.rglob('*') if p.is_file() and p.suffix in ('.toml','.json')]
    if not paths:raise RuntimeError('audit outcome configuration inventory missing')
    for path in sorted(paths):pins[str(path.relative_to(root))]=_sha(path.read_bytes())
    for path in sorted((config/'fleet').glob('*.toml')):
        hull=tomllib.loads(path.read_text()).get('hull_form',{})
        if hull.get('offsets_path'):
            target=(root/hull['offsets_path']).resolve()
            if not target.is_relative_to(root/'data'):raise RuntimeError('hull input escapes data root')
            actual=_sha(target.read_bytes())
            if actual!=hull.get('offsets_sha256'):raise RuntimeError('declared hull input checksum differs')
            pins[str(target.relative_to(root))]=actual
    for name in MODEL_METADATA:
        path=root/'models/glimmer-30b-4bit'/name
        pins[str(path.relative_to(root))]=_sha(path.read_bytes())
    pins['@environment']=_sha(_canonical(_environment()))
    pins['@policy']=_sha(Path(__file__).read_bytes())
    pins['@schema']=SCHEMA
    return pins


def process_snapshot():
    """Capture ONCE before importing evaluation/model modules in a fresh process."""
    global _PROCESS
    if _PROCESS is None:_PROCESS=snapshot()
    return deepcopy(_PROCESS)


def assert_current(expected):
    if not isinstance(expected,dict) or not expected or snapshot()!=expected:
        raise RuntimeError('audit outcome dependency epoch changed; fresh-process evaluation required')
    return expected


def epoch_id(expected):return _sha(_canonical(expected))[:16]


class OutcomeEpochMismatch(BaseException):
    """Never let checker Exception handlers convert source drift to physics FAIL."""


@contextmanager
def guarded_outcome_reads(expected, *, root=None):
    """Child-local immutable source/resource reads for a single full check.

    Validate bytes actually compiled/imported, bypassing stale pyc files. Pin
    primary resource bytes actually consumed too, so change-and-revert cannot
    assemble a hybrid physics result between manifest snapshots. No weights or
    packages are imported here. The child is single-threaded and never models.
    """
    root=Path(root or ROOT).resolve();imports={};resources={}
    original_code=importlib.machinery.SourceFileLoader.get_code
    original_open,original_io_open=builtins.open,io.open

    def binding(filename,data):
        try:name=str(Path(filename).resolve().relative_to(root))
        except (ValueError,TypeError):return None
        if name in expected:
            actual=_sha(data)
            if actual!=expected[name]:raise OutcomeEpochMismatch('outcome bytes changed: '+name)
            return name,actual
        if name+'#ast' in expected:
            actual=ast_closure(data.decode(),MIXED[name])
            if actual!=expected[name+'#ast']:raise OutcomeEpochMismatch('outcome AST changed: '+name)
            return name+'#ast',actual
        if name.startswith(('src/llm/','src/stowage/')) and name.endswith('.py'):
            raise OutcomeEpochMismatch('unbound outcome module imported: '+name)
        return None

    def get_code(loader,fullname):
        filename=loader.get_filename(fullname)
        try:name=str(Path(filename).resolve().relative_to(root))
        except ValueError:return original_code(loader,fullname)
        if name not in expected and name+'#ast' not in expected:
            if name.startswith(('src/llm/','src/stowage/')):
                raise OutcomeEpochMismatch('unbound outcome module imported: '+name)
            return original_code(loader,fullname)
        data=loader.get_data(filename)
        key,value=binding(filename,data);imports[key]=value
        return loader.source_to_code(data,filename)

    def immutable_open(file,mode='r',buffering=-1,encoding=None,errors=None,newline=None,closefd=True,opener=None):
        if isinstance(file,int) or any(flag in mode for flag in 'wax+'):
            return original_io_open(file,mode,buffering,encoding,errors,newline,closefd,opener)
        try:name=str(Path(file).resolve().relative_to(root))
        except (ValueError,TypeError):
            return original_io_open(file,mode,buffering,encoding,errors,newline,closefd,opener)
        if name not in expected and name+'#ast' not in expected:
            return original_io_open(file,mode,buffering,encoding,errors,newline,closefd,opener)
        with original_io_open(file,'rb') as stream:data=stream.read()
        key,value=binding(file,data);resources[key]=value
        stream=io.BytesIO(data);stream.name=str(file)
        if 'b' in mode:return stream
        return io.TextIOWrapper(stream,encoding=encoding,errors=errors,newline=newline)

    importlib.machinery.SourceFileLoader.get_code=get_code
    builtins.open=io.open=immutable_open
    try:yield {'imports':imports,'resources':resources}
    finally:
        builtins.open,io.open=original_open,original_io_open
        importlib.machinery.SourceFileLoader.get_code=original_code
