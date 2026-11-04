from pathlib import Path
import json
import sys
from stowage.io import load_instance, load_plan
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.mask import MaskConfig
from llm.securing import securing_decoder_factory, securing_decode_metrics
root = Path(sys.argv[1])
assert json.loads((root / 'result.json').read_text())['passed'] is True
instance, plan = load_instance(root / 'instance.json'), load_plan(root / 'plan.json')
space = get_action_space()
engine = make_feasibility_engine(instance, space,
                                config=FeasibilityConfig(extra={'global_screen': 0}))
codec = ActionCodec(load_tokenizer(), [box.id for box in instance.containers])
decoder = securing_decoder_factory()(instance, engine, codec, MaskConfig(), 1)[0]
assert decoder.engine.screen.bounds_available is True
for action in space.from_plan(instance, plan):
    for token in codec.encode_action(action):
        decoder.advance(token)
for token in codec.end_plan_tokens:
    decoder.advance(token)
counters = securing_decode_metrics([decoder])
def count_is(key, predicate):
    value = counters.get(key)
    return isinstance(value, int) and not isinstance(value, bool) and predicate(value)
gates = {'decoder_complete': decoder.status == 'complete',
         'metrics_complete': counters.get('securing_metrics_complete') is True,
         'securing_active': counters.get('securing_active') is True,
         'one_screen': count_is('securing_screens', lambda value: value == 1),
         'calls_positive': count_is('securing_calls', lambda value: value > 0),
         'no_unavailable': count_is('unavailable', lambda value: value == 0),
         'no_fail_open': count_is('fail_open', lambda value: value == 0)}
evidence = {'passed': all(gates.values()), 'gates': gates, 'counters': counters,
            'diagnostics': decoder.diagnostics()}
(root / 'securing-mask.json').write_text(json.dumps(evidence, indent=2) + '\n')
assert evidence['passed'], evidence
print(json.dumps(evidence))
