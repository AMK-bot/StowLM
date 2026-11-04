"""Read RCSPPSuite vessels, instances and result files (formats in the suite's RCSPPSuite.pdf).

The files are read-only and git-ignored (D197). This module only parses: it decides nothing about feasibility.
Run scripts here with `.venv/bin/python -I`, passing data paths as arguments.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import xml.etree.ElementTree as ET

HEIGHT = {'20DC': 2.591, '40DC': 2.591, '40HC': 2.896}
RHO = 1.025
THETA, ETA = .05, 10.
ALPHA, BETA, GAMMA = .22, 1.12, .6
TRIM_LOW, TRIM_HIGH = -2., 2.


@dataclass
class Stack:
    index: int
    bay: int
    row: int
    level: str          # 'OnDeck'; hold stacks carry a different label from the documents' 'InHold'
    tp: float
    vp: float
    start: int
    end: int
    max_weight: float
    max_height: float
    plugs: frozenset
    block: int
    hatch: int | None

    @property
    def tiers(self):
        return range(self.start, self.end+1, 2)


@dataclass
class Tank:
    name: str
    volume: float
    vp_empty: float
    vp_full: float

    @property
    def capacity(self):
        return self.volume*RHO


@dataclass
class Bay:
    name: int
    aft: float
    fore: float
    light_weight: float
    light_vp: float
    tanks: list
    bonjean: list
    sagging: float | None
    hogging: float | None
    stacks: list

    @property
    def mid(self):
        return (self.fore+self.aft)/2


@dataclass
class Vessel:
    imo: str
    gm_limit: float
    max_displacement: float
    lash_limit: float
    hydrostatics: list      # (displacement, lcb, kmt, trim factor)
    bays: list              # fore to aft, as the file numbers them
    by_key: dict = field(default_factory=dict)      # (bay, row, level) -> Stack

    def stack_at(self, bay, row, tier):
        """A tier belongs to one level of a bay's row, so this finds the stack a position names."""
        for level in self.levels:
            stack = self.by_key.get((bay, row, level))
            if stack and stack.start <= tier <= stack.end:
                return stack
        return None

    @property
    def levels(self):
        return {key[2] for key in self.by_key}


@dataclass
class Box:
    id: int
    type: str
    weight: float
    pol: int
    pod: int
    reefer: bool
    imdg: bool
    imdg_type: int | None
    points: int
    position: tuple | None = None       # arrival list only: (bay, row, tier, side)

    @property
    def twenty(self):
        return self.type == '20DC'

    @property
    def height(self):
        return HEIGHT[self.type]


@dataclass
class Instance:
    name: str
    imo: str
    max_mixed_bays: int
    load: dict
    arrival: dict
    ports: list         # (code, max long crane) in voyage order
    tanks0: dict        # tank name -> percentage filled at departure from port 0


@dataclass
class Result:
    name: str
    positions: dict     # box id -> {port: (bay, row, tier, side)}
    tanks: dict         # port -> {tank name: percentage}
    ballast: float
    points: float
    restows: float
    objective: float


def _floats(text):
    return [float(x) for x in text.split(',')]


def read_vessel(path):
    root = ET.parse(path).getroot()
    head = root.find('VesselData').attrib
    table = [tuple(_floats(v.text)) for v in root.find('HydrostaticsTable')]
    bays, keyed = [], {}
    for bay in root.find('Bays'):
        name = int(bay.get('Name'))
        info, light, limit = bay.find('BayInfo').attrib, bay.find('LightShipMass').attrib, bay.find('BendingMomentLimit')
        tanks = [Tank(t.get('Name'), float(t.get('MaxVolume')), float(t.get('VpEmpty')), float(t.get('VpFull')))
                 for t in bay.findall('Tanks/Tank')]
        bonjean = [tuple(_floats(v.text)) for v in bay.findall('BonjeanData/Values')]
        stacks = []
        for s in bay.findall('Stacks/Stack'):
            plugs = frozenset(int(x) for x in re.split('[;,]', s.get('ReeferPositions') or '') if x)
            hatch = s.get('HatchCoverId')
            stack = Stack(int(s.get('Index')), name, int(s.get('Row')), s.get('Level'), float(s.get('Tp')), float(s.get('VpMin')),
                          int(s.get('StartTier')), int(s.get('EndTier')), float(s.get('MaxWeight')), float(s.get('MaxHeight')), plugs,
                          int(s.get('BlockId')), int(hatch) if hatch else None)
            stacks.append(stack)
            keyed[name, stack.row, stack.level] = stack
        bays.append(Bay(name, float(info['LpMin']), float(info['LpMax']), float(light['Weight']), float(light['VP']), tanks, bonjean,
                        float(limit.get('SaggingLimit')) if limit is not None else None,
                        float(limit.get('HoggingLimit')) if limit is not None else None, stacks))
    return Vessel(head['IMO'], float(head['GMLimit']), float(head['MaxDispl']), float(head['LashLim']), table, bays, keyed)


def _position(element):
    return int(element.get('Bay')), int(element.get('Row')), int(element.get('Tier')), element.get('Side')


def _box(c):
    position = c.find('Position')
    imdg_type = c.findtext('ImdgType')
    return Box(int(c.findtext('ID')), c.findtext('Type'), float(c.findtext('Weight')), int(float(c.findtext('POL'))),
               int(float(c.findtext('POD'))), c.findtext('Reefer') == '1', c.findtext('Imdg') == '1',
               int(imdg_type) if imdg_type not in (None, '') else None, int(c.findtext('Points') or 0),
               _position(position) if position is not None else None)


def read_instance(path):
    path = Path(path)
    root = ET.parse(path).getroot()
    load = {b.id: b for b in map(_box, root.find('LoadList'))}
    arrival = {b.id: b for b in map(_box, root.find('ACList'))}
    ports = [(int(p.get('Code')), int(p.get('MaxLC'))) for p in root.find('Voyage')]
    tanks0 = {t.get('Name'): float(t.get('PercentageFilled')) for t in root.findall('TankStates/TankState/Tank')}
    return Instance(path.stem, root.findtext('IMO'), int(root.findtext('MaxMixedBays')), load, arrival, ports, tanks0)


def read_result(path):
    """Result files hold several top-level elements, so they are wrapped before parsing."""
    text = re.sub(r'<\?xml[^>]*\?>', '', Path(path).read_text(encoding='utf-8-sig'))
    root = ET.fromstring(f'<R>{text}</R>')
    positions = {int(c.get('ID')): {int(p.get('Port')): _position(p) for p in c.findall('Positions/Position')}
                 for c in root.find('PositionedContainers')}
    tanks = {int(p.get('Name')): {t.get('Name'): float(t.get('PercentageFilled')) for t in p.findall('Tanks/Tank')} for p in root.find('TankStatus')}
    goal = root.find('Objectives')
    return Result(root.findtext('InstanceName'), positions, tanks, *(float(goal.findtext(k)) for k in ('BallastWater', 'ContainerPoints', 'Restows', 'Objective')))


def without_idle_ports(instance):
    """Published plans number ports after dropping any where nothing is loaded or discharged (s_14 port 3, s_02 port 13, s_06 port 3), so renumber to match."""
    boxes = [*instance.load.values(), *instance.arrival.values()]
    used = {b.pol for b in boxes} | {b.pod for b in boxes} | {instance.ports[0][0]}
    kept = [(code, limit) for code, limit in instance.ports if code in used]
    new = {code: i for i, (code, _) in enumerate(kept)}
    if len(kept) == len(instance.ports):
        return instance
    for box in boxes:
        box.pol, box.pod = new[box.pol], new[box.pod]
    instance.ports = [(new[code], limit) for code, limit in kept]
    return instance


def vessel_path(root, instance_name):
    return Path(root)/'Vessels'/f"vessel_{instance_name.split('_')[0]}.xml"
