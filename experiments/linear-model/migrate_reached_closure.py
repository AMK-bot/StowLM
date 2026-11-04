"""Preflight a tightly evidenced linear-calibration closure migration.

This command deliberately performs no cache or bank writes. A migration is
blocked if even one reached provider changed without a pinned equivalence
proof. In particular, a new liquid-CG implementation cannot be explained away
as an unrelated GZ/version change. Run this preflight before preparing or
installing replacement certificates; retain the original observations intact.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PACKAGE = ROOT/'src/stowage'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def query_node(raw, symbol):
    nodes = ast.parse(raw).body
    node = None
    for part in symbol.split('.'):
        node = next(item for item in nodes if isinstance(item, (ast.FunctionDef, ast.ClassDef))
                    and item.name == part)
        nodes = getattr(node, 'body', [])
    return node


def preflight(proof_path, baseline_path, query_proof_path):
    from stowage.linear.dependencies import dependency_contents, dependency_fingerprint
    baseline = json.loads(baseline_path.read_text())
    old = baseline['source_file_hashes_start']
    sources = dependency_contents('calibration')
    current = {name:sha(PACKAGE/name) for name in sources}
    changed = {name:dict(old_sha256=old.get(name), current_sha256=digest)
               for name,digest in current.items() if old.get(name) != digest}
    query = json.loads(query_proof_path.read_text())
    captured_path = ROOT/query['captured_excerpt_path']
    if sha(captured_path) != query['captured_excerpt_sha256']:
        raise ValueError('retained query excerpt checksum mismatch')
    captured = captured_path.read_text()
    # The retained verbatim read has the two function blocks, not the
    # intervening dataclass header. Add only that syntactic wrapper for AST
    # extraction; the original captured bytes/checksum remain untouched.
    marker = '\n    def centroid('
    if '\nclass LiquidTable' not in captured and marker in captured:
        first, second = captured.split(marker,1)
        captured = first+'\nclass LiquidTable:\n    def centroid('+second
    current_query = (PACKAGE/'checker/stability_liquid_tables.py').read_text()
    comparisons = {}
    for symbol in ('_stencil','LiquidTable.centroid'):
        first, second = query_node(captured,symbol), query_node(current_query,symbol)
        comparisons[symbol] = dict(
            text_equal=ast.get_source_segment(captured,first)==ast.get_source_segment(current_query,second),
            ast_equal=ast.dump(first,include_attributes=False)==ast.dump(second,include_attributes=False))
    blockers = []
    if not all(item['text_equal'] and item['ast_equal'] for item in comparisons.values()):
        blockers.append('reached table query excerpt differs')
    # Only these two reached query functions have a retained prechange source.
    # Every other currently reached module must match the pinned full baseline.
    # An additional equivalence migration needs a separate explicit audit;
    # this preflight never turns an arbitrary source difference into a pass.
    unmatched = [name for name in changed if name != 'checker/stability_liquid_tables.py']
    if unmatched:
        blockers.append('unproved reached source changes: '+', '.join(unmatched))
    closure = json.loads(proof_path.read_text()) if proof_path.exists() else None
    ready = isinstance(closure,dict) and (closure.get('migration_ready') is True or closure.get('status') == 'passed')
    if not ready:
        blockers.append('closure audit proof is missing or not passed')
    return dict(status='blocked' if blockers else 'preflight_passed', migration_applied=False,
        baseline=dict(path=str(baseline_path.relative_to(ROOT)),sha256=sha(baseline_path)),
        query_proof=dict(path=str(query_proof_path.relative_to(ROOT)),sha256=sha(query_proof_path)),
        retained_excerpt=dict(path=str(captured_path.relative_to(ROOT)),sha256=sha(captured_path)),
        closure_proof=dict(path=str(proof_path.relative_to(ROOT)),sha256=sha(proof_path) if proof_path.exists() else None),
        current_fingerprints={kind:dependency_fingerprint(kind) for kind in ('coefficient','calibration','liquid_reference')},
        reached_source_changes=changed, query_comparisons=comparisons, blockers=blockers,
        untouched='All coefficient/calibration caches, margin bank and 18 original observations remain unchanged.',
        next_step='Only after the reached-source proof passes: validate all old certificates/reference grids, prepare replacement identities and review an atomic bank migration.')


def main():
    parser = argparse.ArgumentParser()
    folder = HERE/'signed-family/d30/resume'
    parser.add_argument('--closure-proof',type=Path,default=folder/'closure-amendment-proof.json')
    parser.add_argument('--baseline',type=Path,default=folder/'d29-parameter-memo.json')
    parser.add_argument('--query-proof',type=Path,default=folder/'table-query-source-equivalence.json')
    parser.add_argument('--output',type=Path,default=folder/'reached-closure-preflight.json')
    args = parser.parse_args()
    result = preflight(args.closure_proof,args.baseline,args.query_proof)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(status=result['status'],blockers=result['blockers'],migration_applied=False)))
    if result['blockers']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
