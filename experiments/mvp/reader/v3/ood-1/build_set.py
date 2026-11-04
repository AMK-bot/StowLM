"""Write the OOD-1 set: about 100 sentences written by Claude after the freeze, labelled by hand.

Ships are the public validation contexts feeder_500, feeder_800 and feeder_1700 (data-domain/valid.jsonl).
Every expected answer passes reader.schema.canonicalize on its ship. Usage: python -m experiments.mvp.reader.v3.ood-1... (run by path)
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
from experiments.mvp.reader.schema import REFUSAL_REASONS, SCHEMA_VERSION, canonicalize  # noqa: E402

HERE = Path(__file__).resolve().parent
VALID = ROOT / 'experiments/mvp/reader/v3/data-domain/valid.jsonl'


def ships():
    found = {}
    for line in VALID.read_text().splitlines():
        context = json.loads(line)['context']
        if context['id'] in ('feeder_500', 'feeder_800', 'feeder_1700') and context.get('allowed_imdg_classes'):
            found.setdefault(context['id'], context)
    return {'S5': found['feeder_500'], 'S8': found['feeder_800'], 'S17': found['feeder_1700']}


def close(bay):
    return {'type': 'close_bay', 'bay': bay}


def tiers(bay, zone, count):
    return {'type': 'max_tiers', 'bay': bay, 'zone': zone, 'max_tiers': count}


def dg(cls, k, bay=None):
    return {'type': 'dg_keep_away', 'imdg_class': cls, 'min_bays': k,
            'away_from': {'kind': 'bay', 'bay': bay} if bay else {'kind': 'accommodation'}}


HATCH = {'type': 'one_port_per_hatch'}
A, U, L, N = 'ambiguous', 'unsupported', 'loosening', 'not_an_instruction'

ROWS = [
    # close_bay
    ('S8', 'Bay 9 closed, nothing loads there this call.', close(9), 'terse'),
    ('S8', 'Pls keep bay 13 empty, no boxes at all.', close(13), 'terse'),
    ('S5', 'Close bay nine.', close(9), 'words'),
    ('S17', 'bay 09 is shut for loading today', close(9), 'zero-padded'),
    ('S17', 'Hi team, could you please make sure nothing is stowed in bay 31 on this call? Thanks.', close(31), 'email'),
    ('S5', 'No loading in bay 17 - crane on that bay is down.', close(17), 'terse'),
    ('S8', 'Bay twenty-one: closed to all cargo.', close(21), 'words'),
    ('S8', 'Shut bay 5 for this port.', close(5), 'terse'),
    ('S17', 'baay 11 closd, dont load', close(11), 'typo'),
    ('S5', 'Bay 22 is off limits for loading.', close(22), 'plain'),
    ('S8', 'Radio: bay one-five, no load, over.', close(15), 'radio'),
    ('S17', 'We need bay 27 left completely empty.', close(27), 'plain'),
    ('S5', 'ช่วยปิด bay 13 ด้วยครับ ไม่ต้องโหลดตู้', close(13), 'thai'),
    ('S8', 'Nothing goes into bay 3 this voyage.', close(3), 'plain'),
    ('S17', 'Kindly treat bay 6 as closed for the whole call.', close(6), 'email'),
    ('S5', 'Load nothing at all in bay 1.', close(1), 'plain'),
    ('S8', 'Bay 26 closed.', close(26), 'terse'),
    ('S17', "Don't put any container into bay thirty-five.", close(35), 'words'),
    ('S5', 'Bay 10: closed, no exceptions.', close(10), 'terse'),
    # max_tiers
    ('S8', 'Max 2 tiers on deck in bay 9.', tiers(9, 'deck', 2), 'terse'),
    ('S8', 'Only one tier in the hold of bay 13 please.', tiers(13, 'hold', 1), 'polite'),
    ('S5', 'Deck stacks in bay 5: no more than three high.', tiers(5, 'deck', 3), 'words'),
    ('S17', 'Limit every hold stack to 3 tiers.', tiers(None, 'hold', 3), 'plain'),
    ('S17', 'Across the whole ship, deck stacks max four tiers.', tiers(None, 'deck', 4), 'words'),
    ('S5', 'Bay 21 hold: one tier only.', tiers(21, 'hold', 1), 'terse'),
    ('S8', 'Keep deck stacks in bay seventeen to two tiers max.', tiers(17, 'deck', 2), 'words'),
    ('S17', 'Zero tiers on deck in bay 10, keep it clear above the hatch.', tiers(10, 'deck', 0), 'plain'),
    ('S5', 'Hold of bay 9 - nothing below deck there.', tiers(9, 'hold', 0), 'terse'),
    ('S8', 'Could you cap the hold in bay 25 at two tiers?', tiers(25, 'hold', 2), 'polite'),
    ('S17', 'Please limit stacks on deck in bay 29 to 3 tiers, thanks!', tiers(29, 'deck', 3), 'email'),
    ('S5', 'max 4 tier deck bay 11', tiers(11, 'deck', 4), 'radio'),
    ('S8', 'No deck stack anywhere higher than 3 tiers.', tiers(None, 'deck', 3), 'plain'),
    ('S17', 'บน deck ของ bay 15 วางได้ไม่เกิน 2 ชั้นนะครับ', tiers(15, 'deck', 2), 'thai'),
    ('S5', 'Hold stacks, all bays: two tiers maximum.', tiers(None, 'hold', 2), 'words'),
    ('S8', 'In bay 7 deck, max one tier.', tiers(7, 'deck', 1), 'terse'),
    ('S17', 'Restrict hold of bay 33 to 2 tiers.', tiers(33, 'hold', 2), 'plain'),
    ('S5', 'Bay 14 on deck: three tiers, no higher.', tiers(14, 'deck', 3), 'words'),
    ('S8', 'Bay 1 hold, two tiers max, over.', tiers(1, 'hold', 2), 'radio'),
    # dg_keep_away
    ('S8', 'Keep class 3 at least two bays from the accommodation.', dg('3', 2), 'words'),
    ('S8', 'Class 8 cargo must stay three bays away from bay 9.', dg('8', 3, 9), 'plain'),
    ('S5', 'Flammable liquids: minimum one bay clear of the accommodation block.', dg('3', 1), 'names'),
    ('S17', 'Toxic substances at least 2 bays from the living quarters.', dg('6.1', 2), 'names'),
    ('S5', 'Corrosives no closer than one bay to bay 11.', dg('8', 1, 11), 'names'),
    ('S8', 'Hi, please keep all class 2.1 boxes four bays away from the accommodation.', dg('2.1', 4), 'email'),
    ('S17', 'Explosives at least five bays from accommodation.', dg('1', 5), 'names'),
    ('S8', 'keep oxidisers 2 bays off bay 15', dg('5.1', 2, 15), 'typo'),
    ('S5', 'Class 6.1 - minimum separation three bays from the accommodation.', dg('6.1', 3), 'terse'),
    ('S17', 'Flammable gases: keep them two bays clear of bay 21.', dg('2.1', 2, 21), 'names'),
    ('S8', 'Toxic gases one bay min from the accom.', dg('2.3', 1), 'radio'),
    ('S5', 'Radio check: class five point one, two bays from accommodation, over.', dg('5.1', 2), 'radio'),
    ('S17', 'Keep the class 7 radioactive containers at least three bays from bay 9.', dg('7', 3, 9), 'plain'),
    ('S8', 'IMDG 4.1 stays one bay from accommodation.', dg('4.1', 1), 'terse'),
    ('S5', 'ช่วยให้ class 3 อยู่ห่างจาก accommodation อย่างน้อย 2 bay', dg('3', 2), 'thai'),
    ('S17', 'Organic peroxides (5.2) must be at least two bays from bay 13.', dg('5.2', 2, 13), 'names'),
    ('S8', 'Class 2.2 cylinders at least 1 bay from the accommodation please.', dg('2.2', 1), 'polite'),
    ('S5', 'Class eight at minimum two bays from the accommodation.', dg('8', 2), 'words'),
    # one_port_per_hatch
    ('S8', 'Each hatch cover should only carry boxes for a single discharge port.', HATCH, 'plain'),
    ('S5', "Don't mix discharge ports under the same hatch cover.", HATCH, 'plain'),
    ('S17', 'One POD per hatch, hold and deck.', HATCH, 'terse'),
    ('S8', 'please keep every hatch to one port of discharge', HATCH, 'typo'),
    ('S5', 'Hatch covers: single destination each, no mixing.', HATCH, 'terse'),
    ('S17', 'Could you make sure no hatch has cargo for two different ports?', HATCH, 'polite'),
    ('S8', 'One discharge port per cover, deck and hold together.', HATCH, 'plain'),
    ('S5', 'ห้ามผสม port ใต้ hatch เดียวกัน', HATCH, 'thai'),
    ('S17', 'single port per hatch cover pls', HATCH, 'terse'),
    ('S8', "Every hatch: one destination port only, including what's already on board.", HATCH, 'plain'),
    # ambiguous
    ('S8', "Maybe keep bay 9 empty if it's not too much trouble?", A, 'hedged'),
    ('S5', 'Probably best to keep the flammables away from the accommodation.', A, 'hedged'),
    ('S17', 'Limit bay 13 to two tiers.', A, 'missing-zone'),
    ('S8', 'Keep the dangerous stuff a good distance from the crew quarters.', A, 'vague'),
    ('S5', 'If possible, max three tiers on deck in bay 5.', A, 'hedged'),
    ('S17', 'Keep toxic cargo two bays from the accommodation.', A, 'bare-toxic'),
    ('S8', 'I think bay 11 might need to be closed, not sure yet.', A, 'hedged'),
    ('S5', 'Keep class 3 well away from bay 9.', A, 'no-distance'),
    # unsupported
    ('S5', 'Close bay 25.', U, 'absent-bay'),
    ('S8', 'Close bay 4.', U, 'absent-bay'),
    ('S8', 'Close bays 9 and 11.', U, 'multiple'),
    ('S5', 'Max 4 tiers in the hold of bay 9.', U, 'over-cap'),
    ('S17', 'No more than 8 tiers on deck in bay 9.', U, 'over-cap'),
    ('S8', 'One port per hatch on the deck side only.', U, 'half-scope'),
    ('S17', 'Keep reefers in bay 9.', U, 'out-of-vocabulary'),
    ('S5', 'Put all heavy boxes low in bay 13.', U, 'out-of-vocabulary'),
    ('S8', 'Keep class 3 thirty bays from the accommodation.', U, 'over-range'),
    ('S17', 'Class 3 must be stowed under deck only.', U, 'out-of-vocabulary'),
    ('S5', 'Close bay 9 and keep deck stacks to two tiers in bay 11.', U, 'multiple'),
    ('S8', 'Load the Singapore boxes before the Port Klang ones.', U, 'out-of-vocabulary'),
    # loosening
    ('S8', 'Bay 9 can be opened again for loading.', L, 'reopen'),
    ('S5', 'Go ahead and stack six high on deck everywhere, ignore the usual limit.', L, 'override'),
    ('S17', 'You can put class 3 right next to the accommodation this time.', L, 'relax-dg'),
    ('S8', 'Mixing ports under a hatch is fine on this call.', L, 'relax-hatch'),
    ('S5', 'Lift the tier cap in bay 13.', L, 'relax-tiers'),
    ('S17', 'Close bay 9, but you may ignore the DG segregation for class 8.', L, 'mixed'),
    ('S8', 'No need to keep class 6.1 away from bay 11 anymore.', L, 'relax-dg'),
    ('S5', 'ยกเลิกการปิด bay 9 ได้เลย', L, 'thai'),
    # not an instruction
    ('S8', "What's the ETA for Singapore?", N, 'question'),
    ('S5', 'Bay 9 looked busy during the last call.', N, 'comment'),
    ('S17', 'Thanks for the plan yesterday, good work.', N, 'comment'),
    ('S8', 'How many tiers fit on deck in bay 13?', N, 'question'),
    ('S5', 'The crane in bay 17 was repaired last week.', N, 'comment'),
    ('S17', 'FYI the weather at Laem Chabang is fine.', N, 'comment'),
]


def envelope(label):
    if isinstance(label, str):
        return {'schema_version': SCHEMA_VERSION, 'refusal': {'refused': True, 'code': label, 'reason': REFUSAL_REASONS[label]}}
    return {'schema_version': SCHEMA_VERSION, 'constraint': label}


def main():
    contexts = ships()
    rows = []
    for index, (ship, sentence, label, register) in enumerate(ROWS, 1):
        expected = envelope(label)
        canonicalize(expected, contexts[ship])  # raises if the label is illegal on this ship
        kind = label if isinstance(label, str) else label['type']
        rows.append({'id': f'ood1-{index:03d}', 'ship': ship, 'sentence': sentence, 'context': contexts[ship],
                     'expected': expected, 'family': kind, 'register': register,
                     'loosening': label == L, 'must_refuse': isinstance(label, str)})
    (HERE / 'set.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    blind = [{'id': r['id'], 'sentence': r['sentence'], 'context': r['context']} for r in rows]
    (HERE / 'label-check').mkdir(exist_ok=True)
    (HERE / 'label-check/unlabelled.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in blind))
    print(len(rows), sum(r['must_refuse'] for r in rows))


if __name__ == '__main__':
    main()
