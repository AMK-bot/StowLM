"""Read model identity and pre-mask decode evidence, never a safety verdict.

The run-folder contract is experiments/fleet/orders/w2-stowlm-run-contract.md.
A validated declaration plus an instance-content witness permits attribution;
it does not authenticate a model producer or replace the app's full recheck.
"""
from __future__ import annotations
from datetime import datetime
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

from app.identity import instance_binding_matches, record_instance_digest

UNVERIFIED = 'Imported (unverified source)'
BASE = 'Base Muse Glimmer 30B + feasibility mask'
ADAPTER = 'StowLM (Muse Glimmer 30B + LoRA, MLX)'
DIAGNOSTIC = 'StowLM (diagnostic adapter, not evidence)'
CONFLICT = 'Model run evidence conflict (not attributed to a model)'
SHA = re.compile(r'[0-9a-f]{64}')
# Clean adapter training-data kinds. ``mvp_gold`` (plan v4 / D79) is admitted by llm.stowlm.train.admit_dataset on
# the same footing as ``production_gold`` (per-row full-checker pass + D79 mask replay binding). Without it every
# plan-v4 StowLM adapter, including the final showcase runs, was tainted as "diagnostic" and failed the gate.
CLEAN_DATA_MODES = ('production_gold', 'mvp_gold')
# ``PlannerResult.planner`` each manifest kind's producer writes (llm.stowlm.planner: name = "stowlm" with an adapter,
# "stowlm.base" without; solver repair keeps the original planner id and sets ``repaired``).
PRODUCER_IDS = {'stowlm_adapter': 'stowlm', 'base_glimmer_mask': 'stowlm.base'}
SOLVER_METHOD = re.compile(r'\b(solver|two-?step|baseline|cp-?sat|lns)\b', re.I)


def _allow_test_fixture_verification():
    """Tests may monkeypatch this; no environment, CLI or imported-data override."""
    return False


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    return type(value) in {int, float} and math.isfinite(value)


def _sha(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def _mvp_loose_run(value):
    """Plan-v4 production run mode (D79 'mvp-loose'): eval-c1 ``run_c1.py loose`` always writes ``diagnostic: true``
    meaning "not the frozen official harness", which says nothing about the adapter. For such runs the adapter's own
    fields (clean flag, stubs, data mode, weight-bound lineage) decide; any adapter taint still wins."""
    return str(value.get('mode', '')).startswith('mvp-loose')


def _diagnostic_adapter(value):
    """A clean self-claim cannot erase explicit diagnostic or training taint."""
    return ((value.get('diagnostic') is True and not _mvp_loose_run(value)) or value.get('adapter_clean') is False
        or bool(value.get('adapter_stubs'))
        or value.get('adapter_data_mode') in ('pipeline_test', 'diagnostic')
        or str(value.get('mode', '')).startswith('diagnostic'))


def validate_manifest(raw):
    if not isinstance(raw, dict):
        raise ValueError('Model run manifest must be an object')
    value = dict(raw)
    versions = value.get('versions', {})
    if not isinstance(versions, dict):
        raise ValueError('Model run manifest versions must be an object')
    for name, aliases in {'mlx_version': ('mlx',), 'mlx_lm_version': ('mlx_lm', 'mlx-lm')}.items():
        if name not in value:
            value[name] = next((versions[k] for k in aliases if k in versions), None)
    required_text = ('base_model_revision', 'quantisation', 'mlx_version', 'mlx_lm_version',
        'mask_version', 'feasibility_version', 'checker_version', 'prompt_version', 'format_version',
        'created_at', 'command')
    if any(not _text(value.get(k)) for k in required_text):
        raise ValueError('Model run manifest missing required text or version')
    if value.get('planner_kind') not in {'base_glimmer_mask', 'stowlm_adapter'}:
        raise ValueError('Model run manifest planner_kind is unsupported')
    if value.get('base_model') != 'meta-models/Muse-Glimmer-30B' or value.get('runtime') != 'mlx':
        raise ValueError('Model run manifest requires Muse Glimmer 30B and MLX')
    # Plan-v4 'mvp-loose' runs (D79) have no frozen-harness epoch by design; null is allowed there too.
    if not _text(value.get('epoch_id')) and not (value.get('epoch_id') is None and
                                                 (_diagnostic_adapter(value) or _mvp_loose_run(value))):
        raise ValueError('Model run manifest epoch_id is missing')
    if 'adapter_clean' in value and value['adapter_clean'] is not None and type(value['adapter_clean']) is not bool:
        raise ValueError('Model run manifest adapter_clean must be boolean or null')
    if 'adapter_data_mode' in value and value['adapter_data_mode'] is not None and not _text(value['adapter_data_mode']):
        raise ValueError('Model run manifest adapter_data_mode must be text or null')
    if 'adapter_stubs' in value and value['adapter_stubs'] is not None and not isinstance(value['adapter_stubs'], dict):
        raise ValueError('Model run manifest adapter_stubs must be an object or null')
    if 'method' in value and not _text(value['method']):
        raise ValueError('Model run manifest method must be text')
    if 'selection' in value and value['selection'] is not None and not isinstance(value['selection'], (dict, str)):
        raise ValueError('Model run manifest selection must be an object, text or null')
    if not _sha(value.get('instance_root_sha256')):
        raise ValueError('Model run manifest instance root SHA-256 is malformed')
    if 'fixture' in value and type(value['fixture']) is not bool:
        raise ValueError('Model run manifest fixture must be boolean')
    try:
        datetime.fromisoformat(value['created_at'].replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('Model run manifest created_at is not ISO datetime') from error
    if not {'adapter_sha256', 'adapter_lineage'}.issubset(value):
        raise ValueError('Model run manifest requires nullable adapter fields')
    if value['planner_kind'] == 'base_glimmer_mask':
        if value['adapter_sha256'] is not None or value['adapter_lineage'] is not None:
            raise ValueError('Base model run manifest must have null adapter fields')
    else:
        lineage = value['adapter_lineage']
        if not _sha(value['adapter_sha256']) or not isinstance(lineage, dict) or not _text(lineage.get('path')) or not _sha(lineage.get('sha256')):
            raise ValueError('Model run manifest adapter or lineage SHA-256 is malformed')
    decoding = value.get('decoding')
    if not isinstance(decoding, dict):
        raise ValueError('Model run manifest decoding must be an object')
    decoding = dict(decoding)
    if 'c2' not in decoding and 'c2_enabled' in decoding:
        decoding['c2'] = decoding['c2_enabled']
    if decoding.get('c2') == 'on':
        decoding['c2_declaration'] = 'on'
        decoding['c2'] = True
    if isinstance(decoding.get('c2'), str) and decoding['c2'].startswith('off (D79:'):
        # MVP v4 run folders (e.g. runs/base-mask) declare D79's real engine
        # config as text. Keep the exact declaration; C2 was off.
        decoding['c2_declaration'] = decoding['c2']
        decoding['c2'] = False
    if _diagnostic_adapter(value) and decoding.get('c2') == 'off (stub; diagnostic)':
        # The original pipeline-test producer predates the typed C2 flag. Keep
        # its exact declaration visible and never promote this diagnostic run.
        decoding['c2_declaration'] = decoding['c2']
        decoding['c2'] = False
    if not _number(decoding.get('temperature')) or decoding['temperature'] < 0 or not _number(decoding.get('top_p')) or not 0 < decoding['top_p'] <= 1:
        raise ValueError('Model run manifest decoding sampling is invalid')
    if (type(decoding.get('samples_per_instance')) is not int or decoding['samples_per_instance'] < 1
        or type(decoding.get('pool_size')) is not int or decoding['pool_size'] < 0
        or type(decoding.get('seed')) is not int or decoding['seed'] < 0 or type(decoding.get('c2')) is not bool):
        raise ValueError('Model run manifest decoding counts, seed or C2 is invalid')
    value['decoding'] = decoding
    return value


def read_trace(path):
    if not path.is_file():
        raise ValueError('Model run trace.jsonl is missing')
    rows = []
    try:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or type(row.get('step')) is not int or row['step'] < 0 or (rows and row['step'] != rows[-1]['step'] + 1):
                raise ValueError('Model run trace steps must be ordered consecutive integers')
            # A root dead end is evidence even when decoding produced no token.
            # Contract Added 28 Oct: it has no chosen action, top-k or timings.
            if row.get('event') != 'dead_end':
                chosen = row.get('chosen')
                if not isinstance(chosen, dict) or not _text(chosen.get('text')):
                    raise ValueError('Model run trace chosen action invalid')
                action = chosen.get('action_id')
                terminal = action is None and (chosen.get('end_of_plan') is True or chosen.get('partial') is True or
                    chosen['text'].startswith(('<end of plan>', '<partial action:')))
                structured = isinstance(action, dict) and _text(action.get('op'))
                if not (terminal or structured or type(action) is int or _text(action)):
                    raise ValueError('Model run trace chosen action invalid')
            else:
                dead = row.get('dead_end')
                if dead is not None and not isinstance(dead, dict):
                    raise ValueError('Model run trace dead-end evidence invalid')
                rules = (dead or {}).get('rule_ids', row.get('rule_ids'))
                if rules is not None and (not isinstance(rules, list) or any(not _text(x) for x in rules)):
                    raise ValueError('Model run trace dead-end rule IDs invalid')
                if row.get('depth') is not None and not _count(row['depth']):
                    raise ValueError('Model run trace dead-end depth invalid')
                if row.get('dead_ends_by_rule') is not None and not _count_map(row['dead_ends_by_rule']):
                    raise ValueError('Model run trace dead-end counts invalid')
            if row.get('chosen_logprob') == -math.inf:
                # Backtracking decode (src/llm/stowlm/backtrack.py) can record a masked log p of -inf for an action
                # chosen at a revisited state. Kept as evidence, never as a number: browsers cannot parse -Infinity.
                row['chosen_logprob'] = None
                row['chosen_logprob_recorded'] = '-inf'
            legal, possible = row.get('n_legal'), row.get('n_actions')
            # Exact counts belong to selected-sample replay only. Decode-loop
            # counts may be absent; a missing value is never estimated.
            if (any(x is not None and not _count(x) for x in (legal, possible)) or
                (legal is not None and possible is not None and legal > possible)):
                raise ValueError('Model run trace action counts invalid')
            if ((row.get('chosen_logprob') is not None and not _number(row['chosen_logprob'])) or
                any(row.get(k) is not None and (not _number(row[k]) or row[k] < 0) for k in ('mask_ms', 'decode_ms'))):
                raise ValueError('Model run trace timing or logprob invalid')
            # top_k is over vocabulary tokens, not the physical action pairs.
            # EOS tokens may decode to an empty string; retain their evidence.
            if row.get('top_k') is not None and not _top_k(row['top_k']):
                raise ValueError('Model run trace pre-mask top five invalid')
            decisions = row.get('decisions')
            if decisions is not None:
                if not isinstance(decisions, list):
                    raise ValueError('Model run trace decisions must be a list')
                previous = -1
                for decision in decisions:
                    if (not isinstance(decision, dict) or not _count(decision.get('position')) or
                        decision['position'] <= previous or decision.get('field') not in DECISION_FIELDS or
                        any(decision.get(k) is not None and not _count(decision[k]) for k in ('n_legal', 'n_allowed')) or
                        (decision.get('overridden') is not None and type(decision['overridden']) is not bool) or
                        (decision.get('top_k') is not None and not _top_k(decision['top_k']))):
                        raise ValueError('Model run trace decision evidence invalid')
                    previous = decision['position']
            if row.get('format_override') is not None and type(row['format_override']) is not bool:
                raise ValueError('Model run trace format_override must be boolean or null')
            if row.get('screens') is not None and not isinstance(row['screens'], dict):
                raise ValueError('Model run trace screens must be an object or null')
            rows.append(row)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f'Model run trace cannot be read: {error}') from error
    return rows


DECISION_FIELDS = ('box', 'bay', 'row', 'tier')


def _count(value):
    return type(value) is int and value >= 0


def _count_map(value):
    return isinstance(value, dict) and all(_text(k) and _count(v) for k, v in value.items())


def _top_k(value):
    return (isinstance(value, list) and 1 <= len(value) <= 5 and all(
        isinstance(x, dict) and isinstance(x.get('text'), str) and _number(x.get('logprob'))
        and type(x.get('legal')) is bool for x in value))


def _rate(value):
    return _number(value) and 0 <= value <= 1


def mask_override_summary(manifest, record, trace):
    """Use only explicitly aggregated sampling-pass evidence, never trace estimates.

    C1's selected trace is a replay of one sample. Its top-five tokens cannot
    establish the mask override rate of the entire sampling batch.
    """
    result = record.get('result') or record.get('planner_result') or record
    metrics = result.get('metrics', {}) if isinstance(result, dict) else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    candidates = [('manifest.mask_override', manifest.get('mask_override', {})), ('manifest', manifest)]
    candidates += [('result.metrics.repair_original_metrics', metrics.get('repair_original_metrics', {})),
                   ('result.metrics', metrics)]
    candidates += [(f'trace[{i}].mask_override', row.get('mask_override', row)) for i, row in enumerate(trace)]
    samples = manifest.get('decoding', {}).get('samples_per_instance')
    fallback = None
    for source, value in candidates:
        if not isinstance(value, dict) or 'all samples' not in str(value.get('mask_override_scope', '')).lower():
            continue
        rates = [value.get('mask_override_rate_decision'), value.get('mask_override_rate_all_tokens'), value.get('format_override_rate')]
        positions = [value.get('mask_override_decision_positions'), value.get('mask_override_token_positions'), value.get('format_override_positions')]
        if value.get('override_error') or any(rate is not None and (not _number(rate) or not 0 <= rate <= 1) for rate in rates):
            continue
        if any(count is not None and (type(count) is not int or count < 0) for count in positions):
            continue
        if not any(rate is not None for rate in rates):
            continue
        scope = str(value['mask_override_scope']).lower()
        field_rates, field_positions = value.get('mask_override_rate_by_field'), value.get('mask_override_positions_by_field')
        fields_verified = (isinstance(field_rates, dict) and isinstance(field_positions, dict) and
            set(field_rates) == set(field_positions) == set(DECISION_FIELDS) and
            all(_count(field_positions[f]) and (field_rates[f] is None or _rate(field_rates[f])) for f in DECISION_FIELDS) and
            positions[0] is not None and sum(field_positions.values()) == positions[0])
        # Before the 29 Oct amendment "decision" was the opcode. Never relabel
        # that historical statistic as a box/slot planning decision.
        decision_scope = fields_verified or 'box/bay/row/tier' in scope
        summary = dict(available=bool(decision_scope and rates[0] is not None),
            decision_rate=rates[0] if decision_scope else None, token_rate=rates[1],
            decision_scope='box/bay/row/tier' if decision_scope else None,
            decision_positions=positions[0], token_positions=positions[1], samples=samples,
            format_rate=rates[2], format_positions=positions[2],
            rate_by_field=field_rates if fields_verified else None,
            positions_by_field=field_positions if fields_verified else None,
            scope=value['mask_override_scope'], source=source,
            reason=None if decision_scope else 'Box/slot positions not recorded; legacy decision statistic may measure opcode tokens')
        if summary['available']:
            return summary
        # A legacy/opcode or format-only declaration may coexist with current
        # per-decision evidence in the result. Keep searching before falling
        # back to historical token/format statistics.
        if fallback is None:
            fallback = summary
    if fallback is not None:
        return fallback
    return dict(available=False, samples=samples, scope='all samples',
        reason='No recorded all-samples mask override statistic; selected-sample replay is insufficient')


def _producer_conflicts(manifest, result):
    """Declarations that contradict the manifest's producer. Consistency only, never producer authentication."""
    expected = PRODUCER_IDS[manifest['planner_kind']]
    planner = result.get('planner')
    conflicts = []
    # ``stowlm+<fallback>`` is an explicit hybrid; only a repaired result may carry it.
    if planner != expected and not (result.get('repaired') is True and isinstance(planner, str)
                                    and planner.split('+')[0] == expected):
        conflicts.append(f'Result planner {planner!r} contradicts manifest producer {expected!r}')
    if SOLVER_METHOD.search(str(manifest.get('method') or '')):
        conflicts.append(f"Manifest method {manifest['method']!r} contradicts planner_kind {manifest['planner_kind']!r}")
    return conflicts


def _trace_binding(trace, result):
    """Compare the selected trace's placements with the displayed plan, where the trace supports it.

    A Place(box, slot) action expands to lifts: its own load (or restow_on / restow_shift) plus blocker, yard and hatch
    lifts. So every load of the plan must be a decoded action in decoded order (any order after the D75 resequencer
    moved lifts), and each decoded box's last slot must be its last slot in the plan. A repaired plan keeps the draft's
    trace by contract; a failed run shows no plan.
    """
    plan = result.get('plan')
    moves = plan.get('moves') if isinstance(plan, dict) else None
    if result.get('status') != 'ok' or not isinstance(moves, list):
        return {'status': 'not_applicable', 'reason': 'No shipped plan to compare with the trace'}
    if result.get('repaired') is True:
        return {'status': 'repaired', 'reason': 'Solver repair changed the plan; the trace is the StowLM draft'}
    actions = [(row.get('chosen') or {}).get('action_id') for row in trace if row.get('event') != 'dead_end']
    actions = [action for action in actions if action is not None]
    if not actions or any(not isinstance(a, dict) or a.get('op') != 'P' or not _text(a.get('box')) or not _text(a.get('slot'))
                          for a in actions):
        return {'status': 'unsupported', 'reason': 'Trace has no structured Place(box, slot) actions'}
    placed = [(a['box'], a['slot']) for a in actions]
    loads, last = [], {}
    for move in moves:
        if isinstance(move, dict) and move.get('kind') in ('load', 'restow_on', 'restow_shift'):
            last[move.get('container_id')] = move.get('dst')
            if move['kind'] == 'load':
                loads.append((move.get('container_id'), move.get('dst')))
    missing = Counter(loads) - Counter(placed)
    if missing:
        box, slot = next(iter(missing))
        return {'status': 'mismatch', 'reason': f'Plan loads {box} into {slot}, which the trace never chose'}
    resequenced = ((result.get('metrics') or {}).get('resequence') or {}).get('applied') is True
    remaining = iter(placed)
    if not resequenced and not all(load in remaining for load in loads):
        return {'status': 'mismatch', 'reason': 'Plan loads are not in the trace order'}
    final = dict(placed)
    wrong = [box for box, slot in final.items() if last.get(box) != slot]
    if wrong:
        return {'status': 'mismatch', 'reason': f'Trace last placed {wrong[0]} in {final[wrong[0]]}, the plan has {last.get(wrong[0])}'}
    return {'status': 'bound', 'reason': None, 'actions': len(placed), 'loads': len(loads)}


def _optional_object(path):
    """Optional diagnostics are never safety evidence; malformed values stay absent."""
    if not path.is_file():
        return {}, []
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}, [path]


def _run_rates(summary):
    n = summary.get('n')
    fallback = summary.get('fallback_rate')
    repair = summary.get('repair_rate')
    fallback = fallback if _rate(fallback) else None
    if 'repair_rate' not in summary:
        # Producer run_c1.summarize defines fallback_rate as
        # count(result.repaired) / all run instances, including failed repairs.
        repair = fallback
    repair = repair if _rate(repair) else None
    if not _count(n) or n == 0:
        fallback = repair = None
    return {'available': fallback is not None or repair is not None,
        'fallback_rate': fallback, 'repair_rate': repair, 'instances': n if _count(n) else None,
        'scope': 'all run instances', 'source': 'summary.json',
        'definition': 'Producer fallback_rate counts repaired instances / all run instances'}


def _outcome(record, recorded):
    result = record.get('result') or record.get('planner_result') or record
    result = result if isinstance(result, dict) else {}
    metrics = result.get('metrics')
    metrics = metrics if isinstance(metrics, dict) else {}
    original = metrics.get('repair_original_metrics')
    original = original if isinstance(original, dict) else {}
    for source, value in [('result.metrics', metrics), ('result.metrics.repair_original_metrics', original)]:
        if value.get('deadline_exceeded') is True:
            return {'label': 'mask time limit', 'source': source}
    if result.get('status') == 'timeout' and not result.get('repaired'):
        return {'label': 'mask time limit', 'source': 'result.status'}
    known = ('mask time limit', 'own pass', 'repaired pass', 'checker fail', 'dead end', 'error')
    label = recorded.get('outcome')
    # Outcome labels describe the saved draft, never approve the displayed plan.
    # In particular do not propagate outcome.json's stored checker_passed flag.
    return {'label': label if label in known else 'not recorded',
            'source': 'outcome.json' if label in known else None}


def _dead_ends(value):
    value = value if isinstance(value, dict) else {}
    by_rule = value.get('by_rule')
    collected = value.get('collected') is True
    return {'dead_end_samples': value.get('dead_end_samples') if _count(value.get('dead_end_samples')) else None,
        'by_rule': by_rule if _count_map(by_rule) else {},
        'unattributed': value.get('unattributed') if collected and _count(value.get('unattributed')) else None,
        'collected': collected, 'note': 'Rules active at the dead state; one sample can cite several rules'}


def manifest_folder(path, selected_folder=None):
    """Find the nearest run manifest, bounded by an explicit selected run root."""
    path = Path(path).resolve()
    root = Path(selected_folder).resolve() if selected_folder else path.parent
    if not path.is_relative_to(root):
        raise ValueError('Model run result lies outside its run folder')
    for parent in (path.parent, *path.parents):
        if not parent.is_relative_to(root):
            break
        if (parent / 'manifest.json').is_file():
            return parent
        if parent == root:
            break
    return None


def _lineage_constraints(manifest, root):
    """A readable weight-bound sidecar may tighten, never upgrade, declarations."""
    declaration = manifest.get('adapter_lineage')
    if manifest['planner_kind'] != 'stowlm_adapter' or not isinstance(declaration, dict):
        return manifest, []
    hint = Path(declaration['path'])
    candidates = [hint] if hint.is_absolute() else [root / hint, Path(__file__).resolve().parents[1] / hint]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        return manifest, []  # Declaration-only provenance remains explicit.
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != declaration['sha256']:
        raise ValueError('Adapter lineage file SHA256 conflicts with manifest')
    try:
        evidence = json.loads(raw)
        stubs = evidence['provenance']['stubs']
        identity = evidence['training']['data_identity']
        mode = identity.get('claimed_kind') or identity.get('dataset_kind')
        if not isinstance(stubs, dict) or not isinstance(identity, dict):
            raise ValueError('Malformed adapter lineage identity')
        if evidence.get('weights_sha256') != manifest['adapter_sha256']:
            raise ValueError('Adapter lineage weights SHA256 conflicts with manifest')
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('Malformed adapter lineage identity') from error
    effective = dict(manifest)
    effective['adapter_lineage_verified'] = True
    effective['adapter_stubs'] = {**(manifest.get('adapter_stubs') or {}), **stubs}
    if stubs or identity.get('dataset_kind') not in CLEAN_DATA_MODES or mode in ('pipeline_test', 'diagnostic'):
        effective['adapter_clean'] = False
        effective['adapter_data_mode'] = mode
    return effective, [path]


def model_run_metadata(folder, result_path, record, instance):
    """Validate source declarations and retain inspectable pre-mask evidence."""
    root = manifest_folder(result_path, folder)
    planner = (record.get('result') or record.get('planner_result') or record).get('planner', '')
    if root is None:
        return {'label': UNVERIFIED, 'model_run': {'verified': False, 'label': UNVERIFIED,
            'fixture': False, 'trace': [], 'reason': 'No model run manifest'}} if any(x in planner.lower() for x in ('stowlm', 'glimmer')) else {}
    manifest_path = root / 'manifest.json'
    try:
        raw_manifest = json.loads(manifest_path.read_text())
        # Ordinary solver/evaluation runs may also carry manifest.json; that
        # filename alone does not turn them into a claimed Glimmer model run.
        # Reference-solver runs (w2 run contract, e.g. experiments/mvp/runs/test-exact) carry the same keys with
        # planner_kind "classical_reference" and null model fields; that declaration claims no model run.
        classical = (isinstance(raw_manifest, dict) and raw_manifest.get('planner_kind') == 'classical_reference'
                     and not any(raw_manifest.get(key) is not None for key in
                                 ('base_model', 'adapter_sha256', 'mask_version')))
        if (isinstance(raw_manifest, dict) and (classical or not
                {'planner_kind', 'base_model', 'adapter_sha256', 'mask_version'}.intersection(raw_manifest))
                and not any(x in planner.lower() for x in ('stowlm', 'glimmer'))):
            return {}
        manifest = validate_manifest(raw_manifest)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f'Model run manifest cannot be read: {error}') from error
    result_path = Path(result_path).resolve()
    if result_path.name != 'result.json' or result_path.parent.parent.name != 'instances' or result_path.parent.parent.parent != root:
        raise ValueError('Model run result must use instances/<instance_id>/result.json')
    if result_path.parent.name != instance.id:
        raise ValueError('Model run result directory must match its instance_id')
    manifest, lineage_paths = _lineage_constraints(manifest, root)
    trace_path = result_path.with_name('trace.jsonl')
    trace = read_trace(trace_path)
    summary, summary_paths = _optional_object(root / 'summary.json')
    outcome, outcome_paths = _optional_object(result_path.with_name('outcome.json'))
    digest = record_instance_digest(record)
    raw = record.get('instance')
    bound = bool(digest and instance_binding_matches(instance, digest, raw))
    # input_sha256 is accepted only when load_inputs has resolved and checked
    # its original byte witness. This helper never treats it as a model hash.
    if not bound and record.get('input_sha256'):
        bound = True
    fixture = manifest.get('fixture', False)
    verified = bound and (not fixture or _allow_test_fixture_verification())
    result = record.get('result') or record.get('planner_result') or record
    binding = _trace_binding(trace, result)
    conflicts = _producer_conflicts(manifest, result)
    if binding['status'] == 'mismatch':
        conflicts.append(binding['reason'])
    if manifest['planner_kind'] == 'base_glimmer_mask':
        declared_label = BASE
    elif _diagnostic_adapter(manifest):
        declared_label = DIAGNOSTIC
    elif (manifest.get('adapter_clean') is True
          and manifest.get('adapter_data_mode') in CLEAN_DATA_MODES
          and manifest.get('adapter_stubs') == {}):
        declared_label = ADAPTER
    else:
        declared_label = UNVERIFIED
    label = declared_label if verified else UNVERIFIED
    reason = None if verified else ('Synthetic test fixture' if fixture else 'No verified result instance binding')
    if conflicts:
        # Not this model's plan: no model label, and the taint reaches the shipping gate (inputs._retain_adapter_taint).
        verified, label, reason = False, CONFLICT, '; '.join(conflicts)
        manifest = {**manifest, 'adapter_stubs': {**(manifest.get('adapter_stubs') or {}),
                                                  'model_run.producer_conflict': reason}}
    model = {**manifest, 'verified': bool(verified), 'fixture': fixture, 'label': label,
        'mask_override': mask_override_summary(manifest, record, trace),
        'run_rates': _run_rates(summary), 'outcome': _outcome(record, outcome),
        'dead_ends': _dead_ends(manifest.get('dead_ends')),
        'trace': trace, 'instance_bound': bound, 'trace_binding': binding, 'producer_conflicts': conflicts,
        'reason': reason,
        'provenance_scope': 'Manifest declaration and instance binding; not producer authentication'}
    return {'label': label, 'model_run': model, 'source_files': [
        {'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in (manifest_path, trace_path, *lineage_paths, *summary_paths, *outcome_paths)]}
