"""Transcribe segregation-code meanings used in the curated DGL (not all codes)."""
import json
from pathlib import Path

codes = {
    'SG5': (492, 'Segregation as for class 3.'),
    'SG6': (492, 'Segregation as for class 5.1.'),
    'SG14': (492, 'Stow separated from class 1 except for division 1.4S.'),
    'SG17': (492, 'Stow separated from class 5.1.'),
    'SG19': (492, 'Stow separated from class 7.'),
    'SG25': (493, 'Stow separated from goods of classes 2.1 and 3.'),
    'SG26': (493, 'In addition: from goods of classes 2.1 and 3 when stowed on deck of a containership a minimum distance of two container spaces athwartship shall be maintained; when stowed on ro-ro ships a distance of 6 m athwartship shall be maintained.'),
    'SG27': (493, 'Stow separated from explosives containing chlorates or perchlorates.'),
    'SG31': (493, 'Stow away from SGG9 - lead and its compounds.'),
    'SG34': (493, 'When containing ammonium compounds, separated from SGG4 - chlorates or SGG13 - perchlorates and explosives containing chlorates or perchlorates.'),
    'SG35': (493, 'Stow separated from SGG1 - acids.'),
    'SG36': (493, 'Stow separated from SGG18 - alkalis.'),
    'SG38': (493, 'Stow separated from SGG2 - ammonium compounds.'),
    'SG46': (493, 'Stow separated from chlorine.'),
    'SG49': (493, 'Stow separated from SGG6 - cyanides.'),
    'SG50': (493, 'Segregation from foodstuffs as in 7.3.4.2.1, 7.6.3.1.2 or 7.7.3.6.'),
    'SG53': (493, 'Shall not be stowed together with combustible material in the same cargo transport unit.'),
    'SG60': (493, 'Stow separated from SGG16 - peroxides.'),
    'SG72': (494, 'See tables in 7.2.6.3.'),
    'SGG1': (488, 'acids'),
    'SGG2': (488, 'ammonium compounds'),
    'SGG6': (488, 'cyanides'),
    'SGG8': (488, 'hypochlorites'),
    'SGG18': (488, 'alkalis'),
}
rows = [dict(code=code, meaning=text, source=dict(
    document='IMO MSC.556(108), IMDG Code Amendment 42-24', pdf_page=page,
    table='7.2.5.2' if code.startswith('SGG') else '7.2.8',
    row=code, column='Description')) for code, (page, text) in codes.items()]
Path('src/stowage/rules/imdg/data/segregation_codes.json').write_text(json.dumps({
    'edition': '42-24', 'coverage': 'Only codes assigned to the curated DGL sample.',
    'notes': 'Meanings normalize typography. Group membership is not inferred from these names; consult 3.1.4.4 and declarations under 7.2.5.3. Cross-referenced conditions and exceptions remain applicable.',
    'codes': rows}, indent=2) + '\n')
