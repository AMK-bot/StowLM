"""Strict extraction language. Context contains public ship/voyage declarations only.

This schema checks structure, not whether a model faithfully read the text. Every
accepted proposal must also pass task 1.3's deterministic intake validator.
"""
from __future__ import annotations

import json
import math
import re

ROW_KEYS = ('id', 'iso_type', 'length_ft', 'gross_mass_kg', 'pod', 'reefer', 'reefer_setpoint_c', 'dg')
REFUSAL_CODES = ('MISSING_FIELD', 'AMBIGUOUS', 'CONFLICT', 'OUT_OF_CONTEXT', 'UNSUPPORTED')
ISO_TYPES = ('22G1', '42G1', '45G1', 'L5G1', '22R1', '42R1', '45R1')
DG_CLASSES = ('1.1', '1.2', '1.3', '1.4', '1.4S', '1.5', '1.6', '2.1', '2.2', '2.3', '3', '4.1', '4.2', '4.3', '5.1', '5.2', '6.1', '6.2', '7', '8', '9')


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def normalize_context(context):
    """Never infer vocabularies from text, targets, or existing container rows."""
    if not isinstance(context, dict):
        raise ValueError('context must be a mapping')
    result = {}
    for key, pattern in (('pods', r'[A-Z]{2}[A-Z0-9]{3}'), ('iso_types', r'[24L][0-9A-Z]{3}'), ('dg_classes', r'(?:[1-9](?:\.[1-9])?[A-Z]?)')):
        values = context.get(key)
        if not isinstance(values, (tuple, list)) or any(not isinstance(v, str) or not re.fullmatch(pattern, v) for v in values):
            raise ValueError(f'invalid public context {key}')
        result[key] = sorted(set(values))
    if any(c not in DG_CLASSES for c in result['dg_classes']):
        raise ValueError('unsupported DG class')
    return result


def context_from_instance(instance):
    """Read rotation and ship declarations only; never inspect cargo/targets.

    ISO registry and DG class registry are fixed public language vocabularies,
    not assertions of ship permission. Task 1.3 checks the ship's permissions.
    Explicit ship declarations narrow those vocabularies, including empty lists.
    """
    if hasattr(instance, 'model_dump'):
        instance = instance.model_dump(mode='json')
    if not isinstance(instance, dict):
        raise ValueError('base_instance must be an instance mapping')
    calls = instance.get('rotation', {}).get('calls', [])
    current = instance.get('call_index', -1)
    pods = [call['locode'] for i, call in enumerate(calls) if i > current]
    ship = instance.get('vessel', instance.get('ship', {}))
    return normalize_context({'pods': pods, 'iso_types': ship.get('allowed_iso_types', list(ISO_TYPES)),
                              'dg_classes': ship.get('allowed_imdg_classes', list(DG_CLASSES))})


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def context_json_schema(context):
    context = normalize_context(context)
    dg = _object({'un_number': {'type': 'string', 'pattern': '^[0-9]{4}$'}, 'imdg_class': {'enum': context['dg_classes']}})
    row = _object({'id': {'type': 'string', 'pattern': '^[A-Z]{3}[UJZ][0-9]{7}$'},
                   'iso_type': {'enum': context['iso_types']}, 'length_ft': {'enum': [20, 40, 45]},
                   'gross_mass_kg': {'type': 'number', 'minimum': 1, 'maximum': 99999, 'multipleOf': 0.001},
                   'pod': {'enum': context['pods']}, 'reefer': {'enum': [False, True]},
                   'reefer_setpoint_c': {'anyOf': [{'const': None}, {'type': 'number', 'minimum': -60, 'maximum': 40, 'multipleOf': 0.001}]},
                   'dg': {'anyOf': [{'const': None}, dg]}})
    refusal = _object({'refusal': _object({'code': {'enum': list(REFUSAL_CODES)}})})
    variants = []
    for iso in context['iso_types']:
        for reefer in (False, True):
            properties = dict(row['properties'])
            properties['iso_type'] = {'const': iso}
            properties['length_ft'] = {'const': {'2': 20, '4': 40, 'L': 45}[iso[0]]}
            properties['reefer'] = {'const': reefer}
            properties['reefer_setpoint_c'] = ({'type': 'number', 'minimum': -60, 'maximum': 40, 'multipleOf': 0.001}
                                                if reefer else {'const': None})
            variants.append(_object(properties))
    return {'anyOf': [*variants, refusal]}


def prompt_messages(text, context):
    context = normalize_context(context)
    return [{'role': 'system', 'content': 'Read one synthetic booking line, email sentence, or dump row. Return exactly one compact JSON row with keys in this order: id,iso_type,length_ft,gross_mass_kg,pod,reefer,reefer_setpoint_c,dg. dg is null or {"un_number":"four digits","imdg_class":"class"}. Every field must be explicitly supported by the fragment; explicit dry/non-DG means reefer=false,setpoint=null,dg=null. Convert explicit units, never infer missing facts or DG classes from a UN number. If unsure return {"refusal":{"code":"CODE"}}. Codes: MISSING_FIELD (absent fact), AMBIGUOUS (unclear wording/unit), CONFLICT (contradictory facts), OUT_OF_CONTEXT (outside public vocabulary), UNSUPPORTED (unsupported request). Thai and English are supported. No prose. Public ship/voyage vocabulary: ' + compact_json(context)},
            {'role': 'user', 'content': text}]


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('duplicate JSON key')
        obj[key] = value
    return obj


def _number(value, minimum, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError('number outside extraction range')
    if abs(value * 1000 - round(value * 1000)) > 1e-6:
        raise ValueError('at most three fractional digits are supported')


def validate_answer(answer, context):
    context = normalize_context(context)
    if isinstance(answer, str):
        raw = answer
        answer = json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    if not isinstance(answer, dict):
        raise ValueError('answer must be one object')
    if set(answer) == {'refusal'}:
        refusal = answer['refusal']
        if not isinstance(refusal, dict) or set(refusal) != {'code'} or refusal['code'] not in REFUSAL_CODES:
            raise ValueError('invalid refusal')
        return {'refusal': {'code': refusal['code']}}
    if set(answer) != set(ROW_KEYS):
        raise ValueError('row fields must be explicit and exact')
    if not isinstance(answer['id'], str) or not re.fullmatch(r'[A-Z]{3}[UJZ][0-9]{7}', answer['id']):
        raise ValueError('invalid container id')
    if answer['iso_type'] not in context['iso_types'] or answer['pod'] not in context['pods']:
        raise ValueError('outside public context')
    length = answer['length_ft']
    if (type(length) not in (int, float)
            or length != {'2': 20, '4': 40, 'L': 45}[answer['iso_type'][0]]
            or not math.isfinite(length)):
        raise ValueError('ISO type and length conflict')
    _number(answer['gross_mass_kg'], 1, 99999)
    if type(answer['reefer']) is not bool:
        raise ValueError('reefer must be boolean')
    if answer['reefer']:
        _number(answer['reefer_setpoint_c'], -60, 40)
    elif answer['reefer_setpoint_c'] is not None:
        raise ValueError('dry row must have null setpoint')
    dg = answer['dg']
    if dg is not None:
        if not isinstance(dg, dict) or set(dg) != {'un_number', 'imdg_class'} or not isinstance(dg['un_number'], str) or not re.fullmatch(r'[0-9]{4}', dg['un_number']) or dg['imdg_class'] not in context['dg_classes']:
            raise ValueError('invalid DG fields')
        dg = {'un_number': dg['un_number'], 'imdg_class': dg['imdg_class']}
    return {key: dg if key == 'dg' else int(length) if key == 'length_ft' else answer[key]
            for key in ROW_KEYS}
