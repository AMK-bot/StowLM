"""Fair deterministic reader, authored blind to every sealed sentence.

Sources: public reader contract/vocabulary, validation-error grammar and public
IMDG names. No learned model, data file, predictions, or sealed access. Explicit
linguistic recognizers cover all four rule types; refusal precedence is global.
The parser deliberately returns ambiguity for a missing semantic argument.
"""
from __future__ import annotations

import re
import unicodedata

VERSION = 'reader-v3-fair-parser/1'
SCHEMA_VERSION = 'stowlm.reader/2'
NUMBERS = dict(zip(('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty').split(), range(21)))
NUMBERS.update({'a': 1, 'an': 1, 'single': 1})
TENS = {'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
        'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90}
RADIO_DIGITS = {'tree': 'three', 'fife': 'five', 'niner': 'nine'}
CLASSES = {str(n) for n in range(1, 10)} | {f'1.{n}' for n in range(1, 7)} | {f'2.{n}' for n in range(1, 4)} | {f'4.{n}' for n in range(1, 4)} | {'5.1', '5.2', '6.1', '6.2'}
# Longer/more specific public class names take priority over broad words.
ALIASES = (
    (r'non[- ]flammable(?: non[- ]toxic)? gas(?:es)?', '2.2'),
    (r'(?:toxic|poisonous) gas(?:es)?', '2.3'),
    (r'flammable gas(?:es)?', '2.1'),
    (r'flammable (?:liquids?|liquid cargo)', '3'),
    (r'flammable solids?', '4.1'),
    (r'(?:spontaneously combustible|liable to spontaneous combustion)', '4.2'),
    (r'(?:dangerous when wet|water[- ]reactive)', '4.3'),
    (r'organic peroxides?', '5.2'),
    (r'(?:oxidisers?|oxidizers?|oxidising substances?|oxidizing substances?)', '5.1'),
    (r'infectious (?:substances?|materials?)', '6.2'),
    (r'(?:toxic|poisonous)(?: substances?| materials?| cargo| solids?| liquids?)', '6.1'),
    (r'(?:corrosives?|corrosive substances?)', '8'),
    (r'(?:radioactive(?: materials?| substances?)?)', '7'),
    (r'explosives?', '1'),
    (r'gas(?:es)?', '2'),
    (r'miscellaneous dangerous (?:goods|substances)', '9'),
)


def hit(pattern, text):
    return re.search(pattern, text, re.I)


def normalize(sentence):
    text = unicodedata.normalize('NFKC', sentence).casefold().replace('’', "'")
    text = re.sub(r'(?<=\w)[-](?=\w)', ' ', text)
    # Standard maritime/radio digit pronunciations, limited to numeric slots so
    # an ordinary noun "tree" in commentary is not globally changed.
    radio = '|'.join(RADIO_DIGITS)
    text = re.sub(r'\b(bay|class|division|point)\s+(' + radio + r')\b',
                  lambda m: m[1] + ' ' + RADIO_DIGITS[m[2]], text)
    text = re.sub(r'\b(' + radio + r')(?=\s+(?:bays?|tiers?)\b)',
                  lambda m: RADIO_DIGITS[m[1]], text)
    # Decimal class divisions stay decimals, and spoken point divisions work.
    text = re.sub(r'\b(' + '|'.join(NUMBERS) + r') point (' + '|'.join(NUMBERS) + r')\b', lambda m: f'{NUMBERS[m[1]]}.{NUMBERS[m[2]]}', text)
    # Compose tens before replacing individual words: "twenty seven" is 27,
    # never a pair of separate numeric arguments. Hyphens were normalized above.
    units = '|'.join(k for k, n in NUMBERS.items() if 1 <= n <= 9 and k not in {'a', 'an', 'single'})
    text = re.sub(r'\b(' + '|'.join(TENS) + r')(?:\s+(' + units + r'))?\b',
                  lambda m: str(TENS[m[1]] + (NUMBERS[m[2]] if m[2] else 0)), text)
    text = re.sub(r'\b(' + '|'.join(k for k in NUMBERS if k not in {'a', 'an', 'single'}) + r')\b', lambda m: str(NUMBERS[m[0]]), text)
    return re.sub(r'\s+', ' ', text).strip()


def refuse(code):
    reasons = {'loosening': 'Would relax an existing restriction.', 'ambiguous': 'Uncertain instruction or missing argument.', 'unsupported': 'Outside the supported restriction vocabulary.', 'not_an_instruction': 'No operational instruction is being given.'}
    return {'schema_version': SCHEMA_VERSION, 'refusal': {'refused': True, 'code': code, 'reason': reasons[code]}}


def envelope(constraint):
    return {'schema_version': SCHEMA_VERSION, 'constraint': constraint}


def _loosening(t):
    patterns = (
        r'\b(?:reopen|re open|unblock|rescind|waive|override|disregard|bypass|suspend|exempt|relax|cancel|withdraw|lift|abolish)\b',
        r'\b(?:ignore|remove|drop)\b.{0,70}\b(?:rules?|limits?|caps?|restrictions?|closures?|bans?|separations?|segregations?|exclusions?|requirements?)\b',
        r'\b(?:raise|increase)\b.{0,65}\b(?:limits?|caps?|maximums?|tiers?|heights?|ceilings?)\b',
        r'\b(?:reduce|decrease|shorten|lessen)\b.{0,65}\b(?:distances?|separations?|clearances?|buffers?|exclusions?|gaps?)\b',
        r'\b(?:exceptions?|dispensations?)\b.{0,65}\b(?:rules?|limits?|caps?|closures?|bans?|existing|current)\b',
        r'\b(?:permit|allow|authorize|accept|okay)\b.{0,80}\b(?:mixed ports?|mix(?:ing)? (?:ports?|destinations?)|multiple (?:ports?|destinations?)|more than 1 (?:port|destination)|break|violate|ignore|despite|even (?:if|when)|again)\b',
        r'\b(?:load|loading|stow|stowing)\b.{0,60}\b(?:closed|forbidden|banned|prohibited)\b',
        r'\b(?:open|available)\b.{0,25}\b(?:again|for loading)\b',
        r'\b(?:no longer|need not|do not need|don\'t need)\b.{0,50}\b(?:obey|keep|comply|separat|limit|single|1 port)',
        r'\b(?:closer|less clearance|less separation|higher than the (?:current|existing))\b',
    )
    if any(hit(p, t) for p in patterns):
        return True
    # Explicit replacement of an existing numeric cap/distance with a weaker one.
    cap = hit(r'(?:existing|current)\s+(\d+)\s*(?:tier|high).{0,70}(?:to|permit|allow)\s+(\d+)', t)
    distance = hit(r'(?:existing|current)\s+(\d+)\s*bay.{0,70}(?:to|instead use|use)\s+(\d+)', t)
    return bool(cap and int(cap[2]) > int(cap[1]) or distance and int(distance[2]) < int(distance[1]))


def _class(t):
    explicit = re.findall(r'\b(?:imdg\s+)?(?:class|division)\s*(\d+(?:\.\d+)?)\b', t)
    if explicit:
        return list(dict.fromkeys(explicit))
    found = []
    remainder = t
    for pattern, cls in ALIASES:
        expression = r'\b' + pattern + r'\b'
        if hit(expression, remainder):
            found.append(cls)
            remainder = re.sub(expression, ' ', remainder)
    return list(dict.fromkeys(found))


def parse(sentence: str, context: dict) -> dict:
    """Return exactly one strict reader envelope; never consult outside files."""
    t = normalize(sentence)
    if _loosening(t):
        return refuse('loosening')
    # Polite would/could questions are firm requests unless explicitly tentative.
    if hit(r'\b(?:perhaps|maybe|might|possibly|consider(?:ing)?|suggest(?:ion)?|propos(?:al|ed|e)|undecided|pending|awaiting|not (?:yet )?(?:approved|final)|not an? (?:approved )?order|depending|if\b|whether|not sure|unsure|decid(?:ing|e) later|subject to)\b', t):
        return refuse('ambiguous')
    informational = hit(r'^(?:what|why|when|where|how|is there|are there|does|did|was|were)\b', t)
    commentary = hit(r'\b(?:for information only|only (?:recounting|reporting)|old report|yesterday|last voyage|no instruction|do not apply|not an instruction|just (?:asking|describing)|historically)\b', t)
    if informational or commentary:
        return refuse('not_an_instruction')
    # Strip nonsemantic address and request wrappers, never an operational clause.
    t = re.sub(r'^(?:(?:terminal|chief|planner|bridge|control|captain|mate|copy|roger|attention)[,: ]+)+', '', t)
    t = re.sub(r'^(?:(?:please|kindly)\s+|(?:can|could|would|will) you\s+|i (?:need|want) you to\s+)+', '', t)
    t = re.sub(r'\b(?:please|kindly|over|out)\s*[.!?]*$', '', t).strip()
    bays = [int(n) for n in re.findall(r'\bbay\s*(?:no\.?\s*|number\s*|#\s*)?(\d+)\b', t)]
    unique_bays = set(bays)
    is_height = bool(hit(r'\b(?:tiers?|stack\w*|height|high|ceiling)\b', t))
    clearance = bool(hit(r'\b(?:away|clear|clearance|separat\w*|distance|buffer|exclusion|apart|off|between|from)\b', t))
    if clearance and not _class(t) and hit(r'\b(?:toxic|flammable|poisonous|dangerous goods|hazardous)\b', t):
        return refuse('ambiguous')
    is_dg = bool(_class(t) and clearance)
    is_hatch = bool(hit(r'\b(?:hatch(?:es)?|covers?)\b', t) and hit(r'\b(?:ports?|pods?|destinations?|discharge)\b', t))
    is_close = bool(hit(r'\b(?:close|closed|shut|block|bar|ban|unavailable|out of (?:service|action)|no (?:load|loading|cargo|stowage)|do not (?:load|stow|use)|don\'t (?:load|stow|use)|must (?:stay|remain) (?:empty|unused)|must not be (?:used|loaded)|loading (?:prohibited|forbidden)|keep\b.{0,30}\b(?:empty|clear|closed|unused))\b', t))
    categories = [is_height, is_dg, is_hatch, is_close]
    if is_dg and is_close and not hit(r'\b(?:close|closed|shut|block|bar|ban|no loading|do not load|unavailable)\b', t):
        # "Keep class X clear of bay N" is distance language, not closure.
        is_close = False
        categories[3] = False
    # A DG clearance phrase may contain a stack noun, but cannot specify a cap.
    if is_dg and not hit(r'\b(?:tiers?|high|height|ceiling)\b', t):
        is_height = False
        categories[0] = False
    if sum(categories) > 1 or len(unique_bays) > 1:
        return refuse('unsupported')
    if unique_bays - set(context.get('bays', [])):
        return refuse('unsupported')
    if is_height:
        if hit(r'\b(?:minus|negative|at least|minimum|no fewer than)\b', t):
            return refuse('unsupported')
        deck = bool(hit(r'\b(?:deck|topside|above (?:the )?hatch|exposed|weather deck)\b', t))
        hold = bool(hit(r'\b(?:hold|below (?:the )?(?:deck|hatch)|under (?:the )?(?:deck|hatch))\b', t))
        # "below deck" names hold, not two zones.
        if hold and not hit(r'\b(?:on deck|above (?:the )?hatch|deck and|and deck)\b', t):
            deck = False
        if not deck and not hold:
            return refuse('ambiguous')
        if deck and hold:
            return refuse('unsupported')
        zone = 'deck' if deck else 'hold'
        nums = re.findall(r'(?<![\w.])(-?\d+)\s*(?:occupied\s+)?(?:tiers?|high)\b', t)
        if not nums:
            match = hit(r'(?:max(?:imum)?|cap|ceiling|height|at most|not exceed|no more than|limit)\s*(?:of|to|is|at|:)?\s*(-?\d+)\b', t)
            nums = [match[1]] if match else []
        if len(set(nums)) != 1:
            return refuse('ambiguous' if not nums else 'unsupported')
        tiers = int(nums[0])
        bay = next(iter(unique_bays), None)
        cap = context.get('max_tiers_by_bay', {}).get(str(bay), {}).get(zone, context.get('max_tiers', {}).get(zone, -1))
        if not 0 <= tiers <= min(cap, 12):
            return refuse('unsupported')
        if not hit(r'\b(?:cap|limit|max(?:imum)?|at most|no more than|not exceed|ceiling|must|shall|keep|restrict|only|stop|set|height)\b', t):
            return refuse('not_an_instruction')
        return envelope({'type': 'max_tiers', 'bay': bay, 'zone': zone, 'max_tiers': tiers})
    if is_dg:
        if hit(r'\b(?:at most|no more than|maximum|no farther than|no further than)\b', t):
            return refuse('unsupported')
        classes = _class(t)
        if len(classes) != 1 or classes[0] not in CLASSES:
            return refuse('unsupported')
        allowed = context.get('allowed_imdg_classes', context.get('imdg_classes'))
        if allowed is not None and classes[0] not in allowed:
            return refuse('unsupported')
        distances = re.findall(r'(?<![\w.])(\d+|a|an|single)\s*(?:20\s*(?:ft|foot)\s*)?bays?\b', t)
        if len(set(distances)) != 1:
            return refuse('ambiguous' if not distances else 'unsupported')
        distance = NUMBERS.get(distances[0], int(distances[0]) if distances[0].isdigit() else 0)
        if not 1 <= distance <= 20:
            return refuse('unsupported')
        accommodation = bool(hit(r'\b(?:accommodation|living quarters|crew quarters|superstructure|deckhouse)\b', t))
        if accommodation and unique_bays:
            return refuse('unsupported')
        if accommodation:
            if not context.get('has_accommodation'):
                return refuse('unsupported')
            away = {'kind': 'accommodation'}
        elif unique_bays:
            away = {'kind': 'bay', 'bay': next(iter(unique_bays))}
        else:
            return refuse('ambiguous')
        return envelope({'type': 'dg_keep_away', 'imdg_class': classes[0], 'min_bays': distance, 'away_from': away})
    if is_hatch:
        if not context.get('hatch_covers'):
            return refuse('unsupported')
        if hit(r'\b(?:deck only|hold only|only (?:on|below|above)|on deck alone|below deck alone|half|port side only|starboard only|new(?:ly)? (?:cargo|loaded) only)\b', t):
            return refuse('unsupported')
        if unique_bays or hit(r'\b(?:sgsin|mypkg|thlch|assign|reserve|designate)\b', t):
            return refuse('unsupported')
        deck_text = re.sub(r'\b(?:below|under) (?:the )?deck\b', ' ', t)
        deck_scope = bool(hit(r'\b(?:deck|above)\b', deck_text))
        hold_scope = bool(hit(r'\b(?:hold|below|under)\b', t))
        if deck_scope != hold_scope or hit(r'\b(?:hatch|cover)\s*(?:number\s*)?\d+\b', t):
            return refuse('unsupported')
        if hit(r'\b(?:1|one|single|same)\s*(?:discharge\s+)?(?:port|pod|destination)\b|\b(?:no|never|avoid|do not)\s+mix', t):
            return envelope({'type': 'one_port_per_hatch'})
        return refuse('ambiguous')
    if is_close:
        if not unique_bays:
            return refuse('ambiguous')
        return envelope({'type': 'close_bay', 'bay': next(iter(unique_bays))})
    # A recognizable but incompletely expressed supported task needs clarification.
    if hit(r'\b(?:keep away|close bay|cap|limit tiers|one port per)\b', t):
        return refuse('ambiguous')
    if hit(r'\b(?:load|reserve|allocate|move|stow|put|route|ensure|must|shall|forbid|require|assign|segregat\w*|restrict)\b', t):
        return refuse('unsupported')
    return refuse('not_an_instruction')


parse_sentence = parse
