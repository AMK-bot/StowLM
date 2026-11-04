"""A printable one-page boundary of this synthetic handover."""
from html import escape


def render_not_included(*, banner, report, scope, cargo_work):
    shut_out = len(report.shut_out)
    rows = [
        ('Cargo scope', 'An empty section, on a ship whose other bays are a fixed synthetic load. '
         'The remaining bays are outside the planned section; their fixed cargo is included in stability and strength.'),
        ('Shut out', f'{shut_out} shut out. ' + (', '.join(report.shut_out) if shut_out else 'Every box in the section load list is accounted for.')),
        ('Cargo-work stability', "Stability during loading isn't checked. The in-port rule is off. "
         'List estimates are projections, not intermediate hydrostatic checks. Flagged lifts require a chief officer heel-ballast hold and an approved tank programme before release.'),
        ('Ballast', scope['ballast_line'] + ' The tank table is a mass inventory, not a timed pump/valve or heel-ballast programme. No transfer is invented or credited to reduce estimated list.'),
        ('Securing', "Lashing is our approximation, with synthetic gear, because class rules aren't public. "
         'No vessel-specific Cargo Securing Manual or class approval is supplied.'),
        ('Exchange', 'BAPLIE omitted: a correct public-profile mapping and independent carrier-system validation were not established. manifest.csv is a synthetic stow list, not a validated BAPLIE message.'),
        ('Cargo declarations', 'DG, reefer and OOG fields reproduce available synthetic data; missing PSN, packing group, EmS, segregation or temperature data are explicitly marked. They are not signed shipper declarations.'),
        ('Operations', 'Crane assignment is a suggested sequential allocation. No lift duration, simultaneous crane interference, terminal execution, pump/valve sequence or operator approval is checked.'),
        ('Voyage and approval', 'No whole-ship sailing plan, actual loading-computer approval, port clearance, weather routing or continuous-voyage worst-case search. No ship can sail on this plan alone.'),
    ]
    items = ''.join(f'<dt>{escape(title)}</dt><dd>{escape(text)}</dd>' for title, text in rows)
    return (f'<!-- {escape(banner)} -->\n<!doctype html><html lang="en"><meta charset="utf-8">'
            '<title>Not included in this handover</title><style>'
            '@page{size:A4;margin:14mm}*{box-sizing:border-box}body{font:9pt/1.25 Arial,sans-serif;color:#142d3d;max-width:180mm;margin:auto}'
            'h1{font-size:20pt}p{padding:3mm;background:#fff3d6;font-size:9pt}'
            'dl{display:grid;grid-template-columns:33mm 1fr;gap:2.4mm 4mm}dt{font-weight:bold}dd{margin:0}'
            '@media print{body{margin:0}dl{break-inside:avoid}}'
            f'</style><body><p>{escape(banner)}</p><h1>Not included in this handover</h1><dl>{items}</dl></body></html>\n')
