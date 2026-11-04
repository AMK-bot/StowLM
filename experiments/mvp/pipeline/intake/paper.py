"""Named parser baseline and conservative grounding of model paper readings."""
from decimal import Decimal
import hashlib
import json
import re

from .provenance import is_image_document

_ID=re.compile(r'\b[A-Z]{4}\d{7}\b')
_COMMAND=re.compile(r'ignore\s+(?:your|the|all|previous)|do not ask|set\s+[a-z_]+\s*=|ไม่ต้องถาม|ละเลยคำสั่ง',re.I)
_MASS={'gross_mass_kg':r'(?:VGM|GROSS(?:\s+MASS|\s+WEIGHT)?)','tare_kg':r'TARE','net_mass_kg':r'NET(?:\s+WEIGHT)?'}
_MASS_LABELS = (
    ('max_gross_mass_kg', r'MAX(?:IMUM)?\s+(?:OPERATING\s+)?GROSS(?:\s+MASS|\s+WEIGHT)?|CSC\s+(?:RATING|MAX(?:IMUM)?(?:\s+GROSS)?)'),
    ('gross_mass_kg', r'VGM|VERIFIED\s+GROSS(?:\s+MASS|\s+WEIGHT)?|GROSS(?:\s+MASS|\s+WEIGHT)?|น้ำหนักรวม(?:ที่รับรอง)?'),
    ('tare_kg', r'TARE(?:\s+MASS|\s+WEIGHT)?|น้ำหนักตู้เปล่า'),
    ('net_mass_kg', r'NET(?:\s+MASS|\s+WEIGHT)?|น้ำหนักสุทธิ'),
)
_PRINTED_NUMBER = r'[+-]?\d+(?:\.\d+)?'
_PRINTED_UNIT = r'kg|kgs|lb|lbs|MT|tonnes?'


def _mass_binding(observation):
    """Reject borrowing a neighboring mass or rating as the requested fact.

    Unknown wording remains a model interpretation requiring corroboration;
    recognized labels must agree with their own raw printed number and unit.
    """
    field = observation['field']
    if field not in (*_MASS, 'max_gross_mass_kg'):
        return None
    quote = observation.get('quote', '')
    mentions, occupied = [], []
    for declared_field, label in _MASS_LABELS:
        pattern = rf'(?<![A-Za-z])(?:{label})\s*(?:[:=]\s*)?({_PRINTED_NUMBER})\s*({_PRINTED_UNIT})(?![A-Za-z])'
        for match in re.finditer(pattern, quote, re.I):
            if any(match.start() < end and match.end() > start for start, end in occupied):
                continue
            occupied.append(match.span())
            unit = {'kgs':'kg', 'lbs':'lb', 'mt':'t', 'tonne':'t', 'tonnes':'t'}.get(match[2].lower(), match[2].lower())
            mentions.append((declared_field, Decimal(match[1]), unit))
    value = Decimal(str(observation['value']))
    requested = [(number, unit) for name, number, unit in mentions if name == field]
    if requested:
        # A quote with contradictory declarations cannot endorse either one.
        return len(set(requested)) == 1 and requested[0] == (value, source_unit(observation))
    if any(number == value for _, number, _ in mentions):
        return False
    return None


def fingerprint(document):
    if is_image_document(document):
        return document.get('sha256') or str(document.get('path',document['id']))
    return hashlib.sha256(str(document.get('text','')).encode()).hexdigest()


def baseline_paper(document):
    """Fixed English freight-label grammar; no OCR, Thai semantics or aliases."""
    if is_image_document(document):
        return []
    out=[]
    text=str(document.get('text',''))
    for line in text.splitlines():
        ids=_ID.findall(line)
        if len(set(ids))!=1 or _COMMAND.search(line):
            continue
        rid=ids[0]
        values={'id':rid}
        equipment_extras=[]
        for field,label,pattern in [('iso_type',r'ISO(?:\s+CODE)?',r'[A-Z0-9]{4}'),('pol','POL',r'[A-Z]{5}'),('pod','POD',r'[A-Z]{5}')]:
            match=re.search(rf'\b{label}\s*(?:[:=]\s*)?({pattern})\b',line,re.I)
            if match:
                values[field]=match[1].upper()
            if field=='iso_type':
                equipment_extras.extend((field,m.upper()) for m in re.findall(rf'\b{label}\s*(?:[:=]\s*)?({pattern})\b',line,re.I)[1:])
        for field,label,unit in [('length_ft','LENGTH','ft'),('height_m','HEIGHT','m')]:
            match=re.search(rf'\b{label}\s*[:=]?\s*({_PRINTED_NUMBER})\s*{unit}\b',line,re.I)
            if match:
                values[field]=float(match[1])
                equipment_extras.extend((field,float(m)) for m in re.findall(rf'\b{label}\s*[:=]?\s*({_PRINTED_NUMBER})\s*{unit}\b',line,re.I)[1:])
        for field,label in _MASS.items():
            if field=='gross_mass_kg' and re.search(r'cargo\s+gross|package\s+gross',line,re.I):
                continue
            match=re.search(rf'\b{label}\s*(?:[:=]\s*)?([0-9]+(?:\.[0-9]+)?)\s*(kg|lb|lbs|MT|tonnes?)\b',line,re.I)
            if match:
                raw={'field':field,'value':float(match[1]),'quote':line}
                if _mass_binding(raw) is not False:
                    values[field]=float(Decimal(match[1])*{'kg':Decimal(1),'lb':Decimal('0.45359237'),'lbs':Decimal('0.45359237'),'mt':Decimal(1000),'tonne':Decimal(1000),'tonnes':Decimal(1000)}[match[2].lower()])
        for field,label in [('empty','EMPTY'),('reefer','REEFER'),('tank','TANK'),('equipment_reefer','REEFER EQUIPMENT')]:
            match=re.search(rf'\b{label}\s*(?:[:=]\s*)?(YES|NO|TRUE|FALSE)\b',line,re.I)
            if match:
                values[field]=match[1].upper() in ('YES','TRUE')
                equipment_extras.extend((field,m.upper() in ('YES','TRUE')) for m in re.findall(rf'\b{label}\s*(?:[:=]\s*)?(YES|NO|TRUE|FALSE)\b',line,re.I)[1:])
        if re.search(r'\b(?:NON-DG|DG\s*(?:[:=]\s*)?(?:NO|NONE))\b',line,re.I):
            values['dg']=None
        if re.search(r'\bIN GAUGE\b',line,re.I):
            values['oog']=None
        if re.search(r'\bLADEN\b',line,re.I):
            values['empty']=False
        vessel=re.search(r'\bVessel\s*(?:[:=]\s*)?([^;|]+)',line,re.I)
        if vessel:
            values['booked_vessel']=vessel[1].strip().upper()
        status=re.search(r'\bStatus\s*(?:[:=]\s*)?(CONFIRMED|BOOKED|ROLLED|SHUT_OUT)\b',line,re.I)
        if status:
            values['booking_status']='booked' if status[1].upper() in ('CONFIRMED','BOOKED') else status[1].lower()
        for field,value in [*values.items(),*equipment_extras]:
            item={'row_id':rid,'field':field,'value':value,'document':document['id'],'quote':line,'source_kind':'parser_text'}
            out.append(item)
    return out


def source_unit(observation):
    field=observation['field']
    if field not in ('gross_mass_kg','tare_kg','max_gross_mass_kg','net_mass_kg','reefer_setpoint_c'):
        return None
    units=set()
    for number,unit in re.findall(r'([+-]?\d+(?:\.\d+)?)\s*(kg|kgs|lb|lbs|MT|tonnes?|°?C|°?F)(?![A-Za-z])',observation.get('quote',''),re.I):
        if Decimal(number)==Decimal(str(observation['value'])):
            units.add({'kgs':'kg','lbs':'lb','tonne':'t','tonnes':'t','mt':'t','°c':'c','°f':'f'}.get(unit.lower(),unit.lower()))
    return next(iter(units)) if len(units)==1 else None


def grounded(observation, document):
    """Require actual source words plus a literal value; semantics need consensus."""
    quote=observation.get('quote','')
    rid=observation.get('row_id','')
    if not isinstance(quote,str) or not quote.strip() or _COMMAND.search(quote):
        return False
    image=is_image_document(document)
    if not image and quote not in str(document.get('text','')):
        return False
    field,value=observation['field'],observation['value']
    quote_ids=set(_ID.findall(quote))
    if quote_ids - {rid}:
        return False
    if field not in ('booked_vessel','booking_status'):
        if quote_ids != {rid}:
            return False
    elif quote_ids != {rid}:
        # Only actual document-wide booking headers may use a separate row
        # anchor. It cannot lend another container's cargo facts to this row.
        row_quote=observation.get('row_quote')
        if not isinstance(row_quote,str) or set(_ID.findall(row_quote)) != {rid}:
            return False
        if not image and row_quote not in str(document.get('text','')):
            return False
    leaf=field.rsplit('.',1)[-1]
    if field in ('length_ft','height_m'):
        label,unit=('LENGTH','ft') if field=='length_ft' else ('HEIGHT','m')
        # A number elsewhere in a quote cannot supply equipment geometry.
        declarations=re.findall(rf'\b{label}\s*[:=]?\s*({_PRINTED_NUMBER})\s*{unit}\b',quote,re.I)
        return (isinstance(value,(int,float)) and not isinstance(value,bool)
                and bool(declarations) and {Decimal(v) for v in declarations}=={Decimal(str(value))}
                and str(observation.get('unit',unit)).lower()==unit)
    if field=='special_stowage':
        return isinstance(value,str) and bool(value.strip()) and value in quote or isinstance(value,list) and bool(value) and all(isinstance(v,str) and v.strip() and v in quote for v in value)
    if leaf in ('un_number','imdg_class'):
        wanted=str(value).strip()
        if isinstance(value,(int,float)) and not isinstance(value,bool):
            wanted=format(value,'g')
        label='UN' if leaf=='un_number' else 'CLASS'
        declared=re.findall(rf'\b{label}\s*(?:[:=]\s*)?([0-9.]+)',quote,re.I)
        return any(v==wanted or leaf=='un_number' and len(v)==4 and v.isdigit() and wanted.isdigit() and int(v)==int(wanted) for v in declared)
    if value is None:
        terms={'dg':r'\bDG\s*(?:[:=]\s*)?(?:NO|NONE)\b|non[- ]?dangerous goods|non[- ]?dg|no dangerous|ไม่เป็นสินค้าอันตราย|ไม่ใช่สินค้าอันตราย|ไม่มีสินค้าอันตราย',
               'oog':r'in[- ]gauge|no oog|ขนาดปกติ', 'packing_group':r'PG\s*[:=]?\s*(?:NONE|N/A|—|-)'}
        return bool(re.search(terms.get(leaf,r'\b(?:null|none|n/a)\b'),quote,re.I))
    if isinstance(value,bool):
        if field=='oog' and value is False:
            return bool(re.search(r'\bIN GAUGE\b|ขนาดปกติ',quote,re.I))
        if field=='dg':
            if value is False and re.search(r'non[- ]?dangerous goods|non[- ]?dg|ไม่เป็นสินค้าอันตราย|ไม่มีสินค้าอันตราย',quote,re.I):
                return True
            m=re.search(r'\bDG\s*(?:[:=]\s*)?(YES|NO|TRUE|FALSE)\b',quote,re.I)
            return bool(m) and (m[1].upper() in ('YES','TRUE'))==value

        # A recognized label elsewhere in the same row cannot endorse the
        # opposite value for this field.
        labels={'empty':r'EMPTY','reefer':r'REEFER(?!\s+EQUIPMENT)','tank':r'TANK','equipment_reefer':r'REEFER EQUIPMENT','limited_quantity':r'LQ|LIMITED QUANTITY',
                'marine_pollutant':r'MP|MARINE POLLUTANT','ctu_closed':r'CTU CLOSED'}
        label=labels.get(leaf)
        if leaf in ('limited_quantity','marine_pollutant'):
            # A row-wide quote spanning several UN entries cannot lend the
            # first entry's shipment flags to another entry. A narrower quote
            # containing its container id and one entry remains readable.
            declarations={m.upper() in ('YES','TRUE','Y') for m in re.findall(
                rf'(?<![A-Za-z])(?:{label})\s*(?:[:=]\s*)?(YES|NO|TRUE|FALSE|Y|N)\b',quote,re.I)}
            positive,negative = {
                'marine_pollutant': (r'เป็นมลพิษทางทะเล', r'ไม่เป็นมลพิษทางทะเล'),
                'limited_quantity': (r'ปริมาณจำกัด', r'ไม่ใช่ปริมาณจำกัด'),
            }[leaf]
            if re.search(negative,quote):
                declarations.add(False)
            if re.search(positive,re.sub(negative,'',quote)):
                declarations.add(True)
            if declarations:
                return declarations == {value}
        if label:
            declarations={m.upper() in ('YES','TRUE','Y') for m in re.findall(rf'(?:{label})\s*(?:[:=]\s*)?(YES|NO|TRUE|FALSE|Y|N)\b',quote,re.I)}
            if declarations:
                return declarations=={value}
        vocab = {
            'empty': (r'\bEMPTY\b|ตู้เปล่า', r'\b(?:LADEN|LOADED)\b|ตู้หนัก'),
            'reefer': (r'\bOPERATING\b|เดินเครื่อง', r'NON[- ]OPERATING|ไม่เดินเครื่อง'),
            'ctu_closed': (r'closed freight|closed container|ตู้ปิด', r'open container|ตู้เปิด'),
            'marine_pollutant': (r'เป็นมลพิษทางทะเล', r'ไม่เป็นมลพิษทางทะเล'),
            'limited_quantity': (r'ปริมาณจำกัด', r'ไม่ใช่ปริมาณจำกัด')}
        positive,negative=vocab.get(leaf,(r'(?!)',r'(?!)'))
        if re.search(negative,quote,re.I):
            return value is False
        return value is True and bool(re.search(positive,quote,re.I))
    if isinstance(value,(int,float)):
        binding = _mass_binding(observation)
        if binding is False:
            return False
        if field=='gross_mass_kg' and re.search(r'cargo\s+gross|package\s+gross',quote,re.I) and not re.search(r'\bVGM\b',quote,re.I):
            return False
        if field in ('gross_mass_kg','tare_kg','max_gross_mass_kg','net_mass_kg','reefer_setpoint_c'):
            actual=source_unit(observation)
            claimed={'lbs':'lb','mt':'t','°c':'c','°f':'f'}.get(str(observation.get('unit','')).lower(),str(observation.get('unit','')).lower())
            if actual is None or claimed and claimed!=actual:
                return False
        if field=='gross_mass_kg' and re.search(r'\bNET\b|น้ำหนักสุทธิ',quote,re.I) and not re.search(r'\bVGM\b|\bGROSS\b|น้ำหนักรวม',quote,re.I):
            return False
        unit=str(observation.get('unit','')).lower()
        for token in re.findall(r'(?<![A-Za-z0-9])[+-]?\d+(?:\.\d+)?',quote):
            number=Decimal(token)
            converted=(number-32)*Decimal(5)/Decimal(9) if unit=='f' else number*{'lb':Decimal('0.45359237'),'lbs':Decimal('0.45359237'),'mt':Decimal(1000),'t':Decimal(1000)}.get(unit,Decimal(1))
            # Paper proposal may preserve source units; conversion is done later.
            if Decimal(str(value))==number or float(converted)==value:
                return True
        return False
    if isinstance(value,str):
        labels={'un_number':r'UN', 'imdg_class':r'CLASS', 'packing_group':r'PG'}
        if leaf in labels:
            declared=re.findall(rf'\b{labels[leaf]}\s*(?:[:=]\s*)?([A-Za-z0-9.]+)',quote,re.I)
            return value.upper() in {v.upper() for v in declared}
        if value.strip().casefold() in quote.casefold():
            return True
        # Canonical source status/packing labels are ordinary stated meanings.
        vocabulary={'booked':r'confirmed|booked|ยืนยันการจอง|ยืนยันแล้ว','confirmed':r'confirmed|booked|ยืนยันการจอง|ยืนยันแล้ว',
                    'shut_out':r'shut.?out|งดรับขึ้นเรือ|ตัดออก|ไม่รับขึ้นเรือ','rolled':r'roll.?over|(?:ย้าย|เลื่อน).*(?:เที่ยวถัดไป)|ถอนออกจากเที่ยวนี้',
                    'packaged_goods_in_closed_freight_containers':r'closed freight|closed container|ตู้ปิด'}
        return value in vocabulary and bool(re.search(vocabulary[value],quote,re.I))
    return False


def normalize(observation):
    field,value=observation['field'],observation['value']
    if field in ('dg','oog') and value is False:
        return None
    if field.rsplit('.',1)[-1] in ('un_number','imdg_class') and isinstance(value,(int,float)) and not isinstance(value,bool):
        value=format(value,'g')
        if field.endswith('un_number'):
            value=value.zfill(4)
    if isinstance(value,str) and field in ('id','iso_type','pol','pod'):
        value=value.strip().upper()
    if field == 'booking_status' and isinstance(value,str):
        value={'confirmed':'booked','accepted':'booked','shut out':'shut_out','rollover':'rolled'}.get(value.strip().lower(),value.strip().lower())
    if field == 'booked_vessel' and isinstance(value,str):
        value=value.strip().upper()
    unit=source_unit(observation) or str(observation.get('unit','')).lower()
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        if unit in ('lb','lbs','mt','t'):
            value=float(Decimal(str(value))*{'lb':Decimal('0.45359237'),'lbs':Decimal('0.45359237'),'mt':Decimal(1000),'t':Decimal(1000)}[unit])
        elif unit=='f' and field=='reefer_setpoint_c':
            value=float((Decimal(str(value))-32)*Decimal(5)/Decimal(9))
    return value
