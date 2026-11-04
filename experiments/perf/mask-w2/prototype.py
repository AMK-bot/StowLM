"""Scoped exact CPU prototypes; never changes src/tests or patches on import.
Run via UV_CACHE_DIR=/tmp/w2-mask-perf-uv UV_OFFLINE=1 OPENBLAS_NUM_THREADS=1
OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 STOWAGE_IN_PORT_WORKERS=1 uv run python
experiments/perf/mask-w2/prototype.py --bench --equivalence
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent

def module(path, name):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod)
    return mod

@contextmanager
def optimized(variants=()):
    """Only explicitly entered, process-local scope is patched, then restored."""
    from contextlib import ExitStack
    import stowage.moves._physical as physical
    import stowage.actions.expander as expander
    import stowage.moves._paths as paths
    variants=set(variants)
    with ExitStack() as stack:
        if 'strings' in variants:
            import stowage.conventions as conventions
            for name in ('parse_slot','parse_yard','cells_of_slot','is_valid_container_id'):
                original=getattr(conventions,name)
                cached=lru_cache(maxsize=65536)(original)
                def wrapper(value,_original=original,_cached=cached):
                    return _cached(value) if isinstance(value,str) else _original(value)
                for mod in list(sys.modules.values()):
                    if getattr(mod,'__name__','').startswith('stowage.') and getattr(mod,name,None) is original:
                        stack.enter_context(patch.object(mod,name,wrapper))
        if 'transaction' in variants:
            import inspect,textwrap
            from stowage.feasibility._engine import IncrementalEngine
            original_restow=IncrementalEngine._restow_places
            source=textwrap.dedent(inspect.getsource(original_restow))
            source=source.replace('replay = world','from stowage.moves._physical import Replay\n                replay = Replay(self.instance, world)')
            source=source.replace('replay = apply_move(self.instance, replay, move)','replay.move(move)')
            source=source.replace('evaluate(replay.ship)','evaluate(replay.state().ship)')
            # The original function globals are retained; only replay lifecycle changes.
            source=source.replace('    T = self.T', '    from stowage.frozen import active_stubs\n    import stowage.moves as moves_public\n    if \"moves.apply_move\" in active_stubs() or moves_public.apply_move is not _perf_native_apply_move:\n        return _perf_original_restow(self,st,boxes,slots)\n    T = self.T',1)
            import stowage.moves as moves_public
            globals_copy=dict(original_restow.__globals__,_perf_original_restow=original_restow,_perf_native_apply_move=moves_public.apply_move)
            namespace={};exec(source,globals_copy,namespace)
            stack.enter_context(patch.object(IncrementalEngine,'_restow_places',namespace['_restow_places']))
        if 'commit' in variants:
            from stowage.moves._physical import Replay
            from stowage.moves import IllegalMoveError
            original_commit=expander.Expander._validated_commit
            native_apply_move=expander.apply_move
            def validated_commit(self,trial,moves):
                from stowage.frozen import active_stubs
                if 'moves.apply_move' in active_stubs() or expander.apply_move is not native_apply_move:
                    return original_commit(self,trial,moves)
                replay=Replay(self.instance,self.world_state())
                try:
                    for move in moves:replay.move(move)
                except IllegalMoveError as exc:
                    raise ValueError(f'unexpandable: {exc.violation.rule_id}: {exc.violation.message}') from exc
                self.__dict__.update(trial.__dict__)
                return moves
            stack.enter_context(patch.object(expander.Expander,'_validated_commit',validated_commit))
        if 'geometry' in variants:
            # Local bounded engine-run cache; identity retains actual model refs.
            geometries={}
            def cached_paths(instance, move, *, port=None):
                port=instance.port if port is None else port
                if not paths.rule_freeze_v1_enabled() or port.profile_kind=='next_call_environment':
                    return paths.CrossingPath()
                key=(id(instance),id(port))
                hit=geometries.get(key)
                if hit is None:
                    hit=geometries[key]=(instance,port,paths._CrossingGeometry(instance,port))
                return paths._indexed_crossing_paths(move,port=port,geometry=hit[2])
            # Existing imported aliases are all patched, preserving the shared algorithm.
            original=paths.crossing_paths
            for mod in list(sys.modules.values()):
                if getattr(mod,'__name__','').startswith('stowage.') and getattr(mod,'crossing_paths',None) is original:
                    stack.enter_context(patch.object(mod,'crossing_paths',cached_paths))
        if 'world' in variants:
            from stowage.model import Placement
            from stowage.model.yard import YardStack
            def world_state(self):
                base=self._base_state
                return base.model_copy(update={
                    'yard':base.yard.model_copy(update={
                        'stacks':[YardStack(key=k,length_ft=self.ylen[k],boxes=list(v)) for k,v in sorted(self.yard.items())],
                        'quay_buffer':list(self.buffer)}),
                    'ship':base.ship.model_copy(update={
                        'placements':sorted([Placement(container_id=cid,slot=loc[1]) for cid,loc in self.where.items() if loc[0]=='ship'],key=lambda p:p.slot),
                        'tank_masses_kg':dict(base.ship.tank_masses_kg),
                        'covers_off':dict(self.covers_off)})})
            stack.enter_context(patch.object(expander.Expander,'world_state',world_state))
        if 'leaves' in variants:
            # Reuse validation of identical scalar leaf data; return independent copies.
            Placement,YardStack=physical.Placement,physical.YardStack
            @lru_cache(maxsize=100000)
            def place(slot,cid):
                return Placement(slot=slot,container_id=cid)
            @lru_cache(maxsize=20000)
            def yard(key,length,boxes):
                return YardStack(key=key,length_ft=length,boxes=list(boxes))
            def cached_placement(*,slot,container_id):
                return place(slot,container_id).model_copy()
            def cached_yard(*,key,length_ft,boxes):
                return yard(key,length_ft,tuple(boxes)).model_copy(update={'boxes':list(boxes)})
            stack.enter_context(patch.object(physical,'Placement',cached_placement))
            stack.enter_context(patch.object(physical,'YardStack',cached_yard))
        yield

VARIANTS={'baseline':(), 'geometry':('geometry',), 'world':('world',), 'leaves':('leaves',),
          'combined':('geometry','world','leaves'), 'strings':('strings',), 'transaction':('transaction',), 'best':('strings','transaction','world','geometry'), 'commit':('commit',), 'recommended':('strings','transaction','commit')}

def benchmark(repeats=3,rungs=('medium','full'),variants=None):
    cost=module('tests/llm/test_mask_cost.py','prototype_cost')
    rows=[]
    import signal
    signal.signal(signal.SIGALRM,cost._expired)
    for variant in (variants or VARIANTS):
        for rung in rungs:
            with optimized(VARIANTS[variant]):
                row=cost.probe(rung,repeats)
            row['variant']=variant; rows.append(row)
            print(json.dumps(row),flush=True)
            (OUT/'prototype-results.json').write_text(json.dumps(rows,indent=2)+'\n')
    return rows

def fingerprints(instance,witness,tokenizer,all_actions,counts):
    from llm.stowlm.format import ActionCodec
    from llm.stowlm.mask import MaskedDecoder
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig,make_feasibility_engine
    codec=ActionCodec(tokenizer,[b.id for b in instance.containers])
    decoder=MaskedDecoder(make_feasibility_engine(instance,get_action_space('boxslot'),config=FeasibilityConfig(extra={'global_screen':0})),codec)
    reference=make_feasibility_engine(instance,get_action_space('boxslot'),config=FeasibilityConfig(extra={'global_screen':0})) if all_actions else None
    digest=hashlib.sha256()
    for expected in witness:
        offered=reference.legal_actions() if all_actions else (expected,)
        counts['prefixes']+=1;counts['offered_actions']+=len(offered) if all_actions else 0
        # Empty states still contribute their entire root language.
        digest.update(repr(decoder.allowed_token_ids()).encode())
        for action in offered:
            for token in codec.encode_action(action):
                allowed=decoder.allowed_token_ids()
                digest.update(repr(allowed).encode())
                assert token in allowed,(instance.id,action,allowed)
                decoder.advance(token);counts['token_boundaries']+=1
            decoder.pop_action()
        for token in codec.encode_action(expected):
            decoder.advance(token)
        if reference is not None: reference.push(expected)
    assert decoder.engine.is_complete()
    for token in codec.end_plan_tokens:
        digest.update(repr(decoder.allowed_token_ids()).encode());decoder.advance(token)
    assert decoder.status=='complete'
    return digest.hexdigest()

def equivalence(variants=None):
    from llm.stowlm.format import load_tokenizer
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.model import Instance,MovePlan,CheckReport
    from stowage.checker.full import report_matches_inputs
    from stowage.actions.space import Place
    gate=module('tests/llm/test_mask_rule_freeze_agreement.py','prototype_gate')
    tokenizer=load_tokenizer();cases=[]
    for active in (False,True):
        for vessel in ('feeder_500','feeder_800','feeder_1700'):
            for rung in ('tiny','small'):
                for h in (4,8):
                    with rule_freeze_v1(enabled=active):
                        instance,witness=gate.FLEET._case(vessel,rung,h,seed=23)
                    instance.port.yard_max_tiers=5
                    for b in instance.port.yard.blocks:b.max_tiers=5
                    for e in instance.port.equipment:e.stack_tiers=5
                    cases.append(('corpus',active,instance,witness,True))
    for p in sorted(Path('/private/tmp/w2-feas-round2-bound').glob('*.json')):
        bound=json.loads(p.read_text())
        assert bound['sha256']=={k:hashlib.sha256(v.encode()).hexdigest() for k,v in bound['payloads'].items()}
        instance=Instance.model_validate_json(bound['payloads']['instance'])
        plan=MovePlan.model_validate_json(bound['payloads']['plan'])
        report=CheckReport.model_validate_json(bound['payloads']['report'])
        assert report.passed and report_matches_inputs(report,instance,plan)
        assert all(part.ran and not part.error for part in report.parts.values())
        cases.append(('bound',bound['activation'],instance,[Place(**v) for v in bound['actions']],False))
    assert sum(c[0]=='bound' for c in cases)==24
    expected={};results=[]
    for variant in (variants or VARIANTS):
        counts=Counter();start=perf_counter()
        with optimized(VARIANTS[variant]):
            for i,(kind,active,instance,witness,all_actions) in enumerate(cases):
                with rule_freeze_v1(enabled=active):
                    local=Counter(); digest=fingerprints(instance,witness,tokenizer,all_actions,local)
                counts.update({kind+'_'+k:v for k,v in local.items()})
                counts[kind+'_cases']+=1
                if variant=='baseline':expected[i]=digest
                else:assert digest==expected[i],(variant,kind,instance.id,digest,expected[i])
        row={'variant':variant,'equal':True,'elapsed_s':perf_counter()-start,'counts':dict(counts),
             'baseline_digest':hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest()}
        results.append(row);print(json.dumps(row),flush=True)
        (OUT/'prototype-equivalence.json').write_text(json.dumps(results,indent=2)+'\n')
    return results

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bench',action='store_true');p.add_argument('--equivalence',action='store_true');p.add_argument('--repeats',type=int,default=3);p.add_argument('--rung',nargs='+',default=['medium','full']);p.add_argument('--variant',nargs='+');a=p.parse_args()
    if a.bench:benchmark(a.repeats,a.rung,a.variant)
    if a.equivalence:equivalence(a.variant)
