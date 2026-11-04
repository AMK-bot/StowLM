"""Historical D27 finisher witnesses and requalified D64 operating refits.

APPROXIMATION: synthetic loading-manual data, not approved operating instructions.
Historical records passed the independent full checker after a bounded finisher
call. The ON-only D64 static refits passed a fresh independent complete check
without a new finisher invocation. Only adjustable ballast changes; cargo reserves and 97% departure
consumables are deliberately independent of these operating tank masses.
Source artifacts and finisher provenance are retained as inert evidence, never
imported or executed at runtime. Geometry changes require requalification.
"""
from __future__ import annotations

from collections.abc import Mapping

CURRENT_WITNESSES = {'feeder_800.C2': {'input_fingerprint': '489c0442451ad8332a2aab0f20b955535d5973091068c12ca44817b5900b6e3f',
                    'provenance': {'created_at': None,
                                   'created_by': 'solver.finish_ballast',
                                   'git_commit': None,
                                   'input_versions': {},
                                   'parents': ['APPROXIMATION: D50 checks declared departure/arrival '
                                               'endpoints; no worst-intermediate-consumables claim',
                                               'ASSUMPTION Q9: row 1 in every block has reefer plugs; '
                                               'final block permits DG, including reefer DG; detailed DG '
                                               'segregation remains mandatory.',
                                               'ASSUMPTION: LOCODE labels THLCH/SGSIN/THBKK/USLAX identify '
                                               'synthetic scenarios, not real terminals.',
                                               'ASSUMPTION: dock water density 1025 kg/m3 (seawater); no '
                                               'measured density is supplied.',
                                               'ASSUMPTION: synthetic consumable draw-down is globally '
                                               'forward first and aft last, using descending full-sounding '
                                               'LCG and canonical tank ID to break ties; no service-tank '
                                               'roles or usual operating practice are inferred.',
                                               'D27: tank geometry and explicit Tank.kind are declared '
                                               'synthetic design inputs',
                                               'D50: each declared consumable group retains 0.1 of '
                                               'departure contents unless complete explicit consumption '
                                               'replaces it',
                                               'DECLARATION: Tank.kind=diesel_oil',
                                               'DECLARATION: Tank.kind=fresh_water',
                                               'DECLARATION: Tank.kind=fuel_oil',
                                               'DECLARATION: absent from supplied tank catalog: lube_oil, '
                                               'stores',
                                               'UNVERIFIED: berth/channel draught limit is unknown (None), '
                                               'not an assertion of unlimited draught.',
                                               'conv_C2_feeder_1000',
                                               'conv_C2_feeder_1000.convergence-restore',
                                               'data/raw/is-code/msc267-85.pdf: Part B 3.4.1.2 and '
                                               '3.4.1.3, PDF p41',
                                               'dock-water:THBKK:a656aac3bc05c55fbe9fdf2ebf68599486710887d3524382b341f7a30d8b4227',
                                               'dock-water:THLCH:76a5bae4f1802a0c342bb2628c4d0051b0f7c38456bedefb76a671f7825fb15c',
                                               'docs/decisions.md:D27',
                                               'docs/decisions.md:D34',
                                               'docs/decisions.md:D50',
                                               'docs/decisions.md:D56',
                                               'experiments/physics-review/convergence/harness.py:place',
                                               'experiments/physics-review/convergence/post_d30/harness2.py:build',
                                               'experiments/physics-review/convergence/post_d30/search_feeder_1000_C1.json '
                                               'sha256:8505a20a575e9e07a50cd77449288ec5430baa7985c5b8610bd909b89dc3cce8',
                                               'experiments/physics-review/convergence/post_d30/search_feeder_1000_C2.json '
                                               'sha256:3cc53fd5ca1a69a4eebb9e5ad2b8750cbd9f05bd5a7070242149fadad5dad1ad',
                                               'experiments/physics-review/convergence/post_d30/search_feeder_1000_C3.json '
                                               'sha256:68cc097c7611f25bb00436d69474a20b0800a5d8c4ac1b22b7e2bd6ce95b43cb',
                                               'feeder_1000',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[DO-P]',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[DO-P].table[-1].lcg_m=26.265996232377745',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[DO-S]',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[DO-S].table[-1].lcg_m=26.265996232377734',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[FO-A-P]',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[FO-A-P].table[-1].lcg_m=17.336421896156274',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[FO-A-S]',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[FO-A-S].table[-1].lcg_m=17.33642189615627',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[FO-F-P]',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[FO-F-P].table[-1].lcg_m=103.71889399970796',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[FO-F-S]',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[FO-F-S].table[-1].lcg_m=103.71889399970796',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[FW-P]',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[FW-P].table[-1].lcg_m=22.773510741855596',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[FW-S]',
                                               'params/fleet/feeder_1000.toml '
                                               'sha256:6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94#tanks[FW-S].table[-1].lcg_m=22.773510741855596',
                                               'params/ports/small_feeder.toml '
                                               'sha256:b3ccc9cddb40e9b6a8e81d5f3776323756a23c359853ead9b46178ae76ea8b31',
                                               'rotation:synthetic_thai_coastal:call:0:THLCH',
                                               'rotation:synthetic_thai_coastal:call:1:THBKK',
                                               'src/stowage/model/fixtures.py: historical manual route '
                                               'THLCH->THBKK->THSGZ',
                                               'synthetic fixture; no actual terminal operating limits',
                                               'synthetic-port:laem_chabang_like:317aa928fbcbf14935bc1a595025b9fe8cce4bf731ef836acaf3110dd5ee88fe',
                                               'synthetic-port:small_feeder:a9b9b9d6b81c732b8a20d6892b98c259bc35fb36f388f794648c1cca5a8b429c'],
                                   'seed': 0,
                                   'stubs': {},
                                   'versions': {'action_space': '1.0.1',
                                                'ballast': '3.0',
                                                'checker': '2.0',
                                                'feasibility': '2.4',
                                                'infrastructure': '3.0',
                                                'linear_model': '3.5',
                                                'move_engine': '2.0',
                                                'rule_catalogue': 'sha256:31457627bf375bb068c47479f9671598a092bf0beb10f01c8c278f782b417b34',
                                                'schema': '1.8',
                                                'solver.scalable': '2.6.4'}},
                    'seed': 0,
                    'source': 'experiments/physics-review/rerun/fix-t2-vessel/restore/unchanged-v2/feeder_1000_C2/result.json '
                              'sha256:4b91f00dd4ba93d5091331e49e6123c1974c82d4d1f8e19ce9d064b563443dc4',
                    'tank_masses_kg': {'APT-P': 0.0,
                                       'APT-S': 0.0,
                                       'DB-A-P': 0.0,
                                       'DB-A-S': 0.0,
                                       'DB-F-P': 174789.03338234342,
                                       'DB-F-S': 174789.03338234342,
                                       'DB-M-P': 0.0,
                                       'DB-M-S': 0.0,
                                       'FPT-P': 118485.11068886245,
                                       'FPT-S': 118485.11068886248,
                                       'FWD-DEEP-P': 151866.88193783766,
                                       'FWD-DEEP-S': 151866.88193783766,
                                       'HEEL-P': 0.0,
                                       'HEEL-S': 0.0,
                                       'WING-A-P': 0.0,
                                       'WING-A-S': 0.0,
                                       'WING-F-P': 0.0,
                                       'WING-F-S': 0.0},
                    'time_limit_s': 60.0},
 'feeder_1700.C2': {'input_fingerprint': 'd9cb0bb7bf6f6f0f7402bafaf381100dfde184cc70af0a50b8669d7a39b66405',
                    'provenance': {'created_at': None,
                                   'created_by': 'solver.finish_ballast',
                                   'git_commit': None,
                                   'input_versions': {},
                                   'parents': ['APPROXIMATION: D50 checks declared departure/arrival '
                                               'endpoints; no worst-intermediate-consumables claim',
                                               'ASSUMPTION Q9: row 1 in every block has reefer plugs; '
                                               'final block permits DG, including reefer DG; detailed DG '
                                               'segregation remains mandatory.',
                                               'ASSUMPTION: LOCODE labels THLCH/SGSIN/THBKK/USLAX identify '
                                               'synthetic scenarios, not real terminals.',
                                               'ASSUMPTION: dock water density 1025 kg/m3 (seawater); no '
                                               'measured density is supplied.',
                                               'ASSUMPTION: synthetic consumable draw-down is globally '
                                               'forward first and aft last, using descending full-sounding '
                                               'LCG and canonical tank ID to break ties; no service-tank '
                                               'roles or usual operating practice are inferred.',
                                               'D27: tank geometry and explicit Tank.kind are declared '
                                               'synthetic design inputs',
                                               'D50: each declared consumable group retains 0.1 of '
                                               'departure contents unless complete explicit consumption '
                                               'replaces it',
                                               'DECLARATION: Tank.kind=diesel_oil',
                                               'DECLARATION: Tank.kind=fresh_water',
                                               'DECLARATION: Tank.kind=fuel_oil',
                                               'DECLARATION: absent from supplied tank catalog: lube_oil, '
                                               'stores',
                                               'UNVERIFIED: berth/channel draught limit is unknown (None), '
                                               'not an assertion of unlimited draught.',
                                               'conv_C2_feeder_1700',
                                               'conv_C2_feeder_1700.convergence-restore',
                                               'data/raw/is-code/msc267-85.pdf: Part B 3.4.1.2 and '
                                               '3.4.1.3, PDF p41',
                                               'dock-water:THBKK:a656aac3bc05c55fbe9fdf2ebf68599486710887d3524382b341f7a30d8b4227',
                                               'dock-water:THLCH:3d95a32863c40bc7d782ca6543e09992e1c3f33ed2bac771a6b79dd4ae1ef6dd',
                                               'docs/decisions.md:D27',
                                               'docs/decisions.md:D34',
                                               'docs/decisions.md:D50',
                                               'docs/decisions.md:D56',
                                               'experiments/physics-review/CONVERGENCE.md:1700 C2 first '
                                               'DB+HEEL candidate, no passing witness',
                                               'experiments/physics-review/convergence/harness.py:place',
                                               'experiments/physics-review/convergence/post_d30/harness2.py:build',
                                               'experiments/physics-review/convergence/post_d30/search_feeder_1700_C1.json '
                                               'sha256:7574213ab3f2f89e5bdc0e64fa6cafb097f27cda734137497c30aab817757246',
                                               'experiments/physics-review/convergence/post_d30/search_feeder_1700_C3.json '
                                               'sha256:c4c3f1a808da529d7cfbe2faf97ebe216a0b4b0eb559f9efa8beef40770bafb1',
                                               'feeder_1700',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[DO-P]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[DO-P].table[-1].lcg_m=17.581416651440925',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[DO-S]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[DO-S].table[-1].lcg_m=17.58141665144092',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-A-P]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-A-P].table[-1].lcg_m=21.507749158963783',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-A-S]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-A-S].table[-1].lcg_m=21.507749158963787',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-F-P]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-F-P].table[-1].lcg_m=133.73523318820725',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-F-S]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-F-S].table[-1].lcg_m=133.7352331882072',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FW-P]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FW-P].table[-1].lcg_m=28.64129458248091',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FW-S]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FW-S].table[-1].lcg_m=28.64129458248091',
                                               'params/ports/small_feeder.toml '
                                               'sha256:b3ccc9cddb40e9b6a8e81d5f3776323756a23c359853ead9b46178ae76ea8b31',
                                               'rotation:synthetic_thai_coastal:call:0:THLCH',
                                               'rotation:synthetic_thai_coastal:call:1:THBKK',
                                               'src/stowage/model/fixtures.py: historical manual route '
                                               'THLCH->THBKK->THSGZ',
                                               'synthetic fixture; no actual terminal operating limits',
                                               'synthetic-port:laem_chabang_like:4fd1e6d21d558d9c006f12510a82bc50a8d3666052962c4ae3044b67e16bda9c',
                                               'synthetic-port:small_feeder:a9b9b9d6b81c732b8a20d6892b98c259bc35fb36f388f794648c1cca5a8b429c'],
                                   'seed': 0,
                                   'stubs': {},
                                   'versions': {'action_space': '1.0.1',
                                                'ballast': '3.0',
                                                'checker': '2.0',
                                                'feasibility': '2.4',
                                                'infrastructure': '3.0',
                                                'linear_model': '3.5',
                                                'move_engine': '2.0',
                                                'rule_catalogue': 'sha256:31457627bf375bb068c47479f9671598a092bf0beb10f01c8c278f782b417b34',
                                                'schema': '1.8',
                                                'solver.scalable': '2.6.4'}},
                    'seed': 0,
                    'source': 'experiments/physics-review/rerun/fix-t2-vessel/restore/unchanged-v2/feeder_1700_C2/result.json '
                              'sha256:716358dea32aba916ed21f8749ddd8390db6dd26aba24c172f872fff5cc3022c',
                    'tank_masses_kg': {'APT-P': 0.0,
                                       'APT-S': 0.0,
                                       'DB-A-P': 0.0,
                                       'DB-A-S': 0.0,
                                       'DB-F-P': 0.0,
                                       'DB-F-S': 0.0,
                                       'DB-M-P': 0.0,
                                       'DB-M-S': 0.0,
                                       'FPT-P': 245966.9879108559,
                                       'FPT-S': 245966.9879108559,
                                       'FWD-DEEP-P': 303315.5938574785,
                                       'FWD-DEEP-S': 303315.5938574786,
                                       'HEEL-P': 0.0,
                                       'HEEL-S': 0.0,
                                       'WING-A-P': 0.0,
                                       'WING-A-S': 0.0,
                                       'WING-F-P': 0.0,
                                       'WING-F-S': 0.0},
                    'time_limit_s': 60.0},
 'feeder_1700.C3': {'input_fingerprint': 'de5c5f57720e2b9fd757519735b895495fe2d5040ad052d587ebea7f7f2161e1',
                    'provenance': {'created_at': None,
                                   'created_by': 'solver.finish_ballast',
                                   'git_commit': None,
                                   'input_versions': {},
                                   'parents': ['APPROXIMATION: D50 checks declared departure/arrival '
                                               'endpoints; no worst-intermediate-consumables claim',
                                               'ASSUMPTION Q9: row 1 in every block has reefer plugs; '
                                               'final block permits DG, including reefer DG; detailed DG '
                                               'segregation remains mandatory.',
                                               'ASSUMPTION: LOCODE labels THLCH/SGSIN/THBKK/USLAX identify '
                                               'synthetic scenarios, not real terminals.',
                                               'ASSUMPTION: dock water density 1025 kg/m3 (seawater); no '
                                               'measured density is supplied.',
                                               'ASSUMPTION: synthetic consumable draw-down is globally '
                                               'forward first and aft last, using descending full-sounding '
                                               'LCG and canonical tank ID to break ties; no service-tank '
                                               'roles or usual operating practice are inferred.',
                                               'D27: tank geometry and explicit Tank.kind are declared '
                                               'synthetic design inputs',
                                               'D50: each declared consumable group retains 0.1 of '
                                               'departure contents unless complete explicit consumption '
                                               'replaces it',
                                               'DECLARATION: Tank.kind=diesel_oil',
                                               'DECLARATION: Tank.kind=fresh_water',
                                               'DECLARATION: Tank.kind=fuel_oil',
                                               'DECLARATION: absent from supplied tank catalog: lube_oil, '
                                               'stores',
                                               'UNVERIFIED: berth/channel draught limit is unknown (None), '
                                               'not an assertion of unlimited draught.',
                                               'conv_C3_feeder_1700',
                                               'conv_C3_feeder_1700.convergence-restore',
                                               'data/raw/is-code/msc267-85.pdf: Part B 3.4.1.2 and '
                                               '3.4.1.3, PDF p41',
                                               'dock-water:THBKK:a656aac3bc05c55fbe9fdf2ebf68599486710887d3524382b341f7a30d8b4227',
                                               'dock-water:THLCH:3d95a32863c40bc7d782ca6543e09992e1c3f33ed2bac771a6b79dd4ae1ef6dd',
                                               'docs/decisions.md:D27',
                                               'docs/decisions.md:D34',
                                               'docs/decisions.md:D50',
                                               'docs/decisions.md:D56',
                                               'experiments/physics-review/CONVERGENCE.md:1700 C2 first '
                                               'DB+HEEL candidate, no passing witness',
                                               'experiments/physics-review/convergence/harness.py:place',
                                               'experiments/physics-review/convergence/post_d30/harness2.py:build',
                                               'experiments/physics-review/convergence/post_d30/search_feeder_1700_C1.json '
                                               'sha256:7574213ab3f2f89e5bdc0e64fa6cafb097f27cda734137497c30aab817757246',
                                               'experiments/physics-review/convergence/post_d30/search_feeder_1700_C3.json '
                                               'sha256:c4c3f1a808da529d7cfbe2faf97ebe216a0b4b0eb559f9efa8beef40770bafb1',
                                               'feeder_1700',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[DO-P]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[DO-P].table[-1].lcg_m=17.581416651440925',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[DO-S]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[DO-S].table[-1].lcg_m=17.58141665144092',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-A-P]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-A-P].table[-1].lcg_m=21.507749158963783',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-A-S]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-A-S].table[-1].lcg_m=21.507749158963787',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-F-P]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-F-P].table[-1].lcg_m=133.73523318820725',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-F-S]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FO-F-S].table[-1].lcg_m=133.7352331882072',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FW-P]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FW-P].table[-1].lcg_m=28.64129458248091',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FW-S]',
                                               'params/fleet/feeder_1700.toml '
                                               'sha256:ab9c1bdbd3f47b56d74a79f9c326fbc8446e311d6294c5c6127447eb06708b2e#tanks[FW-S].table[-1].lcg_m=28.64129458248091',
                                               'params/ports/small_feeder.toml '
                                               'sha256:b3ccc9cddb40e9b6a8e81d5f3776323756a23c359853ead9b46178ae76ea8b31',
                                               'rotation:synthetic_thai_coastal:call:0:THLCH',
                                               'rotation:synthetic_thai_coastal:call:1:THBKK',
                                               'src/stowage/model/fixtures.py: historical manual route '
                                               'THLCH->THBKK->THSGZ',
                                               'synthetic fixture; no actual terminal operating limits',
                                               'synthetic-port:laem_chabang_like:4fd1e6d21d558d9c006f12510a82bc50a8d3666052962c4ae3044b67e16bda9c',
                                               'synthetic-port:small_feeder:a9b9b9d6b81c732b8a20d6892b98c259bc35fb36f388f794648c1cca5a8b429c'],
                                   'seed': 0,
                                   'stubs': {},
                                   'versions': {'action_space': '1.0.1',
                                                'ballast': '3.0',
                                                'checker': '2.0',
                                                'feasibility': '2.4',
                                                'infrastructure': '3.0',
                                                'linear_model': '3.5',
                                                'move_engine': '2.0',
                                                'rule_catalogue': 'sha256:31457627bf375bb068c47479f9671598a092bf0beb10f01c8c278f782b417b34',
                                                'schema': '1.8',
                                                'solver.scalable': '2.6.4'}},
                    'seed': 0,
                    'source': 'experiments/physics-review/rerun/fix-t2-vessel/restore/unchanged-v2/feeder_1700_C3/result.json '
                              'sha256:89ee76fcd87edc71309ce9d01849bebe59278a3e1567a44f11534a7b09258604',
                    'tank_masses_kg': {'APT-P': 0.0,
                                       'APT-S': 0.0,
                                       'DB-A-P': 207890.2409504449,
                                       'DB-A-S': 207890.2409504449,
                                       'DB-F-P': 269409.65604605863,
                                       'DB-F-S': 269409.65604605863,
                                       'DB-M-P': 772912.3020753908,
                                       'DB-M-S': 772912.3020753908,
                                       'FPT-P': 245966.9879108559,
                                       'FPT-S': 245966.9879108559,
                                       'FWD-DEEP-P': 151657.79692873926,
                                       'FWD-DEEP-S': 151657.7969287393,
                                       'HEEL-P': 216044.36613189816,
                                       'HEEL-S': 216044.36613189825,
                                       'WING-A-P': 0.0,
                                       'WING-A-S': 0.0,
                                       'WING-F-P': 0.0,
                                       'WING-F-S': 0.0},
                    'time_limit_s': 60.0},
 'feeder_500.C1': {'input_fingerprint': 'bd2dfa7e96898480acc25402a60c13d680ec0fe3029615b5ee730511d4d049ff',
                   'provenance': {'created_at': None,
                                  'created_by': 'solver.finish_ballast',
                                  'git_commit': None,
                                  'input_versions': {},
                                  'parents': ['APPROXIMATION: D50 checks declared departure/arrival '
                                              'endpoints; no worst-intermediate-consumables claim',
                                              'ASSUMPTION Q9: row 1 in every block has reefer plugs; final '
                                              'block permits DG, including reefer DG; detailed DG '
                                              'segregation remains mandatory.',
                                              'ASSUMPTION: LOCODE labels THLCH/SGSIN/THBKK/USLAX identify '
                                              'synthetic scenarios, not real terminals.',
                                              'ASSUMPTION: dock water density 1025 kg/m3 (seawater); no '
                                              'measured density is supplied.',
                                              'ASSUMPTION: synthetic consumable draw-down is globally '
                                              'forward first and aft last, using descending full-sounding '
                                              'LCG and canonical tank ID to break ties; no service-tank '
                                              'roles or usual operating practice are inferred.',
                                              'D27: tank geometry and explicit Tank.kind are declared '
                                              'synthetic design inputs',
                                              'D50: each declared consumable group retains 0.1 of '
                                              'departure contents unless complete explicit consumption '
                                              'replaces it',
                                              'DECLARATION: Tank.kind=diesel_oil',
                                              'DECLARATION: Tank.kind=fresh_water',
                                              'DECLARATION: Tank.kind=fuel_oil',
                                              'DECLARATION: absent from supplied tank catalog: lube_oil, '
                                              'stores',
                                              'UNVERIFIED: berth/channel draught limit is unknown (None), '
                                              'not an assertion of unlimited draught.',
                                              'conv_C1_feeder_500',
                                              'conv_C1_feeder_500.convergence-restore',
                                              'data/raw/is-code/msc267-85.pdf: Part B 3.4.1.2 and 3.4.1.3, '
                                              'PDF p41',
                                              'dock-water:THBKK:a656aac3bc05c55fbe9fdf2ebf68599486710887d3524382b341f7a30d8b4227',
                                              'dock-water:THLCH:b87dfc7616837f619b2181d33ccc982ac0c21da2b0489ac1bb739d745ffde10d',
                                              'docs/decisions.md:D27',
                                              'docs/decisions.md:D34',
                                              'docs/decisions.md:D50',
                                              'docs/decisions.md:D56',
                                              'experiments/physics-review/convergence/harness.py:place',
                                              'experiments/physics-review/convergence/post_d30/harness2.py:build',
                                              'experiments/physics-review/convergence/post_d30/search_feeder_500_C1.json '
                                              'sha256:73d3328b594b2757897c5ac0f9edf0906d3562ce51899c851db9e421a4355e1b',
                                              'experiments/physics-review/convergence/post_d30/search_feeder_500_C2.json '
                                              'sha256:785f58bf41bda474266def09ba23b9d0119d78453c633ff0a5006c7b99ca6999',
                                              'experiments/physics-review/convergence/post_d30/search_feeder_500_C3.json '
                                              'sha256:f215a77f9838d08c8e365e282e40e7a061ec7b3914801142fb9fe97873b3d928',
                                              'feeder_500',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[DO-P]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[DO-P].table[-1].lcg_m=19.66567676071772',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[DO-S]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[DO-S].table[-1].lcg_m=19.665676760717716',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-A-P]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-A-P].table[-1].lcg_m=16.398109613939745',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-A-S]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-A-S].table[-1].lcg_m=16.39810961393975',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-F-P]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-F-P].table[-1].lcg_m=88.6256876966863',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-F-S]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-F-S].table[-1].lcg_m=88.62568769668631',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FW-P]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FW-P].table[-1].lcg_m=22.18696917615442',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FW-S]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FW-S].table[-1].lcg_m=22.186969176154424',
                                              'params/ports/small_feeder.toml '
                                              'sha256:b3ccc9cddb40e9b6a8e81d5f3776323756a23c359853ead9b46178ae76ea8b31',
                                              'rotation:synthetic_thai_coastal:call:0:THLCH',
                                              'rotation:synthetic_thai_coastal:call:1:THBKK',
                                              'src/stowage/model/fixtures.py: historical manual route '
                                              'THLCH->THBKK->THSGZ',
                                              'synthetic fixture; no actual terminal operating limits',
                                              'synthetic-port:laem_chabang_like:d1e3069c2ce17581e22df71a8e890f099ade21493ca8a6a9fd40005d9033d6a4',
                                              'synthetic-port:small_feeder:a9b9b9d6b81c732b8a20d6892b98c259bc35fb36f388f794648c1cca5a8b429c'],
                                  'seed': 0,
                                  'stubs': {},
                                  'versions': {'action_space': '1.0.1',
                                               'ballast': '3.0',
                                               'checker': '2.0',
                                               'feasibility': '2.4',
                                               'infrastructure': '3.0',
                                               'linear_model': '3.5',
                                               'move_engine': '2.0',
                                               'rule_catalogue': 'sha256:31457627bf375bb068c47479f9671598a092bf0beb10f01c8c278f782b417b34',
                                               'schema': '1.8',
                                               'solver.scalable': '2.6.4'}},
                   'seed': 0,
                   'source': 'experiments/physics-review/rerun/fix-t2-vessel/restore/unchanged-v2/feeder_500_C1/result.json '
                             'sha256:2c62c33d8b40921e00f6f886500e8b399129d86eca6cab746bb6ceba44545c62',
                   'tank_masses_kg': {'APT-P': 0.0,
                                      'APT-S': 0.0,
                                      'DB-A-P': 50121.21252296202,
                                      'DB-A-S': 50121.21252296202,
                                      'DB-F-P': 112216.5959502341,
                                      'DB-F-S': 112216.5959502341,
                                      'DB-M-P': 312434.6319652328,
                                      'DB-M-S': 312434.6319652328,
                                      'FPT-P': 71071.59098407856,
                                      'FPT-S': 71071.59098407856,
                                      'FWD-DEEP-P': 100131.41062440611,
                                      'FWD-DEEP-S': 100131.41062440613,
                                      'HEEL-P': 0.0,
                                      'HEEL-S': 0.0,
                                      'WING-A-P': 389656.70743010635,
                                      'WING-A-S': 389656.70743010635,
                                      'WING-F-P': 224607.45387319505,
                                      'WING-F-S': 224607.45387319502},
                   'time_limit_s': 60.0},
 'feeder_500.C3': {'input_fingerprint': '34cf65de6bca9e68c335555be2ab725db77d5203330eae319528a69358f829f3',
                   'provenance': {'created_at': None,
                                  'created_by': 'solver.finish_ballast',
                                  'git_commit': None,
                                  'input_versions': {},
                                  'parents': ['APPROXIMATION: D50 checks declared departure/arrival '
                                              'endpoints; no worst-intermediate-consumables claim',
                                              'ASSUMPTION Q9: row 1 in every block has reefer plugs; final '
                                              'block permits DG, including reefer DG; detailed DG '
                                              'segregation remains mandatory.',
                                              'ASSUMPTION: LOCODE labels THLCH/SGSIN/THBKK/USLAX identify '
                                              'synthetic scenarios, not real terminals.',
                                              'ASSUMPTION: dock water density 1025 kg/m3 (seawater); no '
                                              'measured density is supplied.',
                                              'ASSUMPTION: synthetic consumable draw-down is globally '
                                              'forward first and aft last, using descending full-sounding '
                                              'LCG and canonical tank ID to break ties; no service-tank '
                                              'roles or usual operating practice are inferred.',
                                              'D27: tank geometry and explicit Tank.kind are declared '
                                              'synthetic design inputs',
                                              'D50: each declared consumable group retains 0.1 of '
                                              'departure contents unless complete explicit consumption '
                                              'replaces it',
                                              'DECLARATION: Tank.kind=diesel_oil',
                                              'DECLARATION: Tank.kind=fresh_water',
                                              'DECLARATION: Tank.kind=fuel_oil',
                                              'DECLARATION: absent from supplied tank catalog: lube_oil, '
                                              'stores',
                                              'UNVERIFIED: berth/channel draught limit is unknown (None), '
                                              'not an assertion of unlimited draught.',
                                              'conv_C3_feeder_500',
                                              'conv_C3_feeder_500.convergence-restore',
                                              'data/raw/is-code/msc267-85.pdf: Part B 3.4.1.2 and 3.4.1.3, '
                                              'PDF p41',
                                              'dock-water:THBKK:a656aac3bc05c55fbe9fdf2ebf68599486710887d3524382b341f7a30d8b4227',
                                              'dock-water:THLCH:b87dfc7616837f619b2181d33ccc982ac0c21da2b0489ac1bb739d745ffde10d',
                                              'docs/decisions.md:D27',
                                              'docs/decisions.md:D34',
                                              'docs/decisions.md:D50',
                                              'docs/decisions.md:D56',
                                              'experiments/physics-review/convergence/harness.py:place',
                                              'experiments/physics-review/convergence/post_d30/harness2.py:build',
                                              'experiments/physics-review/convergence/post_d30/search_feeder_500_C1.json '
                                              'sha256:73d3328b594b2757897c5ac0f9edf0906d3562ce51899c851db9e421a4355e1b',
                                              'experiments/physics-review/convergence/post_d30/search_feeder_500_C2.json '
                                              'sha256:785f58bf41bda474266def09ba23b9d0119d78453c633ff0a5006c7b99ca6999',
                                              'experiments/physics-review/convergence/post_d30/search_feeder_500_C3.json '
                                              'sha256:f215a77f9838d08c8e365e282e40e7a061ec7b3914801142fb9fe97873b3d928',
                                              'feeder_500',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[DO-P]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[DO-P].table[-1].lcg_m=19.66567676071772',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[DO-S]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[DO-S].table[-1].lcg_m=19.665676760717716',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-A-P]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-A-P].table[-1].lcg_m=16.398109613939745',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-A-S]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-A-S].table[-1].lcg_m=16.39810961393975',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-F-P]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-F-P].table[-1].lcg_m=88.6256876966863',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-F-S]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FO-F-S].table[-1].lcg_m=88.62568769668631',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FW-P]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FW-P].table[-1].lcg_m=22.18696917615442',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FW-S]',
                                              'params/fleet/feeder_500.toml '
                                              'sha256:609b884c4ff77f1fadad837b139cc86f04555f9a61bb80b32f0d1fc9b9616695#tanks[FW-S].table[-1].lcg_m=22.186969176154424',
                                              'params/ports/small_feeder.toml '
                                              'sha256:b3ccc9cddb40e9b6a8e81d5f3776323756a23c359853ead9b46178ae76ea8b31',
                                              'rotation:synthetic_thai_coastal:call:0:THLCH',
                                              'rotation:synthetic_thai_coastal:call:1:THBKK',
                                              'src/stowage/model/fixtures.py: historical manual route '
                                              'THLCH->THBKK->THSGZ',
                                              'synthetic fixture; no actual terminal operating limits',
                                              'synthetic-port:laem_chabang_like:d1e3069c2ce17581e22df71a8e890f099ade21493ca8a6a9fd40005d9033d6a4',
                                              'synthetic-port:small_feeder:a9b9b9d6b81c732b8a20d6892b98c259bc35fb36f388f794648c1cca5a8b429c'],
                                  'seed': 0,
                                  'stubs': {},
                                  'versions': {'action_space': '1.0.1',
                                               'ballast': '3.0',
                                               'checker': '2.0',
                                               'feasibility': '2.4',
                                               'infrastructure': '3.0',
                                               'linear_model': '3.5',
                                               'move_engine': '2.0',
                                               'rule_catalogue': 'sha256:31457627bf375bb068c47479f9671598a092bf0beb10f01c8c278f782b417b34',
                                               'schema': '1.8',
                                               'solver.scalable': '2.6.4'}},
                   'seed': 0,
                   'source': 'experiments/physics-review/rerun/fix-t2-vessel/restore/unchanged-v2/feeder_500_C3/result.json '
                             'sha256:37eb5dc0cbc909e9ee348f05df33ad406d6e0d6b1148ac4c7f86ec59e1d30d87',
                   'tank_masses_kg': {'APT-P': 0.0,
                                      'APT-S': 0.0,
                                      'DB-A-P': 50121.21252296202,
                                      'DB-A-S': 50121.21252296202,
                                      'DB-F-P': 112216.5959502341,
                                      'DB-F-S': 112216.5959502341,
                                      'DB-M-P': 312434.6319652328,
                                      'DB-M-S': 312434.6319652328,
                                      'FPT-P': 65646.66519670078,
                                      'FPT-S': 65646.66519670078,
                                      'FWD-DEEP-P': 50065.705312203056,
                                      'FWD-DEEP-S': 50065.70531220306,
                                      'HEEL-P': 149892.14371833156,
                                      'HEEL-S': 149892.1437183316,
                                      'WING-A-P': 0.0,
                                      'WING-A-S': 0.0,
                                      'WING-F-P': 0.0,
                                      'WING-F-S': 0.0},
                   'time_limit_s': 60.0}}


# APPROXIMATION: synthetic D64 operating refits, qualified by the independent
# complete-checker regressions in test_fleet_conditions_manual_d64.py. These
# static maps replace existing ballast; they never alter the manual cargo
# reserve, placed cargo or 97% consumables. Upright sounding-table mass/Mx
# conservation initializes the actual finite-angle checker, not a physics pass.
_D64_REFITS = {'feeder_1700.C3': {'baseline_moments': [3727762.7000867752,
                                         357324353.7190593,
                                         -9.89530235528946e-10,
                                         9741280.361126434,
                                         92757.56687937741],
                    'fsm_delta_kgm': 2937978.1707769716,
                    'heel_increase_kg': 172146.67531469604,
                    'mass_delta_kg': 0.0,
                    'mx_delta_kgm': 1.1920928955078125e-07,
                    'my_delta_kgm': 8.731149137020111e-10,
                    'mz_delta_kgm': 388669.8003850356,
                    'initial_qualification_parameter_sha256': 'e4270e6606bdb7bab4883933aa48fdf0596345e5dd4d9f058486a581b04a1454',
                    'parameter_sha256': '50d71d7d262ab259434ef7873b8dd12fbd72bc92fa07c8f88c4f8bcc2ba2676b',
                   # Rebound again 00:49 EDT from 6672a01b1477e385: D74 amendment raised pump/HEEL
                   # transfer rates only (tanks, tables, hull identical); requalified.
                   # Rebound 29 Oct 00:26 EDT from 1194227c5919eb2c: only ballast_system
                   # ranges/provenance changed (rates, tanks, tables, hull identical);
                   # full-checker requalification rerun, see d64-witness/fix-log.md.
                   'rebound_from_parameter_sha256': '1194227c5919eb2cd33b709db62083b106bdc452fae3bb35ca5ae6a1be087b1d',
                    'qualification_report_sha256': 'ee12f65be5e1e7437ece2cfa9495b949289b6bd0b7d41cc3d6deef4524baa64e',
                    'score_kgm': 3326647.971162008,
                    'tank_masses_kg': {'APT-P': 0.0,
                                       'APT-S': 0.0,
                                       'DB-A-P': 149124.10765635964,
                                       'DB-A-S': 149124.10765635964,
                                       'DB-F-P': 269409.65604605863,
                                       'DB-F-S': 269409.65604605863,
                                       'DB-M-P': 772912.3020753908,
                                       'DB-M-S': 772912.3020753908,
                                       'FPT-P': 218659.7835475932,
                                       'FPT-S': 218659.7835475932,
                                       'FWD-DEEP-P': 151657.79692873926,
                                       'FWD-DEEP-S': 151657.7969287393,
                                       'HEEL-P': 302117.70378924604,
                                       'HEEL-S': 302117.70378924604,
                                       'WING-A-P': 0.0,
                                       'WING-A-S': 0.0,
                                       'WING-F-P': 0.0,
                                       'WING-F-S': 0.0},
                    'withdraw_groups': [['DB-A-P', 'DB-A-S'], ['FPT-P', 'FPT-S']]},
 'feeder_500.C1': {'baseline_moments': [2520479.2067004303,
                                        144904760.77539903,
                                        -2.764863893389702e-10,
                                        10070125.248218162,
                                        0.0],
                   'fsm_delta_kgm': 48660.42123638653,
                   'heel_increase_kg': 180301.337275289,
                   'mass_delta_kg': 0.0,
                   'mx_delta_kgm': 0.0,
                   'my_delta_kgm': 6.984919309616089e-10,
                   'mz_delta_kgm': -982502.6966917478,
                   'initial_qualification_parameter_sha256': '97c31c65d1cbe4c753d9da3471b2e9d96d65ecdb41c64f5f0cc20e42f2062672',
                   'parameter_sha256': '797886f10c5e858a123ae61f0550a06be6e7d620c668544a215cf3f9a6b25281',
                   # Rebound again 00:49 EDT from cc37de1034fc2f64: D74 amendment raised pump/HEEL
                   # transfer rates only (tanks, tables, hull identical); requalified.
                   # Rebound 29 Oct 00:26 EDT from e000cd57ed1012ed: only ballast_system
                   # ranges/provenance changed (rates, tanks, tables, hull identical);
                   # full-checker requalification rerun, see d64-witness/fix-log.md.
                   'rebound_from_parameter_sha256': 'e000cd57ed1012edb9144e7659215f71025716894e45e096c2e7bddf71343027',
                   'qualification_report_sha256': 'c0caf5df24f0ea8ee5fd5f450517eed7da9ba676745e4e20fbcd6065c8e83dd1',
                   'score_kgm': -933842.2754553612,
                   'tank_masses_kg': {'APT-P': 0.0,
                                      'APT-S': 0.0,
                                      'DB-A-P': 50121.21252296202,
                                      'DB-A-S': 50121.21252296202,
                                      'DB-F-P': 112216.5959502341,
                                      'DB-F-S': 112216.5959502341,
                                      'DB-M-P': 312434.6319652328,
                                      'DB-M-S': 312434.6319652328,
                                      'FPT-P': 71071.59098407856,
                                      'FPT-S': 71071.59098407856,
                                      'FWD-DEEP-P': 100131.41062440611,
                                      'FWD-DEEP-S': 100131.41062440613,
                                      'HEEL-P': 90150.66863764448,
                                      'HEEL-S': 90150.66863764448,
                                      'WING-A-P': 352444.16082558077,
                                      'WING-A-S': 352444.16082558077,
                                      'WING-F-P': 171669.3318400761,
                                      'WING-F-S': 171669.33184007605},
                   'withdraw_groups': [['WING-A-P', 'WING-A-S'], ['WING-F-P', 'WING-F-S']]}}


class _OperatingWitnesses(Mapping):
    """Select requalified ON witnesses while OFF retains the exact old object."""

    def __init__(self, historical):
        self._historical = historical

    def __iter__(self):
        return iter(self._historical)

    def __len__(self):
        return len(self._historical)

    def __getitem__(self, key):
        from stowage.rules.catalogue import rule_freeze_v1_enabled
        if not rule_freeze_v1_enabled() or key not in _D64_REFITS:
            return self._historical[key]
        import hashlib
        import json
        from copy import deepcopy
        from stowage.vessel.fleet import _parameter_bytes
        from stowage.versions import component_versions

        specification = _D64_REFITS[key]
        name, condition = key.split(".")
        parameter_sha = hashlib.sha256(_parameter_bytes(name)).hexdigest()
        if parameter_sha != specification["parameter_sha256"]:
            raise ValueError(f"D64 {key} manual ballast witness requires requalification for changed tanks/parameters")
        previous = self._historical[key]
        result = deepcopy(previous)
        result["tank_masses_kg"] = dict(specification["tank_masses_kg"])
        audit = {field: deepcopy(value) for field, value in specification.items()
                 if field != "tank_masses_kg"}
        audit.update(version="D64-manual-operating-refit-v1",
            cargo_and_consumables_unchanged=True,
            historical_finisher_versions=deepcopy(previous["provenance"]["versions"]),
            moment_definition="Upright interpolation of the current declared tank soundings; full finite-angle checker independently requalifies the operating recipe.",
            selection="Offline bounded two-group withdrawal preserving ballast mass and longitudinal moment; non-HEEL paired fills remain symmetric.",
            historical_finisher_metadata="seed/time_limit describe the inherited witness lineage, not a new finisher invocation",
            qualification_test="tests/vessel/test_fleet_conditions_manual_d64.py::test_requalified_manual_passes_full_checker_without_cargo_or_fuel_changes")
        result["refit_audit"] = audit
        identity = dict(condition=key, parameter_sha256=parameter_sha,
                        tank_masses_kg=result["tank_masses_kg"],
                        historical_input_fingerprint=previous["input_fingerprint"],
                        refit_version=audit["version"])
        result["input_fingerprint"] = hashlib.sha256(json.dumps(identity,sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        result["source"] = (f"D64-manual-operating-refit-v1 {key} sha256:{result['input_fingerprint']}; "
                            + audit["qualification_test"])
        result["provenance"]["created_by"] = "vessel.fleet_manual_witness.D64_operating_refit"
        result["provenance"]["versions"] = component_versions()
        result["provenance"]["parents"] = sorted(set([
            *result["provenance"]["parents"], previous["source"],
            f"params/fleet/{name}.toml sha256:{parameter_sha}",
            result["source"], "APPROXIMATION: synthetic operating ballast refit; no approved loading-manual claim",
            audit["historical_finisher_metadata"]]))
        return result


# APPROXIMATION: D93 static synthetic operating witness, measured by the
# independent complete checker in the cited report; not a finisher run.
# Preserve the historical .38 cargo reserve; this map supplies operating
# ballast only. FPT0.50 includes both FPT and FWD-DEEP pairs.
CURRENT_WITNESSES['feeder_500.C2'] = {'input_fingerprint': 'd88abcd9e2597dec8512aab6f2120b912870d0162f5ed4878fa5d474b9a2382b',
 'parameter_sha256': '797886f10c5e858a123ae61f0550a06be6e7d620c668544a215cf3f9a6b25281',
 'ballast_label': 'D93_declared_DB+HEEL+FPT0.50',
 'tank_masses_kg': {'DB-A-P': 50121.21252296202,
                    'DB-A-S': 50121.21252296202,
                    'DB-M-P': 312434.6319652328,
                    'DB-M-S': 312434.6319652328,
                    'DB-F-P': 112216.5959502341,
                    'DB-F-S': 112216.5959502341,
                    'WING-A-P': 0.0,
                    'WING-A-S': 0.0,
                    'HEEL-P': 180301.33727528897,
                    'HEEL-S': 180301.33727528897,
                    'WING-F-P': 0.0,
                    'WING-F-S': 0.0,
                    'FWD-DEEP-P': 50065.705312203056,
                    'FWD-DEEP-S': 50065.70531220306,
                    'FPT-P': 35535.79549203928,
                    'FPT-S': 35535.79549203928,
                    'APT-P': 0.0,
                    'APT-S': 0.0},
 'source': 'experiments/mvp/solve47/physics/manual-regression/REPORT.md; exact candidate from '
           'experiments/mvp/solve47/physics/manual-regression/fixtest.py '
           'sha256:704681a9e149e067c4412a0089cc32fb697c905815d2ce2b645b89577f90a34f',
 'seed': None,
 'time_limit_s': 0.0,
 'provenance': {'created_by': 'vessel.fleet_manual_witness.D93_declared_operating_witness',
                'seed': None,
                'parents': ['APPROXIMATION: synthetic D93 loading-manual ballast, not approved '
                            'operating instructions',
                            'DECLARATION: static half FPT and FWD-DEEP with full DB/HEEL; no new '
                            'finisher invocation',
                            'params/fleet/feeder_500.toml '
                            'sha256:797886f10c5e858a123ae61f0550a06be6e7d620c668544a215cf3f9a6b25281',
                            'experiments/mvp/solve47/physics/manual-regression/REPORT.md '
                            'sha256:18ec4a6acee467a8b0f054670952d1b2547dadd7d11c402be7986c85725cc54c',
                            'experiments/mvp/solve47/physics/manual-regression/fixtest.py '
                            'sha256:704681a9e149e067c4412a0089cc32fb697c905815d2ce2b645b89577f90a34f']}}


CURRENT_WITNESSES = _OperatingWitnesses(CURRENT_WITNESSES)
