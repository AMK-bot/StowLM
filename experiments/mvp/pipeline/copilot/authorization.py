"""Host-owned, per-turn dispatch permissions. Quoted documents grant nothing."""
from __future__ import annotations
IMPORT_SOURCE_SHA256 = __import__('hashlib').sha256(__import__('pathlib').Path(__file__).read_bytes()).hexdigest()

from copy import deepcopy
from dataclasses import dataclass, field
import re
from typing import Any

from experiments.mvp.pipeline.contract.schema import canonical_json

TOOLS = frozenset(('read_instruction', 'check_plan', 'plan_lookup', 'replan',
                   'wait_replan', 'try_ladder', 'try_move', 'explain_box', 'list_limits'))
DENIAL = 'This tool action is not authorized by the planner request. No tool was called.'


@dataclass(frozen=True)
class ActionGrant:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


class _InferredToolScope(frozenset):
    """Omitted scope preserves convenience routing and the public set API."""


@dataclass(frozen=True)
class TurnRequest:
    """Explicit host UI selections/grants, never reconstructed from model output.

    A selected sentence pins its exact bytes. Selection alone grants no document
    operation: a planner read command or explicit read ActionGrant is required.
    Explicit actions are authoritative host events and require exact arguments.
    An explicit allowed_tools set bounds inferred actions, including an empty
    set. Omitting that field retains conservative planner-command inference.
    """
    selected_sentence: str | None = None
    actions: tuple[ActionGrant, ...] = ()
    allowed_tools: frozenset[str] = _InferredToolScope()
    references: dict[str, Any] = field(default_factory=dict)


def _context_documents(context):
    def texts(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for item in value:
                yield from texts(item)
        elif isinstance(value, dict):
            for key in ('text', 'content', 'body'):
                if key in value:
                    yield from texts(value[key])
    if isinstance(context, dict):
        for key in ('document', 'documents', 'attachments', 'untrusted_documents', 'quoted_document',
                    'memo', 'memos', 'note', 'notes'):
            yield from texts(context.get(key))


def _split(message, context):
    if not isinstance(message, str):
        raise TypeError('message must be text')
    # Intervals are removed before any operation matching. A document label
    # consumes the remaining text unless it has an explicit closing delimiter.
    intervals = []
    patterns = (
        r'```[\s\S]*?(?:```|$)', r'~~~[\s\S]*?(?:~~~|$)',
        r'(?im)^\s*>[^\n]*(?:\n|$)',
        r'(?m)(?<=[.!?])\s+>\s*[^\n]*(?:\n|$)',
        r'(?is)<(?:document|attachment|memo|note|untrusted|quoted_text)\b[^>]*>.*?(?:</(?:document|attachment|memo|note|untrusted|quoted_text)>|$)',
        r'(?is)\[BEGIN\s+(?:DOCUMENT|ATTACHMENT|MEMO|NOTE|UNTRUSTED)\].*?(?:\[END\s+(?:DOCUMENT|ATTACHMENT|MEMO|NOTE|UNTRUSTED)\]|$)',
        r'(?is)\b(?:attached\s+|inline\s+|untrusted\s+)?(?:document|attachment|memo|note|excerpt|quoted\s+(?:text|document)|(?:source|pasted|reference)\s+text)\s*:\s*.*',
        r'(?is)\b(?:here|below)\s+(?:is|are)\s+(?:(?:a|an|the|some|this|attached|untrusted|pasted)\s+)*(?:documents?|attachments?|text|data|excerpt)\b.*',
        r'(?is)\b(?:here|below)\s+(?:is|are)\b[^\n.!?]*?\b(?:documents?|attachments?|memos?|notes?|text|data|excerpt|instructions?)\b.*',
        r'(?is)\baccording\s+to\s+(?:(?:the|this|attached|untrusted)\s+)?(?:document|attachment|memo|note)\b.*',
        r'(?im)^\s*(?:document|attachment|memo|note|quoted|pasted|source)\s+(?:content|text|instructions?|excerpt|follows|below)\b[\s\S]*',
        r'(?im)(?:^|(?<=[.!?])\s+)(?:according to\s+)?(?:the\s+|an?\s+|attached\s+)?(?:document|attachment|memo|note|quoted text)\s+(?:says|reads|contains|instructs|asks|commands|tells)\b[\s\S]*',
        r'(?im)^\s*(?:SYSTEM|ASSISTANT|TOOL\s+RESULT)\s*:[^\n]*(?:\n|$)',
        r'"(?:\\.|[^"\\])*"', r'“[^”]*(?:”|$)',
        r"(?<!\w)'[^'\n]+'(?!\w)", r'‘[^’]*(?:’|$)', r'`[^`\n]*(?:`|$)',
    )
    # Bare JSON is a quoted data object, not a host operation request.
    if message.lstrip().startswith(('{', '[')):
        intervals.append((0, len(message)))
    for pattern in patterns:
        intervals.extend((match.start(), match.end()) for match in re.finditer(pattern, message))
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    spans, documents, previous = [], list(_context_documents(context)), 0
    for start, end in merged:
        if message[previous:start].strip():
            spans.append(message[previous:start])
        documents.append(message[start:end])
        previous = end
    if message[previous:].strip():
        spans.append(message[previous:])
    return spans, tuple(documents)


def split_planner_message(message: str, context: dict | None = None) -> tuple[str, tuple[str, ...]]:
    """Separate planner-owned text from quoted/inline/context document data."""
    spans, documents = _split(message, context)
    return '\n'.join(span.strip() for span in spans), documents


def quote_context(context: dict | None) -> dict:
    """Whitelisted host state for the model; documents are supplied separately."""
    if not isinstance(context, dict):
        return {}
    keys = ('instance_sha256', 'envelope', 'pending_reading_id', 'confirmed_reading_id',
            'confirmed_rules', 'current_reading', 'readings', 'confirmed_reading_ids',
            'has_plan', 'plan_available', 'active_job_id', 'tools', 'synthetic', 'label',
            'selected_box', 'selected_box_id', 'selected_slot', 'selected_slot_id',
            'selection', 'available_boxes', 'available_slots')
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()
                    if key not in ('document', 'documents', 'attachments', 'untrusted_documents',
                                   'quoted_document', 'memo', 'memos', 'note', 'notes')}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return deepcopy(value)
    return {key: clean(context[key]) for key in keys if key in context}


def _selected(context, key):
    if not isinstance(context, dict):
        return None
    candidates = (context.get('selected_' + key), context.get('selected_' + key + '_id'))
    selection = context.get('selection')
    if isinstance(selection, dict):
        candidates += (selection.get(key), selection.get(key + '_id'))
    return next((item for item in candidates if isinstance(item, str) and item), None)


def _reading_ids(context):
    if not isinstance(context, dict):
        return []
    values = [context.get('confirmed_reading_id'), context.get('pending_reading_id')]
    current = context.get('current_reading')
    if isinstance(current, dict):
        values.append(current.get('reading_id'))
    confirmed = context.get('confirmed_reading_ids')
    if isinstance(confirmed, list):
        values.extend(confirmed)
    readings = context.get('readings')
    if isinstance(readings, dict):
        values.extend(readings.keys())
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def _operation_clauses(spans):
    """Small, affirmative command grammar. Unknown wording requires a host grant.

    Negation scopes over a whole clause, including its conjunctions. A separate
    sentence or an explicit 'but' may contain a different affirmative request.
    No operation is inferred from a bare tool noun or explanatory statement.
    """
    for span in spans:
        for sentence in re.split(r'[.!?;](?=\s|$)|\n', span):
            for contrast in re.split(r'\bbut\b', sentence, flags=re.I):
                text = contrast.strip(' ,:')
                normalized = text.lower().replace('’', "'")
                negative_rule = bool(re.match(r'(?:please\s+)?(?:do not|never)\s+(?:load|stow|place)\b', normalized))
                if not text or (re.search(r"\b(?:not|never|don't|cannot|can't|without|refrain|stop|cancel)\b", normalized)
                                and not negative_rule):
                    continue
                if re.match(r'(?:please\s+)?(?:inspect|review|summari[sz]e|quote|describe)\b', normalized):
                    continue
                yield text


def _infer_intents(spans):
    sources: dict[str, list[str]] = {}
    prefix = r'^(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|i\s+(?:want|need)\s+you\s+to\s+)?'
    current = set()
    def add(name, clause):
        current.add(name)
        sources.setdefault(name, []).append(clause)
    clauses = [(clause, True) for clause in _operation_clauses(spans)]
    for clause, expand in clauses:
        current = set()
        parts = re.split(r'\s+(?:and\s+then|then|and)\s+', clause, flags=re.I) if expand else [clause]
        clause = parts[0].strip(' ,:')
        own = re.sub(prefix, '', clause.lower().replace('’', "'"), count=1)
        if re.match(r'(?:call|run|invoke)\s+(?:the\s+)?(?:tool\s+)?(?:read_instruction|check_plan|plan_lookup|replan|wait_replan|try_ladder|try_move|explain_box|list_limits)\b', own):
            own = re.sub(r'^(?:call|run|invoke)\s+(?:the\s+)?(?:tool\s+)?', '', own)
        status = bool(re.match(r'(?:wait_replan|poll)\b', own) or
                      (re.match(r'(?:what\s+is|what\x27s|has|is|did|show|get|tell|check)\b', own)
                       and re.search(r'\b(?:status|progress|finished|completed)\b', own)))
        if status:
            add('wait_replan', clause)
        elif re.match(r'(?:check_plan|check|verify|validate|approve|accept)\b', own):
            add('check_plan', clause)
        if re.match(r'(?:replan|execute|apply)\b', own):
            add('replan', clause)
        if re.match(r'try_ladder\b|(?:try|run|start|use)\s+(?:the\s+)?ladder\b', own):
            add('try_ladder', clause)
        lookup = (re.match(r'(?:plan_lookup|lookup|look\s+up)\b', own) or
                  (re.match(r'(?:find|search|check|show|is|are)\b', own)
                   and re.search(r'\b(?:stored|cached|lattice)\b', own)))
        if lookup:
            add('plan_lookup', clause)
        if re.match(r'(?:try_move|(?:try\s+)?(?:move|moving|drag))\b', own):
            add('try_move', clause)
        if re.match(r'explain_box\b|(?:why|explain)\b.*\bbox\b', own):
            add('explain_box', clause)
        if (re.match(r'list_limits\b', own) or
                (re.match(r'(?:list|show|state|give|tell|report|what|which|how)\b', own)
                 and re.search(r'\b(?:limits?|envelope|maximum|threshold|fill)\b', own)) or
                re.match(r'what\s+can\s+.*\bchange\b', own)):
            add('list_limits', clause)
        if re.match(r'(?:read_instruction|read|parse|interpret)\b', own):
            add('read_instruction', clause)
        elif re.match(r'(?:keep|use|close|leave|place|load|stow|put|avoid|limit|set|across|'
                      r'in\s+bay|for\s+bay|one\s+port|optimise|optimize|ignore|disable|weaken|'
                      r'(?:do not|never)\s+(?:load|stow|place))\b', own):
            if not re.match(r'use\s+(?:the\s+)?ladder\b', own):
                add('read_instruction', clause)
        # A conjunction cannot promote explanatory prose into a command. Only
        # continue an already affirmative operation/request, preserving each
        # operation's own argument clause.
        if current and expand:
            clauses.extend((part.strip(' ,:'), False) for part in parts[1:])
    return sources


class TurnAuthorization:
    denial_text = DENIAL

    def __init__(self, planner_message, documents, selected_sentence, intents, scope_ceiling=None):
        self.planner_message = planner_message
        self.documents = documents
        self.selected_sentence = selected_sentence
        self._intents = frozenset(intents)
        self._scope_ceiling = None if scope_ceiling is None else frozenset(scope_ceiling)
        self._grants: dict[str, set[str]] = {}

    def _grant(self, name, arguments, *, host_action=False):
        if name not in TOOLS or not isinstance(arguments, dict):
            raise ValueError('Invalid host action grant')
        if not host_action and self._scope_ceiling is not None and name not in self._scope_ceiling:
            return
        self._grants.setdefault(name, set()).add(canonical_json(arguments))

    def allows(self, name: str, arguments: dict) -> bool:
        try:
            return canonical_json(arguments) in self._grants.get(name, ())
        except (ValueError, TypeError):
            return False

    def observe(self, name: str, arguments: dict, result: dict):
        """Only an authorized actual tool result can provide follow-up arguments.

        Result text is never parsed for commands. Existing planner intent must
        already authorize the corresponding follow-up operation.
        """
        if not self.allows(name, arguments) or not isinstance(result, dict):
            return
        if name == 'read_instruction':
            proposed = result.get('proposed_reading')
            if isinstance(proposed, dict):
                reading_id = proposed.get('reading_id')
                if 'replan' in self._intents and isinstance(reading_id, str) and reading_id:
                    self._grant('replan', {'reading_id': reading_id})
                parsed = proposed.get('parsed')
                if 'plan_lookup' in self._intents and isinstance(parsed, dict):
                    self._grant('plan_lookup', {'rules': [deepcopy(parsed)]})
        job_id = result.get('job_id')
        if (name in ('replan', 'try_ladder', 'wait_replan')
                and isinstance(job_id, str) and job_id
                and self._intents & {'replan', 'try_ladder', 'wait_replan'}):
            self._grant('wait_replan', {'job_id': job_id})

    def as_dict(self):
        import json
        return {'planner_message': self.planner_message,
                'selected_sentence': self.selected_sentence,
                'allowed_tools': sorted(self._intents),
                'authorized_actions': [{'tool': name, 'arguments': json.loads(arguments)}
                                       for name, rows in sorted(self._grants.items())
                                       for arguments in sorted(rows)]}


def build_authorization(message: str, *, context: dict | None = None,
                        request: TurnRequest | None = None) -> TurnAuthorization:
    """Conservative convenience routing; explicit host requests cover precise UI actions.

    This identifies requested operations, never stowage rules. Rules can only be
    created by the reader, whose input is pinned to a selected original string.
    """
    if request is not None and not isinstance(request, TurnRequest):
        raise TypeError('request must be a host TurnRequest')
    context = context if isinstance(context, dict) else {}
    references = deepcopy(request.references) if request is not None else {}
    if not isinstance(references, dict):
        raise TypeError('references must be a host reference dictionary')
    if set(references) - {'reading_id', 'job_id', 'box', 'slot', 'rules'}:
        raise ValueError('Unknown host reference field')
    for key in ('box', 'slot'):
        if key in references:
            context = dict(context, **{'selected_' + key: references[key]})
    spans, documents = _split(message, context)
    planner = '\n'.join(span.strip() for span in spans)
    sources = _infer_intents(spans)
    intents = set(sources)
    scope_ceiling = None
    if request is not None:
        if not isinstance(request.allowed_tools, (set, frozenset)) or request.allowed_tools - TOOLS:
            raise ValueError('allowed_tools must be a host set of known tools')
        if not isinstance(request.allowed_tools, _InferredToolScope):
            scope_ceiling = frozenset(request.allowed_tools)
            intents = set(scope_ceiling)
    reader_command = any(re.match(r'\s*(?:please\s+)?(?:read_instruction|read|parse|interpret)\b',
                                  source, re.I) for source in sources.get('read_instruction', []))

    selected = request.selected_sentence if request is not None else None
    if selected is not None and (not isinstance(selected, str) or not selected):
        raise ValueError('selected_sentence must be nonempty exact text')
    if selected is None and 'read_instruction' in intents:
        # Without an explicit host reference, only a contiguous planner-owned
        # original span can be selected. Quoted payload selection is explicit.
        if 'read_instruction' not in sources or (documents and reader_command):
            intents.discard('read_instruction')
        elif len(spans) == 1 and spans[0].strip():
            selected = spans[0] if not documents else spans[0].strip()
        else:
            intents.discard('read_instruction')
    auth = TurnAuthorization(planner, documents, selected, intents, scope_ceiling)
    for name in ('check_plan', 'try_ladder', 'list_limits'):
        if name in intents:
            auth._grant(name, {})
    if 'read_instruction' in intents and selected is not None:
        auth._grant('read_instruction', {'sentence': selected})
    if 'replan' in intents:
        arguments_text = '\n'.join(sources.get('replan', []))
        named = [match[1] for match in re.finditer(
            r'\b(?:reading_id|reading\s+id|reading|receipt)(?:\s*[:=]\s*|\s+)([\w:-]+(?:\.[\w:-]+)*)', arguments_text, re.I)
            if match[1].lower() not in {'that', 'which', 'has', 'not', 'to', 'and', 'please', 'before', 'first'}]
        if 'reading_id' in references:
            named = [references['reading_id']]
        for reading_id in named or _reading_ids(context):
            if not isinstance(reading_id, str) or not reading_id:
                raise ValueError('Host reading reference must be nonempty text')
            auth._grant('replan', {'reading_id': reading_id})
    rules = references.get('rules', context.get('confirmed_rules'))
    if 'plan_lookup' in intents and isinstance(rules, list):
        auth._grant('plan_lookup', {'rules': deepcopy(rules)})
    if 'wait_replan' in intents:
        arguments_text = '\n'.join(sources.get('wait_replan', []))
        named = [match[1] for match in re.finditer(
            r'\b(?:job_id|job\s+id|job)(?:\s*[:=]\s*|\s+)([\w:-]+(?:\.[\w:-]+)*)', arguments_text, re.I)
            if match[1].lower() not in {'status', 'finished', 'completed', 'that', 'which', 'has', 'is'}]
        job_ids = [references['job_id']] if 'job_id' in references else named or [context.get('active_job_id')]
        for job_id in job_ids:
            if isinstance(job_id, str) and job_id:
                auth._grant('wait_replan', {'job_id': job_id})
    for operation in ('try_move', 'explain_box'):
        operation_sources = sources.get(operation, []) or ([''] if operation in intents else [])
        for source in operation_sources:
            box, slot = _selected(context, 'box'), _selected(context, 'slot')
            for key in ('box', 'slot'):
                for match in re.finditer(r'\b' + key + r'(?:_id)?\s*[:=]?\s+([\w:-]+(?:\.[\w:-]+)*)', source, re.I):
                    if match[1].lower() not in {'to', 'there', 'here', 'the', 'this', 'that', 'it', 'is', 'identifier', 'id',
                                              'i', 'me', 'my', 'we', 'our', 'you', 'your', 'they', 'their', 'which', 'what'}:
                        if key == 'box':
                            box = match[1]
                        else:
                            slot = match[1]
            if operation == 'explain_box' and box:
                auth._grant(operation, {'box': box})
            elif operation == 'try_move' and box and slot:
                auth._grant(operation, {'box': box, 'slot': slot})
            # Missing references call for a planner question; they do not
            # authorize an unrelated ship-facts operation.
    if request is not None:
        # Snapshot grants now; mutation of the caller's dictionaries cannot widen
        # permissions during model generation or leak them into another turn.
        for grant in request.actions:
            if not isinstance(grant, ActionGrant):
                raise TypeError('actions must contain host ActionGrant values')
            if grant.name == 'read_instruction':
                sentence = grant.arguments.get('sentence')
                if selected is not None and sentence != selected:
                    raise ValueError('Read grant must match selected_sentence exactly')
                if not isinstance(sentence, str) or not sentence:
                    raise ValueError('Read grant requires exact nonempty sentence')
                auth.selected_sentence = sentence
            auth._grant(grant.name, deepcopy(grant.arguments), host_action=True)
    return auth
