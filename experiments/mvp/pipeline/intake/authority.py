"""Host policy for authoritative freight documents; never model-made authority."""
import re

from .paper import _ID, fingerprint
from .provenance import is_image_document

ROLES = {
    'id': {'gate_slip'}, 'seal': {'gate_slip'},
    'pod': {'booking'}, 'booked_vessel': {'booking'}, 'booking_status': {'booking'},
    'pol': {'shipping_instructions','booking'}, 'iso_type': {'shipping_instructions','gate_slip'},
    'empty': {'shipping_instructions','gate_slip'}, 'commodity': {'shipping_instructions'},
    'gross_mass_kg': {'vgm'}, 'reefer_setpoint_c': {'shipping_instructions'},
    'reefer': {'reefer_list','shipping_instructions'}, 'oog': {'oog_list'},
    'dg': {'dg_declaration'},
    'tare_kg': {'shipping_instructions','gate_slip','vgm','booking','email'},
    'max_gross_mass_kg': {'shipping_instructions','gate_slip','vgm'},
}
LISTS = {'dg':'dg_declaration','reefer':'reefer_list','oog':'oog_list'}


def field_roles(field):
    if field=='weight_class':
        return {'vgm'}
    if field in ('length_ft','height_m','tank','equipment_reefer'):
        return {'shipping_instructions','gate_slip'}
    if field=='category':
        return {'vgm','dg_declaration','shipping_instructions','reefer_list','oog_list'}
    if field.startswith('dg.'):
        return ROLES['dg']
    return ROLES.get(field,set())


def ordinary(document):
    return not is_image_document(document) and not document.get('handwritten') and document.get('source_format')!='handwriting'


def authoritative(field,document):
    if not ordinary(document):
        return False
    if field in ('tare_kg','max_gross_mass_kg'):
        return document.get('role') in ROLES[field]
    return document.get('role') in field_roles(field)


def prepare(documents,base,grouped,add,issue,excluded=()):
    """Check document presence once and derive scoped, closed-world negatives.

    Membership comes from actual typed source bytes, never model omissions.
    Host role and booking scope are supplied outside model output. A photograph
    cannot establish omissions by itself, even if its metadata says complete.
    """
    by_role={role:[d for d in documents if d.get('role')==role] for role in set().union(*ROLES.values())}
    refs={d.get('booking_ref') for d in by_role.get('booking',[]) if d.get('booking_ref')}
    members={ref:set().union(*(set(_ID.findall(str(d.get('text','')))) for d in by_role.get('booking',[]) if d.get('booking_ref')==ref and ordinary(d))) for ref in refs}
    missing=set()
    for role,field in [('booking','pod'),('vgm','gross_mass_kg'),('dg_declaration','dg'),('gate_slip','id'),('shipping_instructions','iso_type')]:
        if not by_role.get(role):
            missing.add(role)
            issue(None,'document:'+role,'missing_document',f'Supply the {role.replace("_"," ")} for this booking once; its cargo fields remain unresolved.',[role])
    for field,role in LISTS.items():
        for doc in by_role.get(role,[]):
            if doc.get('complete_booking_list') is not True:
                missing.add(role)
                issue(None,'document:'+role,'incomplete_document_scope',f'{doc["id"]}: supply the complete list for this booking once.')
                continue
            if not doc.get('booking_ref') or doc['booking_ref'] not in refs:
                issue(None,'document:'+role,'document_scope_mismatch',f'{doc["id"]}: complete-list booking scope does not match the booking confirmation.')
                missing.add(role)
                continue
            if not ordinary(doc):
                issue(None,'document:'+role,'unverified_closed_world',f'{doc["id"]}: a photo/handwritten list cannot establish omitted boxes without independent typed evidence.')
                missing.add(role)
                continue
            text=str(doc.get('text',''))
            listed=set(_ID.findall(text))
            scoped=members[doc['booking_ref']] & set(base['load_list'])-set(excluded)
            if listed-members[doc['booking_ref']]:
                missing.add(role)
                issue(None,'document:'+role,'list_membership_conflict',f'{doc["id"]}: listed identities are absent from the scoped booking roster; no omitted-box declarations were inferred.',sorted(listed-members[doc['booking_ref']]))
                continue
            if not scoped:
                missing.add(role)
                issue(None,'document:'+role,'unverified_booking_scope',f'{doc["id"]}: no independently readable booking roster establishes the list scope.')
            for rid in scoped:
                if rid in listed and field!='reefer':
                    if field=='oog':
                        issue(rid,'oog','missing_oog_declaration',f'{rid}: listed OOG cargo requires verified dimensions; an absent model reading cannot declare it in gauge.')
                    continue
                if field=='reefer':
                    value=rid in listed
                else:
                    value=None
                add({'row_id':rid,'field':field,'value':value,'document':doc['id'],
                     'quote':text,'derived':True,'evidence':'closed_world_declaration',
                     'inputs':[f'complete {role} for booking {doc["booking_ref"]}',f'{rid} '+('listed' if rid in listed else 'not listed')],
                     'closed_world':True},'closed_world')
    # An explicit per-box SI flag can replace a separate reefer roster. An
    # OOG list is required unless equivalent complete, typed OOG declarations
    # already cover every box. Missing documents are one host task, not N tasks.
    for field,role in [('reefer','reefer_list'),('oog','oog_list')]:
        if by_role.get(role):
            continue
        covered=all(any(authoritative(field,next(d for d in documents if d['id']==o['document']))
                        for o in grouped[rid].get(field,[])) for rid in set(base['load_list'])-set(excluded))
        if not covered:
            missing.add(role)
            issue(None,'document:'+role,'missing_document',f'Supply the complete {role.replace("_"," ")} for this booking once.',[role])
    return missing


def missing_role(field,missing):
    return bool(field_roles(field)&missing)


def required(field):
    return field not in {'oog','net_mass_kg','commodity','seal','special_stowage'}


def dependency_questions(rows,questions,issue):
    """Source conflicts can block inputs before the cargo gate sees them."""
    by_row={r['id']:r for r in rows}
    for rid,row in by_row.items():
        if 'reefer_setpoint_c' in row and row['reefer_setpoint_c'] is not None:
            blockers=[q for q in questions if q.get('row_id')==rid and q.get('field') in ('reefer','iso_type')]
            if blockers:
                issue(rid,'reefer_setpoint_c','reefer_dependency',f'{rid}: the set point cannot be accepted while operating-reefer or ISO identity is unresolved.')
