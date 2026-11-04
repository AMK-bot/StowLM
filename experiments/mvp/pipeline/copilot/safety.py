"""Allow model prose; gate current evidence, acceptance and plan references."""
from __future__ import annotations

import re
from typing import Any

from experiments.mvp.pipeline.contract.schema import CopilotTurn, Mode, sha256_json

_DIGEST = re.compile(r'^[0-9a-f]{64}$')
_NUMBERS = re.compile(r'(?<![\w.])-?\d+(?:[.,]\d+)*(?:%|\b)|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion)\b', re.I)
_PROSE_DIGEST = re.compile(r'\b[0-9a-fA-F]{64}\b')
_IDENTITY_FIELDS = frozenset({
    'schema_version', 'planning_request_id', 'planning_generation',
    'reading_id', 'pending_reading_id', 'confirmed_reading_id', 'job_id',
    'instance_id', 'source', 'model', 'adapter', 'prompt', 'authentication',
})
NO_EVIDENCE = 'I need tool evidence before answering. No plan has been accepted.'


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, (int, float)):
        yield str(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            # Request generations, identities and provenance describe the
            # receipt, not a measured quantity or a rule in the plan.
            if key not in _IDENTITY_FIELDS and not key.endswith('_sha256'):
                yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def number_tokens(text: str) -> set[str]:
    # Digests have their own current-check gate; their digits cannot supply
    # quantities and a valid all-numeric digest is not itself a quantity claim.
    return {match.group().lower() for match in _NUMBERS.finditer(_PROSE_DIGEST.sub('', text))}


def numbers_supported(say: str, results: list[dict]) -> bool:
    numbers = set()
    for result in results:
        for text in _strings(result):
            numbers.update(number_tokens(text))
    return number_tokens(say).issubset(numbers)


def checked_digest(result: dict) -> str | None:
    """Accept only an explicit full-check success, never a screen or relaxed plan."""
    checker = result.get('checker')
    digest = result.get('plan_sha256')
    if (isinstance(checker, dict) and checker.get('passed') is True
            and checker.get('screen') == 'full' and result.get('passed') is True
            and result.get('status') not in ('relaxed_plan', 'out_of_envelope', 'needs_confirmation', 'no_checked_plan', 'error', 'running', 'queued', 'pending', 'stale')
            and isinstance(digest, str) and _DIGEST.fullmatch(digest)):
        return digest
    return None


def _scrub(value, checked: set[str], forbidden: set[str], field=''):
    """Unchecked digests must not hide in the turn's nested audit dictionaries."""
    if isinstance(value, dict):
        return {key: _scrub(item, checked, forbidden, key) for key, item in value.items()
                if key != 'plan_sha256' or (isinstance(item, str) and item in checked)}
    if isinstance(value, list):
        return [_scrub(item, checked, forbidden, field) for item in value]
    if isinstance(value, str):
        # Typed provenance/identity fields remain usable; quoted prose cannot
        # launder an unchecked plan reference, even by quoting a provenance hash.
        if field not in ('reading_id', 'pending_reading_id', 'confirmed_reading_id') and not field.endswith('_sha256'):
            value = re.sub(r'\b[0-9a-fA-F]{64}\b',
                           lambda match: match.group() if match.group() in checked
                           else '[unchecked plan reference withheld]', value)
        elif field == 'plan_sha256':
            for digest in forbidden:
                value = value.replace(digest, '[unchecked plan reference withheld]')
    return value


def _sources(results):
    texts = []
    for result in results:
        text = result.get('text')
        alternatives = []
        for alternative in result.get('alternatives', []):
            if not isinstance(alternative, dict) or not isinstance(alternative.get('text'), str):
                continue
            raw = alternative['text'].strip()
            if not raw:
                continue
            label = alternative.get('label')
            identifier = alternative.get('id')
            suffix = ' ' + identifier if isinstance(identifier, str) and identifier else ''
            if label == 'checked' and _alternative_checked(alternative):
                prefix = 'Checked alternative' + suffix + '; requires planner choice: '
            elif label == 'screen_only':
                prefix = 'Screen only, not checked; requires planner choice: '
            elif label == 'historical_witness':
                prefix = 'Historical witness, not rechecked; requires planner choice: '
            else:
                prefix = 'Unchecked alternative; requires planner choice: '
            rendered = prefix + raw
            alternatives.append(rendered)
            if isinstance(text, str):
                text = text.replace(raw, rendered)
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
        proof = result.get('proof')
        if isinstance(proof, dict):
            for fact in proof.get('facts', []):
                if isinstance(fact, dict) and isinstance(fact.get('text'), str):
                    texts.append(fact['text'].strip())
        texts.extend(alternatives)
    return list(dict.fromkeys(text for text in texts if text))


_ACCEPTANCE = re.compile(r'\b(?:accept(?:ed|s)?|approv(?:e|ed|es)|safe|legal|checked|verified|'
                         r'certified|passed|passes|compliant|feasible|works|ready to sail|will sail|can sail)\b', re.I)
_NEGATION = re.compile(r"\b(?:not|no|never|cannot|can't|don't|doesn't|didn't|hasn't|haven't|isn't|aren't|wasn't|weren't|without|unable)\b", re.I)


def _question(sentence):
    interrogative = re.match(r'^(?:is|are|was|were|can|could|would|will|should|do|does|did|has|have)\b', sentence, re.I)
    inversion = re.match(r'^(?:is|are|was|were|can|could|would|will|should|do|does|did|has|have)\s+'
                         r'(?:i|you|we|it|this|that|these|those|the\s+(?:plan|arrangement|move|option)|'
                         r'both\s+(?:options|alternatives))\b', sentence, re.I)
    return bool(sentence.endswith('?') and re.match(r'^(?:what|which|how|why|when)\b', sentence, re.I)
                or interrogative and (sentence.endswith('?') or inversion))


def _positive_claims(text):
    """Conservative acceptance-language check, separate from ordinary questions."""
    fragments = []
    for whole in re.split(r'(?<=[.!?])\s+|[;\n]+', text):
        if not _question(whole.strip()):
            fragments.extend(re.split(r',+|\s+(?:but|yet|however|and)\s+', whole, flags=re.I))
    for sentence in fragments:
        sentence = sentence.strip()
        if _question(sentence):
            continue
        normalized = re.sub(r'\bnot only\b|\bno doubt\b', '', sentence.replace('’', "'"), flags=re.I)
        if re.search(r'\b(?:legal|checked|safe)\s+plan\s+(?:requires|needs|must)\b', normalized, re.I):
            continue
        # Checking a fact or a limit is not acceptance of an arrangement.
        if (re.search(r'\b(?:limits?|facts?|reading|instruction|tool results?)\b', normalized, re.I)
                and not re.search(r'\b(?:plan|arrangement|ship|move|alternative|option|candidate)\b', normalized, re.I)):
            continue
        claims = [match for match in _ACCEPTANCE.finditer(normalized)
                  if not _NEGATION.search(normalized[:match.start()])]
        if (claims and {match.group().lower() for match in claims} == {'checked'}
                and re.search(r'\bchecked\s+(?:weighted\s+)?(?:lifts?|costs?|quantities|numbers)\b',normalized,re.I)
                and not re.search(r'\b(?:plan|arrangement|ship|vessel|move|alternative|option|candidate|card)\b',normalized,re.I)):
            continue  # A checked quantity is not an accepted arrangement.
        if re.search(r'\bnot\s+(?:unchecked|unsafe|illegal|unapproved|unverified)\b', normalized, re.I):
            claims.append(None)
        if claims:
            yield sentence, [match.group().lower() for match in claims if match is not None]


def _alternatives(results):
    return [alternative for result in results for alternative in result.get('alternatives', [])
            if isinstance(alternative, dict)]


def _alternative_checked(alternative):
    return (alternative.get('label') == 'checked' and alternative.get('checked') is True
            and alternative.get('passed') is True
            and alternative.get('checker', {}).get('passed') is True
            and alternative.get('checker', {}).get('screen') == 'full'
            and alternative.get('instruction_recheck', {}).get('passed') is True)


def acceptance_supported(say: str, visible: list[dict], eligible: list[dict]) -> bool:
    """A claim needs the latest relevant full check, never a screen or old plan."""
    alternatives = _alternatives(visible)
    claims = []
    for block in re.split(r'\n\s*\n|\n(?=\s*[-*\u2022]\s)', say):
        scoped = [row for row in alternatives if isinstance(row.get('id'),str) and re.search(
            r'(?<!\w)' + re.escape(row['id']) + r'(?!\w)', block, re.I)]
        claims.extend((sentence,words,scoped) for sentence,words in _positive_claims(block))
    for sentence, words, scoped in claims:
        if re.search(r'\b(?:certified|class.approved)\b', sentence, re.I):
            return False  # A checker is not an authority's certification.
        named = [alternative for alternative in alternatives
                 if isinstance(alternative.get('id'), str) and re.search(
                     r'(?<!\w)' + re.escape(alternative['id']) + r'(?!\w)', sentence, re.I)]
        current_subject = re.search(
            r'\b(?:this|the|your|my|our|current|unchecked)\s+(?:(?:current|unchecked)\s+)?'
            r'(?:plan|ship|vessel|arrangement)\b', sentence, re.I)
        if current_subject and not eligible:
            return False
        check_only = bool(words) and set(words) <= {'checked','verified','passed','passes'}
        named_check = bool(named) and check_only
        scoped_check = (not named and bool(scoped) and check_only and re.search(
            r'^\s*(?:[-\u2022]\s*)?(?:(?:the|its|a|full|native|recorded)\s+)*'
            r'(?:checker|check|checked|verified|passed|passes)\b', sentence, re.I))
        if named_check or scoped_check or re.search(r'\b(?:alternatives?|options?|cards?|candidates?|witness(?:es)?)\b', sentence, re.I):
            targets = named or (scoped if scoped_check else alternatives)
            explicit = re.findall(r'\b(?:alternative|option|candidate|card)\s+([\w-]+)', sentence, re.I)
            ignored = {'is', 'are', 'has', 'can', 'requires', 'requiring', 'would', 'was', 'were',
                       'that', 'which', 'passed', 'works', 'for', 'with', 'must', 'checked'}
            known = {alternative.get('id', '').lower() for alternative in alternatives
                     if isinstance(alternative.get('id'), str)}
            if any(reference.lower() not in known | ignored for reference in explicit):
                return False
            if not targets or not all(_alternative_checked(alternative) for alternative in targets):
                return False
            if any(word.startswith(('accept', 'approv')) for word in words):
                current = {checked_digest(result) for result in eligible}
                if not all(alternative.get('plan_sha256') in current for alternative in targets):
                    return False
            continue
        if (re.search(r'\bmove\b', sentence, re.I)
                and not re.search(r'\b(?:plan|arrangement|ship)\b', sentence, re.I)):
            if not any(result.get('verdict', {}).get('screen') == 'full'
                       and result.get('verdict', {}).get('legal') is True for result in visible):
                return False
            continue
        if (words and set(words) <= {'passed', 'passes'}
                and re.search(r'\b(?:fast|static)\s+screen\b', sentence, re.I)):
            if not any((result.get('checker', {}).get('screen') == 'fast'
                        and result.get('checker', {}).get('passed') is True)
                       or result.get('screen_passed') is True for result in visible):
                return False
            continue
        if not eligible:
            return False
    return True


def _current_evidence(tool_calls, results):
    """Replay request chronology; an older certificate cannot win by arriving last."""
    visible, eligible = [], []
    generation, request_id = -1, None
    seen = set()
    for index, result in enumerate(results):
        call = tool_calls[index] if index < len(tool_calls) else {}
        name = call.get('name')
        current_id = result.get('planning_request_id')
        current_generation = result.get('planning_generation')
        has_generation = type(current_generation) is int
        if has_generation and current_generation < generation:
            continue
        if current_id is not None and current_id != request_id and current_id in seen:
            continue
        if (request_id is not None and checked_digest(result) is not None
                and current_id != request_id
                and (not has_generation or current_generation <= generation)):
            continue
        if (has_generation and current_generation == generation and request_id is not None
                and current_id is not None and current_id != request_id):
            continue
        newer = ((has_generation and current_generation > generation)
                 or (current_id is not None and current_id != request_id))
        if newer:
            visible, eligible = [], []
            if has_generation:
                generation = current_generation
            if current_id is not None:
                request_id = current_id
                seen.add(current_id)
        # Every new plan request replaces earlier acceptance, even when it
        # completes inline, fails before dispatch or returns a relaxed result.
        status = result.get('status')
        unresolved = (result.get('proposed_reading') is not None or name == 'read_instruction'
                      or status in ('needs_confirmation', 'out_of_envelope', 'running', 'queued', 'pending', 'stale'))
        planning = name in ('replan', 'try_ladder', 'plan_lookup')
        failed = (status in ('no_checked_plan', 'relaxed_plan', 'error')
                  and name not in ('list_limits', 'explain_box', 'try_move'))
        failed_check = name == 'check_plan' and checked_digest(result) is None
        if unresolved or planning or failed or failed_check:
            visible, eligible = [], []
        visible.append(result)
        if (not unresolved and not failed and checked_digest(result) is not None
                and (request_id is None or current_id == request_id)):
            eligible.append(result)
    return visible, eligible


def _say_context(tool_calls, results):
    visible, eligible = _current_evidence(tool_calls, results)
    checked = {digest for result in eligible if (digest := checked_digest(result))}
    checked.update(digest for alternative in _alternatives(visible)
                   if _alternative_checked(alternative) and (digest := checked_digest(alternative)))
    return visible, eligible, checked


def _typed_references(tool_calls, results, visible):
    """Identity receipts are not plan certificates or measured quantities."""
    readings, reports = set(), set()
    for result in visible:
        for key in ('reading_id', 'pending_reading_id', 'confirmed_reading_id',
                    'negotiation_reading_id'):
            value = result.get(key)
            if isinstance(value, str) and _DIGEST.fullmatch(value):
                readings.add(value)
        checker = result.get('checker')
        report = checker.get('report', {}) if isinstance(checker, dict) else {}
        value = report.get('report_sha256') if isinstance(report, dict) else None
        if isinstance(value, str) and _DIGEST.fullmatch(value):
            reports.add(value)
    # A planning failure can revoke acceptance while leaving a reader proposal
    # awaiting confirmation. Only a subsequent reader event replaces that ID.
    for index in range(len(results) - 1, -1, -1):
        name = tool_calls[index].get('name') if index < len(tool_calls) else None
        result = results[index]
        if name == 'read_instruction' or (name is None and result.get('proposed_reading') is not None):
            prefix_visible, _ = _current_evidence(tool_calls[:index + 1], results[:index + 1])
            if not any(item is result for item in prefix_visible):
                continue  # A late old-generation reader receipt is not current.
            proposed = result.get('proposed_reading')
            value = proposed.get('reading_id') if isinstance(proposed, dict) else None
            if isinstance(value, str) and _DIGEST.fullmatch(value):
                readings.add(value)
            break
    return readings, reports


def _unsupported_references(text, tool_calls, results, visible, checked):
    readings, reports = _typed_references(tool_calls, results, visible)
    def plan_references(value):
        if isinstance(value, dict):
            if isinstance(value.get('plan_sha256'), str):
                yield value['plan_sha256']
            for item in value.values():
                yield from plan_references(item)
        elif isinstance(value, list):
            for item in value:
                yield from plan_references(item)
    unchecked_plans = set(plan_references(results)) - checked
    for match in _PROSE_DIGEST.finditer(text):
        digest = match.group()
        if digest in checked:
            continue
        # Require an explicit adjacent type label. A quoted digest, arbitrary
        # provenance field, or a reading ID relabelled as a plan cannot pass.
        left = re.split(r'[.!?;\n]', text[:match.start()])[-1]
        right = re.split(r'[.!?;\n]', text[match.end():], maxsplit=1)[0]
        plan_label = re.search(r'\bplan(?:_sha256|\s+(?:hash|digest|sha256|reference|id))\b', left, re.I)
        if digest in unchecked_plans or plan_label or re.search(r'\bplan\b', right, re.I):
            yield digest
            continue
        reading_label = re.search(r'\b(?:reading(?:_id|\s+id)?|reader\s+result\s*\(\s*id)\s*[:=]?\s*$', left, re.I)
        report_label = re.search(r'\breport(?:_sha256|\s+(?:hash|digest|sha256))?\s*(?:is\s*)?[:=]?\s*$', left, re.I)
        if not (reading_label and digest in readings or report_label and digest in reports):
            yield digest


def gate_diagnostics(say, tool_calls, results):
    """Report violations of model prose before repair; scoring remains independent."""
    visible, eligible, checked = _say_context(tool_calls, results)
    candidate = say.strip() if isinstance(say, str) else ''
    reasons = []
    if not candidate:
        reasons.append('empty_answer')
    if not numbers_supported(candidate, visible):
        reasons.append('unsupported_number')
    if not acceptance_supported(candidate, visible, eligible):
        reasons.append('unsupported_acceptance')
    if any(_unsupported_references(candidate, tool_calls, results, visible, checked)):
        reasons.append('unchecked_plan_reference')
    return {'reasons': reasons}


def finish_turn(say, tool_calls, results, mode) -> CopilotTurn:
    """Keep free prose/questions; repair evidence violations with software text."""
    visible, eligible, checked = _say_context(tool_calls, results)
    def unchecked_refs(value):
        if isinstance(value, dict):
            digest = value.get('plan_sha256')
            if isinstance(digest, str) and _DIGEST.fullmatch(digest) and digest not in checked:
                yield digest
            for item in value.values():
                yield from unchecked_refs(item)
        elif isinstance(value, list):
            for item in value:
                yield from unchecked_refs(item)
    forbidden = set(unchecked_refs(results))
    sources = _sources(visible)
    # No unchecked hash can be surfaced even if a provider included it in text.
    sources = [text for text in sources
               if not any(_unsupported_references(text, tool_calls, results, visible, checked))]
    required = []
    refusal_templates = []
    for result in visible:
        template = result.get('refusal_template')
        if not template and result.get('status') == 'refused':
            template = result.get('text')
        if isinstance(template, str) and template.strip():
            refusal_templates.append(template.strip())
        proof = result.get('refusal', result.get('proof'))
        if not isinstance(proof, dict) or proof.get('status') != 'static_impossible':
            continue
        identifiers = set(proof.get('required_fact_ids', []))
        required.extend(fact['text'] for fact in proof.get('facts', [])
                        if fact.get('id') in identifiers and isinstance(fact.get('text'), str))
    candidate = say.strip() if isinstance(say, str) else ''
    if gate_diagnostics(candidate, tool_calls, results)['reasons']:
        safe_sources = [text for text in sources if numbers_supported(text, visible)
                        and acceptance_supported(text, visible, eligible)]
        candidate = safe_sources[-1] if safe_sources else NO_EVIDENCE
        for result in reversed(visible):
            source = _sources([result])
            if source and source[0] in safe_sources:
                candidate = source[0]
                break
    # Software owns these mandatory facts/templates, while Spark may add its
    # own explanation or question around them. Never replay provider refusal.reason.
    additions = [text for text in dict.fromkeys(refusal_templates + required) if text not in candidate]
    for alternative in _alternatives(visible):
        if _alternative_checked(alternative):
            continue
        identifier = alternative.get('id')
        raw = alternative.get('text')
        mentioned = isinstance(raw, str) and raw and raw in candidate
        if isinstance(identifier, str) and identifier:
            pattern = (r'\b(?:option|alternative|candidate)\s+' + re.escape(identifier) + r'\b'
                       if len(identifier) <= 2 else r'(?<!\w)' + re.escape(identifier) + r'(?!\w)')
            mentioned = mentioned or re.search(pattern, candidate, re.I) is not None
        if mentioned:
            name = 'Alternative ' + identifier if isinstance(identifier, str) and identifier else 'Alternative'
            status = {'screen_only': 'screen only; not fully checked',
                      'historical_witness': 'historical witness; not rechecked'}.get(
                          alternative.get('label'), 'unchecked')
            additions.append(name + ': ' + status + '; requires planner choice.')
    if additions:
        candidate = candidate + '\n' + ' '.join(additions)
    proposed = None
    for index in range(len(results) - 1, -1, -1):
        name = tool_calls[index].get('name') if index < len(tool_calls) else None
        if name == 'read_instruction' or (name is None and results[index].get('proposed_reading') is not None):
            value = results[index].get('proposed_reading')
            proposed = value if isinstance(value, dict) else None
            break
    digest = next((checked_digest(result) for result in reversed(eligible)
                   if checked_digest(result) is not None), None)
    calls = _scrub(tool_calls, checked, forbidden)
    for call in calls:
        if isinstance(call.get('result'), dict):
            call['result_sha256'] = sha256_json(call['result'])
    return CopilotTurn(say=candidate, tool_calls=calls,
                       proposed_reading=_scrub(proposed, checked, forbidden),
                       plan_sha256=digest, mode=Mode(mode))
