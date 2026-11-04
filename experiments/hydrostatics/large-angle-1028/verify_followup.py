"""Provenance and numerical integrity checks for the leader's follow-up."""
import ast,difflib,hashlib,json,math,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2];OUT=HERE/'followup'
sys.path.insert(0,str(HERE))
from historical_fixture import HISTORICAL_INSTANCE,verify_historical_identity,require_fixture_hash
from followup_numerics import verify_curve_areas,verify_feeder_table,verify_iges_reference
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
archive=OUT/'feeder-driver-C1-source.txt'
checks=0

# Exact whole-file epochs, not a permit for arbitrary wrapper/port changes.
# New epochs require a new retained snapshot, full patch and explicit audit.
_EPOCH_AUDIT_PATH='followup/snapshots/epoch-guard-audit.json'
_EPOCH_AUDIT_SHA256='3e255a056527dc6f95913aa9ca9e47029ed7b9bba1044846421cea3d43f21dfe'
_AUDITED_SOURCE_SNAPSHOTS={
 'src/stowage/checker/stability.py':{
  '93efb614863f3eca66b0aa2323207234d7f478caf3b3692a27b0429bac1f53e7':'followup/snapshots/stability-reconstructed-pre-d64.py',
  'bac930becd2ac0f3bedfb91fb93bafe2ae3d9c700c8401c80642b5343f8a7943':'followup/snapshots/stability-d64-bac930becd2a.py',
  '43f02c8f516c6bf334b6f455918a522738ebac32b979036781657b6c0293a9ab':'followup/snapshots/stability-seagoing-43f02c8f516c.txt',
 },
 'src/stowage/checker/stability_loading.py':{
  'acab0ef99fabf2ddc0b5f970edfa6c49f5db1f27e02700dcdaf9b5b164a2166f':'followup2/source-snapshot/src/stowage/checker/stability_loading.py',
  'c449d9d3df5ecf5e18f0bf899e6eba1f6677570cdfffc5fdb8d3acae26bc2533':'followup/snapshots/stability-loading-c449d9d3df5e.txt',
 },
}


class _SeagoingAST(ast.NodeTransformer):
 """Specialize the audited D64 module to explicit in_port=False.

 This is used ONLY after every full source byte/snapshot/audit pin passes.
 It never authorizes a new source epoch by selected helper similarity.
 """
 def generic_visit(self,node):
  node=super().generic_visit(node)
  if (isinstance(node,(ast.Module,ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))
      and node.body and isinstance(node.body[0],ast.Expr)
      and isinstance(node.body[0].value,ast.Constant)
      and isinstance(node.body[0].value.value,str)):
   node.body=node.body[1:]
  return node

 def visit_Name(self,node):
  if node.id=='in_port' and isinstance(node.ctx,ast.Load):
   return ast.copy_location(ast.Constant(False),node)
  return node

 def visit_UnaryOp(self,node):
  node=self.generic_visit(node)
  if isinstance(node.op,ast.Not) and isinstance(node.operand,ast.Constant):
   return ast.Constant(not node.operand.value)
  return node

 def visit_BoolOp(self,node):
  node=self.generic_visit(node)
  is_and=isinstance(node.op,ast.And)
  decisive=False if is_and else True
  if any(isinstance(v,ast.Constant) and v.value is decisive for v in node.values):
   return ast.Constant(decisive)
  identity=True if is_and else False
  node.values=[v for v in node.values if not (isinstance(v,ast.Constant) and v.value is identity)]
  if len(node.values)==1:return node.values[0]
  if not node.values:return ast.Constant(identity)
  return node

 def visit_If(self,node):
  node=self.generic_visit(node)
  if isinstance(node.test,ast.Constant):return node.body if node.test.value else node.orelse
  return node


def _seagoing_module_ast(payload):
 tree=ast.parse(payload.decode('utf-8'))
 port_only={'_in_port_criteria','_in_port_immersion_angles'}
 tree.body=[n for n in tree.body if not (isinstance(n,ast.FunctionDef) and n.name in port_only)]
 tree=_SeagoingAST().visit(tree)
 # A removed port definition must have no remaining executable call/reference.
 assert not any(isinstance(n,ast.Name) and n.id in port_only for n in ast.walk(tree))
 return ast.dump(tree,include_attributes=False)


def _verify_audited_source(name,artifact_digest):
 """Reject unknown live/artifact epochs; verify entire retained audit chain."""
 snapshots=_AUDITED_SOURCE_SNAPSHOTS[name]
 assert artifact_digest in snapshots, f'unaudited artifact source epoch: {name} {artifact_digest}'
 live=(ROOT/name).read_bytes()
 live_digest=hashlib.sha256(live).hexdigest()
 assert live_digest in snapshots, f'unaudited live source epoch: {name} {live_digest}'
 audit_bytes=(HERE/_EPOCH_AUDIT_PATH).read_bytes()
 assert hashlib.sha256(audit_bytes).hexdigest()==_EPOCH_AUDIT_SHA256, 'epoch audit pin'
 audit=json.loads(audit_bytes)
 retained={}
 for path,digest in audit['files'].items():
  payload=(HERE/path).read_bytes()
  assert hashlib.sha256(payload).hexdigest()==digest, f'epoch audit dependency: {path}'
  retained[path]=payload
 for module,epochs in _AUDITED_SOURCE_SNAPSHOTS.items():
  assert set(epochs)==set(audit['accepted_stability_epochs' if module.endswith('/stability.py') else 'accepted_loading_epochs'])
  for digest,path in epochs.items():
   assert hashlib.sha256(retained[path]).hexdigest()==digest, path
 assert live==retained[snapshots[live_digest]], 'live source differs from audited complete snapshot'

 # Verify the entire original D64 patch, not only five helper functions.
 original=retained['followup/snapshots/stability-reconstructed-pre-d64.py']
 d64=retained['followup/snapshots/stability-d64-bac930becd2a.py']
 latest=retained['followup/snapshots/stability-seagoing-43f02c8f516c.txt']
 for before,after,path,left,right in [
   (original,d64,'followup/snapshots/pre-d64-to-bac930.patch','old-93efb614','current-bac930bec'),
   (d64,latest,'followup/snapshots/bac930-to-43f02.patch','audited-bac930bec','audited-43f02c8f')]:
  patch=''.join(difflib.unified_diff(before.decode().splitlines(True),after.decode().splitlines(True),
                                   fromfile=left,tofile=right)).encode()
  assert patch==retained[path], f'complete audited patch mismatch: {path}'
 assert _seagoing_module_ast(d64)==_seagoing_module_ast(latest), 'audited seagoing module differs'

 # The separately audited loading change is strictly comments, not an AST
 # allowance for any future loading/liquid-transfer implementation change.
 loading_old=retained['followup2/source-snapshot/src/stowage/checker/stability_loading.py']
 loading_new=retained['followup/snapshots/stability-loading-c449d9d3df5e.txt']
 assert ast.dump(ast.parse(loading_old),include_attributes=False)==ast.dump(ast.parse(loading_new),include_attributes=False)
 identity=json.loads(retained['followup/snapshots/stability-drift-identity.json'])
 assert identity['reconstructed_sha256']==hashlib.sha256(original).hexdigest()
 assert identity['current_snapshot_sha256']==hashlib.sha256(d64).hexdigest()
 replay=json.loads(retained['followup/post-d64-baseline-revalidation.json'])
 assert replay['passed'] and replay['errors'] and all(value==0. for value in replay['errors'].values())


def verify_epoch(d,*,manual=False):
 global checks
 assert d['source_hashes_before']==d['source_hashes_after']
 for name,digest in d['source_hashes_after'].items():
  path=ROOT/name
  if manual and name.endswith('/feeder_envelope.py') and sha(path)!=digest:
   # Original manual calculations precede CLI arrival/provenance-only fixes.
   # The complete calculation-time driver is archived. The numeric functions
   # are AST-compared below; production drift uses exact audited epochs.
   assert sha(archive)==digest
  elif name in _AUDITED_SOURCE_SNAPSHOTS:
   _verify_audited_source(name,digest)
  else:assert sha(path)==digest,name
  checks+=1

def verify_historical_artifact(d):
 global checks
 digest=verify_historical_identity(ROOT,d)
 record=d.get('representative_condition',d)
 source=record.get('source_path',d.get('source_path',HISTORICAL_INSTANCE))
 assert source==HISTORICAL_INSTANCE,'Unexpected historical fixture identity'
 identity=dict(source_path=source,source_instance_sha256=digest)
 for field in ('source_hashes_before','source_hashes_after'):
  require_fixture_hash(d[field],identity)
  checks+=1
 return digest


baselines={}
for name in ['feeder_500','feeder_800','feeder_1700']:
 d=json.loads((OUT/f'{name}-C1.json').read_text());verify_epoch(d,manual=True)
 base=d['rows'][0]
 baselines[name]=base
 assert base['b_total_area_ratio']>1
 assert 0<base['deck_edge_deg']<base['flooding_deg']<50
 for row in d['rows']:
  verify_curve_areas(row)
  assert len(row['sealed_curve'])==11
  assert all(x['within_downflooding']==(x['heel_deg']<=row['flooding_deg']+1e-8) for x in row['sealed_curve'])
  assert row['total_area_end_deg']<=row['flooding_deg']+1e-7
  assert abs(row['b_total_area_ratio']-row['metrics']['area_0_flood_mrad']/row['required_b_areas_mrad']['area_0_flood_mrad'])<1e-12
verify_feeder_table(OUT/'feeder-areas.csv',baselines)

old=ast.parse(archive.read_text());new=ast.parse((HERE/'feeder_envelope.py').read_text())
for name in ['manual_instance','geometry_variant','evaluate']:
 a=next(n for n in old.body if isinstance(n,ast.FunctionDef) and n.name==name)
 b=next(n for n in new.body if isinstance(n,ast.FunctionDef) and n.name==name)
 assert ast.dump(a,include_attributes=False)==ast.dump(b,include_attributes=False)
for name in ['extend19-results.json','iges-upper19-results.json']:
 d=json.loads((HERE/name).read_text());verify_epoch(d)
 if name.startswith('iges'):
  assert verify_iges_reference(d,HERE/'keramati-fig8-samples.csv')=={'kn':7,'gz':7}
  assert max(abs(r['error_gz_m']) for r in d['rows'])<.013
h=json.loads((OUT/'feeder_500-historical.json').read_text());verify_epoch(h)
verify_historical_artifact(h)
for row in h['rows']:verify_curve_areas(row)
a=next(r for r in h['rows'] if r['phase']=='arrival' and r['variant']=='baseline')
b=next(r for r in h['rows'] if r['phase']=='arrival' and r['variant']=='sealed_cover_0p6')
assert .90<a['b_total_area_ratio']<.91
assert .98<b['b_total_area_ratio']<1
combined=json.loads((OUT/'feeder_500-historical-combined.json').read_text());verify_epoch(combined)
verify_historical_artifact(combined)
for row in combined['rows']:verify_curve_areas(row)
assert combined['own_script_sha256']==sha(HERE/'historical_combined.py')
assert .99<next(r for r in combined['rows'] if r['phase']=='arrival')['b_total_area_ratio']<1
conv=json.loads((OUT/'historical-arrival-area-convergence.json').read_text());verify_epoch(conv)
verify_historical_artifact(conv)
assert conv['script_sha256']==sha(HERE/'refine_historical_area.py')
assert all(r['ratio']<1 for r in conv['rows'])
assert abs(conv['rows'][-1]['area_mrad']-conv['rows'][0]['area_mrad'])<.0012
raw=ROOT/'data/raw/kcs-envelope-practice-1028'
for r in json.loads((raw/'MANIFEST.json').read_text())['files']:
 assert sha(raw/r['file'])==r['sha256'];checks+=1
print(f'PASS: {checks} source/archive/input checks; 14/14 KN/GZ bars derived from published ordinates; all partial/total areas integrated from curves and matched to exported tables; near-boundary arrival remains below1 after angular refinement.')
