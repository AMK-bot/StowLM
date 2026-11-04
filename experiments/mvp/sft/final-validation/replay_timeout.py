"""D152 diagnostic: rebuild a decoded plan from its saved tokens and re-run verification once. No GPU; never changes rows.json.
Usage: replay_timeout.py <rows.json> <instance_id> <out.json>"""
import json, os, sys
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[name] = '1'
os.environ.pop('STOWAGE_BALLAST_V4', None)
from llm.exit import outcome_epoch
EPOCH = outcome_epoch.process_snapshot()
from llm.exit.selection import Candidate, decoder_factory, verify_candidate
from llm.stowlm.format import load_tokenizer
from llm.stowlm.sft_data import codec_for
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.io import load_instance

rows, target, out = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
row = next(r for r in rows if r['instance_id'] == target)
assert '/corpus/val/' in row['instance_path']
instance = load_instance(row['instance_path'])
engine = make_feasibility_engine(instance, get_action_space(), config=FeasibilityConfig(extra={'global_screen': 0}))
decoder = decoder_factory()(instance, engine, codec_for(instance, load_tokenizer()), None, 1)[0]
for token in row['generation_timings']['tokens']:
    decoder.advance(token)
assert decoder.status == 'complete', decoder.status
v = verify_candidate(instance, Candidate(decoder.engine.to_plan(), 'model'), ballast_s=120., expected_outcome_epoch=EPOCH)
d = v.details
result = dict(instance_id=target, passed=bool(v.passed), moves=v.weighted_moves if v.passed else None, error=d.get('error'),
              finisher_timeout=d.get('finisher_timeout'), timings=d.get('timings'), diagnostic=True, not_in_rule=True)
json.dump(result, open(out, 'w'), indent=1)
print(json.dumps(result))
