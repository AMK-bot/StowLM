"""Studio evidence provenance and projection parity; no browser or model runs."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app/static"
DATA = STATIC / "studio-data"
WITHHELD = {
    "docs/headline.md": "user-gated development claims contract is deliberately not shipped",
    "experiments/mvp/reader/v3/sealed/spark-summary.json": "sealed Reader Spark summary is deliberately withheld",
    "experiments/fleet/orders/studio/CONTRACT.md": "development Studio design contract is deliberately not shipped",
}


def node(script, payload=None):
    result = subprocess.run(
        ["node", "-e", script], input=json.dumps(payload), text=True,
        capture_output=True, cwd=ROOT, timeout=30, check=False,
    )
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)


def reread(source):
    """Independent reader: never call the builder's own source resolver."""
    path = (ROOT / source["path"]).resolve()
    assert path.is_relative_to(ROOT), path
    assert path.is_file(), path
    text = path.read_text()
    field = source["field"]
    if field.startswith("regex:"):
        match = re.search(field[6:], text, re.MULTILINE | re.DOTALL)
        assert match, source
        value = match.group(source.get("group", 1))
        if source.get("clean"):
            value = re.sub(r"\s+", " ", value.replace("**", "").replace("`", "")).strip()
        if source.get("type") == "float":
            value = float(value.replace(",", ""))
        return value
    value = json.loads(text)
    for token in field.lstrip("/").split("/") if field else []:
        key = token.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def figures(tree):
    if isinstance(tree, dict):
        if "value" in tree:
            yield tree
        for value in tree.values():
            yield from figures(value)
    elif isinstance(tree, list):
        for value in tree:
            yield from figures(value)


def sources(tree):
    if isinstance(tree, dict):
        if "path" in tree and "field" in tree:
            yield tree
        else:
            for value in tree.values():
                yield from sources(value)
    elif isinstance(tree, list):
        for value in tree:
            yield from sources(value)


class StudioEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import studio_data
        cls.builder = studio_data
        # Runtime checks use the public artifacts; regeneration has its own
        # independent builder comparison below. Bad public JSON must fail here.
        cls.payloads = {name: json.loads((DATA / f"{name}.json").read_text())
                        for name in ("evidence", "impact")}

    def require_development_sources(self, *paths):
        missing = [f"{path} ({WITHHELD[path]})" for path in paths
                   if not (ROOT / path).is_file()]
        if missing:
            self.skipTest("Development source check requires " + "; ".join(missing))

    def test_generated_data_matches_builder_and_is_deterministic(self):
        self.require_development_sources("docs/headline.md", "experiments/mvp/reader/v3/sealed/spark-summary.json")
        rebuilt = {"evidence": self.builder.build_evidence(), "impact": self.builder.build_impact()}
        snapshots = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in DATA.glob('*.json')}
        for name, expected in rebuilt.items():
            before = (DATA / f"{name}.json").read_bytes()
            self.assertEqual(json.loads(before), expected)
        with tempfile.TemporaryDirectory(prefix='studio-evidence-') as temporary:
            destination = Path(temporary)
            first = None
            for _ in range(2):
                result = subprocess.run([sys.executable, '-m', 'app.studio_data', '--output-dir', str(destination)],
                                        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                generated = {path.name: path.read_bytes() for path in destination.glob('*.json')}
                if first is not None:
                    self.assertEqual(generated, first)
                first = generated
                for name, expected in rebuilt.items():
                    serialized = json.dumps(expected, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
                    self.assertEqual(generated[f'{name}.json'], serialized.encode())
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in DATA.glob('*.json')}, snapshots)

    def test_every_figure_and_denominator_and_interval_rereads(self):
        self.require_development_sources("experiments/mvp/reader/v3/sealed/spark-summary.json")
        count = 0
        for name, payload in self.payloads.items():
            for figure in figures(payload):
                count += 1
                with self.subTest(data=name, source=figure.get("source")):
                    self.assertTrue({"value", "n", "interval", "label", "source"} <= figure.keys())
                    self.assertTrue(figure["label"])
                    self.assertEqual(figure["value"], reread(figure["source"]))
                    if figure["n"] is not None:
                        self.assertEqual(figure["n"], reread(figure["n_source"]))
                        self.assertGreater(figure["n"], 0)
                        self.assertLessEqual(figure["value"], figure["n"])
                    if figure["interval"] is not None:
                        self.assertEqual(figure["interval"], reread(figure["interval_source"]))
                        lo, hi = figure["interval"]
                        self.assertTrue(0 <= lo <= figure["value"] / figure["n"] <= hi <= 1)
        self.assertGreater(count, 100)

    def test_prose_and_charts_have_existing_resolvable_sources(self):
        self.require_development_sources("docs/headline.md")
        for payload in self.payloads.values():
            for source in sources(payload["text_sources"]):
                self.assertTrue(reread(source))
        for path in self.payloads["evidence"]["chart_sources"]:
            self.assertTrue((ROOT / path).is_file())

    def test_dashboard_cards_and_claim_labels(self):
        self.require_development_sources("docs/headline.md")
        data = self.payloads["evidence"]
        self.assertTrue(4 <= len(data["cards"]) <= 6)
        cards = {card["id"]: card for card in data["cards"]}
        self.assertEqual(set(cards), {"voyages", "reader", "mask", "binding", "ood"})
        headline = (ROOT / "docs/headline.md").read_text()
        for key in ("extension", "baseline", "partial_run", "partial_run_short"):
            self.assertIn(data["labels"][key], headline)
        self.assertIn(data["labels"]["original"].removeprefix("held-out maps, "), headline)
        self.assertEqual(data["scope"], "Synthetic ships and cargo, simulated port")
        for card in cards.values():
            self.assertTrue(card["show"])
            self.assertTrue(card["does_not"])
            self.assertTrue(card["series"])
        self.assertIn("two interrupted attempts", data["labels"]["original"])
        self.assertIn("fresh held-out extension, run after the freeze; not the pre-registered sealed claim", data["labels"]["extension"])
        self.assertEqual(cards["voyages"]["series"], cards["voyages"]["variants"]["original"])
        self.assertNotIn("secondary_pooled", json.dumps(data))

    def test_javascript_projection_matches_authoritative_python_to_cent(self):
        # docs/impact.md binds this v6 calculator. impact.py is archival v5 and
        # explicitly excludes annual dollar scaling of its old measured lifts.
        path = ROOT / "experiments/impact-estimate/impact_v6.py"
        spec = importlib.util.spec_from_file_location("studio_impact_reference", path)
        reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference)
        expected = reference.calculate()["pilot"]
        data = self.payloads["impact"]
        self.assertEqual(data["model"]["path"], str(path.relative_to(ROOT)))
        actual = node("""
          const fs = require('fs');
          const {project} = require('./app/static/studio-impact.js');
          const data = JSON.parse(fs.readFileSync(0, 'utf8'));
          process.stdout.write(JSON.stringify(Object.fromEntries(Object.entries(data.pilot).map(([name, row]) => [name, project(row.inputs)]))));
        """, data)
        self.assertEqual(set(actual), {"low", "mid", "high"})
        for case, result in actual.items():
            self.assertEqual(result["annual_calls"], expected[case]["annual_calls"])
            for period in ("per_call", "annual"):
                self.assertEqual(set(result[period]), set(expected[case][period]))
                for key, value in result[period].items():
                    with self.subTest(case=case, period=period, key=key):
                        self.assertAlmostEqual(value, expected[case][period][key], places=8)
                        self.assertEqual(round(value * 100), round(expected[case][period][key] * 100))
                        self.assertEqual(value, data["pilot"][case]["outputs"][period][key]["value"])

    def test_steps_register_without_dom_and_javascript_parses(self):
        registered = node("""
          const steps = [];
          global.window = {Studio: {step: s => steps.push(s)}};
          require('./app/static/studio-evidence.js');
          require('./app/static/studio-impact.js');
          process.stdout.write(JSON.stringify(steps.map(s => ({id:s.id, order:s.order, title:s.title, features:s.features, ship:s.ship, mount:typeof s.mount}))));
        """)
        self.assertEqual(registered, [
            {"id": "evidence", "order": 70, "title": "Evaluation", "features": [], "ship": False, "mount": "function"},
            {"id": "impact", "order": 80, "title": "Forward pilot", "features": [], "ship": False, "mount": "function"},
        ])
        for name in ("evidence", "impact"):
            result = subprocess.run(["node", "--check", str(STATIC / f"studio-{name}.js")],
                                    capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_fullpage_start_is_idempotent_and_autoplay_preserves_input(self):
        result = node(r"""
          const fs = require('fs');
          const payloads = JSON.parse(fs.readFileSync(0, 'utf8'));
          const walk = root => [root, ...root.children.flatMap(walk)];
          const fakeEvent = (type, target, extra = {}) => ({type, target, bubbles: true, defaultPrevented: false,
            preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...extra});
          class Element {
            constructor(tag) {
              this.tagName = tag.toLowerCase(); this.children = []; this.listeners = {};
              this.dataset = {}; this.style = {setProperty(key, value) { this[key] = value; }}; this.attributes = {}; this.className = '';
              this._text = ''; this.value = ''; this.hidden = false;
              this.classList = {
                add: (...items) => { this.className = [...new Set(this.className.split(/\s+/).filter(Boolean).concat(items))].join(' '); },
                remove: (...items) => { this.className = this.className.split(/\s+/).filter(item => !items.includes(item)).join(' '); },
                contains: item => this.className.split(/\s+/).includes(item),
                toggle: (item, force) => {
                  const add = force === undefined ? !this.classList.contains(item) : force;
                  this.classList[add ? 'add' : 'remove'](item); return add;
                }
              };
            }
            append(...nodes) { for (const item of nodes) { const node = typeof item === 'string' ? new Element('#text') : item; if (typeof item === 'string') node._text = item; node.parentNode = this; this.children.push(node); } }
            appendChild(node) { this.append(node); return node; }
            replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }
            remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(item => item !== this); }
            set innerHTML(html) {
              this.html = html; this.children = []; this._text = '';
              const stack = [this];
              for (const token of html.match(/<[^>]+>|[^<]+/g) || []) {
                if (token.startsWith('</')) { if (stack.length > 1) stack.pop(); }
                else if (token.startsWith('<')) {
                  if (token.startsWith('<!')) continue;
                  const match = token.match(/^<([\w-]+)/); if (!match) continue;
                  const el = new Element(match[1]);
                  for (const attr of token.slice(match[0].length).matchAll(/([\w:-]+)(?:="([^"]*)"|='([^']*)'|=([^\s>]+))?/g)) el.setAttribute(attr[1], attr[2] ?? attr[3] ?? attr[4] ?? '');
                  stack.at(-1).append(el);
                  if (!/\/>$/.test(token) && !['input','br','hr','img','meta','link'].includes(el.tagName)) stack.push(el);
                } else { const el = new Element('#text'); el._text = token; stack.at(-1).append(el); }
              }
            }
            get innerHTML() { return this.html || this.children.map(el => el.innerHTML).join(''); }
            get firstElementChild() { return this.children.find(el => el.tagName !== '#text'); }
            get textContent() { return this._text + this.children.map(el => el.textContent).join(''); }
            set textContent(value) { this._text = String(value); this.children = []; }
            setAttribute(key, value) {
              this.attributes[key] = String(value);
              if (key === 'class') this.className = String(value);
              else if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = String(value);
              else if (['type', 'min', 'max', 'step', 'value', 'id'].includes(key)) this[key] = String(value);
            }
            getAttribute(key) { return this.attributes[key] ?? null; }
            addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
            removeEventListener(type, callback) { this.listeners[type] = (this.listeners[type] || []).filter(fn => fn !== callback); }
            dispatchEvent(event) {
              if (!event.target) Object.defineProperty(event, 'target', {value: this, configurable: true});
              for (const fn of this.listeners[event.type] || []) fn(event);
              if (event.bubbles && !event.stopped && this.parentNode) this.parentNode.dispatchEvent(event);
              return !event.defaultPrevented;
            }
            matches(selector) {
              if (selector.startsWith('.')) return this.classList.contains(selector.slice(1));
              const attr = selector.match(/^\[([^=\]]+)(?:="?([^"\]]+)"?)?\]$/);
              if (attr) return attr[2] === undefined ? this.getAttribute(attr[1]) !== null : this.getAttribute(attr[1]) === attr[2];
              return this.tagName === selector.toLowerCase();
            }
            querySelectorAll(selector) { return walk(this).slice(1).filter(el => selector.split(',').some(part => el.matches(part.trim()))); }
            querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
            closest(selector) { for (let el = this; el; el = el.parentNode) if (selector.split(',').some(part => el.matches(part.trim()))) return el; return null; }
            contains(node) { return walk(this).includes(node); }
            getBoundingClientRect() { return {left: 0, top: 0, width: 400, height: 32, right: 400, bottom: 32}; }
            focus() { this.dispatchEvent(new Event('focus')); }
          }
          const steps = [];
          global.document = {createElement: tag => new Element(tag)};
          global.window = {Studio: {step: step => steps.push(step)}, addEventListener() {}, removeEventListener() {}};
          require('./app/static/studio-evidence.js'); require('./app/static/studio-impact.js');
          (async () => {
            const records = [];
            for (const step of steps) {
              const panel = new Element('section');
              const calls = {done: 0, wait: 0, drag: 0, load: 0, log: 0, centers: []};
              let autoplay = false;
              const autoplayDisclosures = [], autoplayClicks = [];
              const disclosuresClosed = () => walk(panel).filter(el => el.tagName === 'details').every(el => !el.open);
              const annual = () => walk(panel).find(el => el.dataset.field === 'annual_calls');
              const hero = () => walk(panel).find(el => el.classList.contains('st-impact-amount'));
              let atFirstDrag;
              const ctx = {
                data: name => { calls.load++; return Promise.resolve(payloads[name]); },
                center: node => calls.centers.push(node === null ? null : 'non-null'), done: () => calls.done++,
                log: () => calls.log++, wait: async ms => {
                  calls.wait += ms;
                  if (autoplay && step.id === 'evidence') autoplayDisclosures.push(disclosuresClosed());
                },
                drag: async () => { calls.drag++; if (atFirstDrag === undefined) atFirstDrag = Number(annual().value); },
                click: async el => {
                  if (!el) throw new Error('Missing autoplay target');
                  if (autoplay) autoplayClicks.push(el.tagName);
                  el.dispatchEvent(fakeEvent('click', el));
                },
              };
              const view = step.mount(panel, {}, ctx);
              if (typeof view.start !== 'function') throw new Error(step.id + ' lacks start');
              // Direct icon entry and shell-start may converge on the same view.
              await Promise.all([view.enter(), view.start(), view.start()]);
              await view.enter();
              const record = {id: step.id, loads: calls.load, done: calls.done, logs: calls.log, centers: calls.centers};
              record.grid = walk(panel).some(el => el.classList.contains('st-grid2'));
              record.cards = walk(panel).filter(el => el.classList.contains('st-card') && el.classList.contains('big')).length;
              record.disclosures = walk(panel).filter(el => el.tagName === 'details' && el.classList.contains('st-disclosure')).length;
              record.pageHeader = walk(panel).some(el => el.classList.contains('st-page-header'));
              record.pageTitle = walk(panel).some(el => el.classList.contains('st-page-title'));
              record.pageSubtitle = walk(panel).some(el => el.classList.contains('st-page-subtitle'));
              if (step.id === 'impact') {
                record.projectionLine = walk(panel).some(el => el.classList.contains('st-meta') && el.textContent === 'Projection, not a result');
                const chart = walk(panel).find(el => el.classList.contains('st-impact-components'));
                record.initialChart = chart.querySelector('svg').getAttribute('aria-label');
                record.initialChartWidth = chart.querySelector('rect').getAttribute('width');
                record.initialAmount = hero().textContent;
                record.initialRangeStyle = {...annual().style};
                annual().value = payloads.impact.pilot.mid.inputs.annual_calls.value;
                annual().dispatchEvent(new Event('input'));
                record.customAmount = hero().textContent;
                await view.start();
                record.afterRepeatedStart = Number(annual().value);
                record.sliders = walk(panel).filter(el => el.tagName === 'input').length;
                record.inertInputs = walk(panel).filter(el => el.tagName === 'input' && (el.disabled || Number(el.min) === Number(el.max))).length;
                record.fixedCharge = walk(panel).find(el => el.dataset.fixedField === 'move_charge_usd').textContent;
                await view.autoplay(ctx);
                record.atFirstDrag = atFirstDrag;
                record.finalAmount = hero().textContent;
                record.finalRangeStyle = {...annual().style};
                record.drag = calls.drag; record.duration = calls.wait;
                record.revenueCheck = walk(panel).find(el => el.classList.contains('st-impact-global-check')).textContent;
                record.finalChart = chart.querySelector('svg').getAttribute('aria-label');
                record.finalChartWidth = chart.querySelector('rect').getAttribute('width');
                record.scopeFooter = walk(panel).find(el => el.tagName === 'footer').textContent;
              } else {
                record.hasCharts = walk(panel).some(el => el.tagName === 'svg');
                record.scope = panel.textContent.includes(payloads.evidence.scope);
                record.actorTiles = panel.querySelectorAll('[data-evidence-card]').map(card => ({
                  id: card.dataset.evidenceCard,
                  reader: !!card.querySelector('.st-evidence-icon-reader'),
                  checker: !!card.querySelector('.st-evidence-icon-checker'),
                  chartActor: walk(card).filter(el => el.classList.contains('st-evidence-plot')).some(plot => plot.className.includes('icon-reader') || plot.className.includes('icon-checker')),
                }));
                const card = panel.querySelector('[data-evidence-card="voyages"]');
                const disclosure = card.querySelector('details');
                const summary = card.querySelector('summary');
                const heading = card.querySelector('h2');
                record.summary = summary.textContent;
                const states = [];
                card.dispatchEvent(new Event('mouseenter')); states.push(disclosure.open === true);
                card.dispatchEvent(new Event('mouseleave')); states.push(disclosure.open === false);
                heading.dispatchEvent(fakeEvent('click', heading)); states.push(disclosure.open === true);
                card.dispatchEvent(new Event('mouseleave')); states.push(disclosure.open === true);
                heading.dispatchEvent(fakeEvent('click', heading)); states.push(disclosure.open === false);
                card.dispatchEvent(fakeEvent('keydown', card, {key: 'Enter'})); states.push(disclosure.open === true);
                const space = fakeEvent('keydown', card, {key: ' '});
                card.dispatchEvent(space); states.push(disclosure.open === false && space.defaultPrevented);
                heading.dispatchEvent(fakeEvent('keydown', heading, {key: 'Enter'})); states.push(disclosure.open === false);
                summary.dispatchEvent(fakeEvent('click', summary)); states.push(disclosure.open === true);
                // Nested interactive controls must not toggle their containing card.
                const nestedButton = new Element('button'); const nestedIcon = new Element('span');
                nestedButton.append(nestedIcon); card.append(nestedButton);
                nestedIcon.dispatchEvent(fakeEvent('click', nestedIcon)); states.push(disclosure.open === true);
                summary.dispatchEvent(fakeEvent('click', summary)); states.push(disclosure.open === false);
                record.interactions = states;
                // Exercise a previously opened disclosure, then ensure autoplay
                // closes it and never reveals raw paths at any timed beat.
                disclosure.open = true;
                const hovered = [];
                for (const card of panel.querySelectorAll('[data-evidence-card]')) {
                  const dispatch = card.dispatchEvent.bind(card);
                  card.dispatchEvent = event => {
                    if (event.type === 'mouseenter') hovered.push(card.dataset.evidenceCard);
                    return dispatch(event);
                  };
                }
                autoplay = true;
                await view.autoplay(ctx);
                record.autoplayDisclosures = [...autoplayDisclosures, disclosuresClosed()];
                record.autoplayClicks = autoplayClicks;
                record.autoplayHovers = hovered;
                record.sublines = walk(panel).filter(el => el.classList.contains('st-card-sub') && !el.closest('details')).map(el => el.textContent);
                record.duration = calls.wait;
              }
              view.leave(); await view.enter(); await view.start();
              record.loadsAfterRevisit = calls.load; record.doneAfterRevisit = calls.done;
              records.push(record);
            }
            process.stdout.write(JSON.stringify(records));
          })().catch(error => { process.stderr.write(error.stack); process.exitCode = 1; });
        """, self.payloads)
        evidence, impact = result
        for record in result:
            self.assertEqual(record['loads'], 1)
            self.assertEqual(record['done'], 1)
            self.assertEqual(record['logs'], 1)
            self.assertEqual(record['loadsAfterRevisit'], 1)
            self.assertEqual(record['doneAfterRevisit'], 1)
            self.assertTrue(record['centers'])
            self.assertTrue(all(center is None for center in record['centers']))
            self.assertTrue(12000 <= record['duration'] <= 20000)
            self.assertTrue(record['grid'])
            self.assertGreaterEqual(record['cards'], 2)
            self.assertGreaterEqual(record['disclosures'], 1)
            for key in ('pageHeader', 'pageTitle', 'pageSubtitle'):
                self.assertEqual(record[key], record['id'] == 'evidence')
        self.assertTrue(evidence['hasCharts'])
        self.assertTrue(evidence['scope'])
        self.assertEqual(evidence['summary'], 'Details')
        self.assertEqual(evidence['interactions'], [True] * 11)
        self.assertTrue(evidence['autoplayDisclosures'])
        self.assertTrue(all(evidence['autoplayDisclosures']))
        self.assertNotIn('summary', evidence['autoplayClicks'])
        self.assertTrue(all(tag == 'button' for tag in evidence['autoplayClicks']))
        self.assertEqual(evidence['autoplayHovers'], ['voyages', 'reader'])
        for line in evidence['sublines']:
            self.assertFalse(line.endswith('.'), line)
            self.assertEqual(line[:1], line[:1].upper(), line)
        self.assertTrue(impact['projectionLine'])
        inputs = self.payloads['impact']['pilot']['low']['inputs']
        mutable_inputs = [key for key in inputs if len({case['inputs'][key]['value'] for case in self.payloads['impact']['pilot'].values()}) > 1]
        self.assertEqual(impact['sliders'], len(mutable_inputs))
        self.assertEqual(impact['inertInputs'], 0)
        self.assertIn('$20.86', impact['fixedCharge'])
        for tile in evidence['actorTiles']:
            self.assertEqual(tile['reader'], tile['id'] in ('reader', 'mask', 'ood'))
            self.assertEqual(tile['checker'], tile['id'] in ('voyages', 'binding'))
            self.assertFalse(tile['chartActor'])
        self.assertEqual(impact['initialAmount'], '$1,656')
        self.assertEqual(impact['customAmount'], '$8,278')
        self.assertEqual(impact['afterRepeatedStart'], 260)
        self.assertEqual(impact['atFirstDrag'], 260)
        self.assertEqual(impact['finalAmount'], '$34,135')
        self.assertEqual(impact['drag'], 1)
        self.assertIn('world terminal revenue', impact['revenueCheck'])
        self.assertIn('$4.17', impact['initialChart'])
        self.assertIn('$31.29', impact['finalChart'])
        self.assertNotEqual(impact['initialChartWidth'], impact['finalChartWidth'])
        self.assertEqual(impact['scopeFooter'], self.payloads['impact']['scope'])
        self.assertNotEqual(impact['initialRangeStyle'], impact['finalRangeStyle'])

    def test_local_only_and_no_banned_claims(self):
        self.require_development_sources("docs/headline.md")
        headline = (ROOT / "docs/headline.md").read_text().lower()
        banned = ["solves real ships", "industry level", "industry grade", "production ready",
                  "beats spark", "beats navis", "learned sight", "learned physics", "beyond simple rules",
                  "instructions nobody wrote code for", "the job software can't do", "never guessed",
                  "fine-tuning cut moves", "stops wrong bays", "62 rules", "32 of 32"]
        paths = [STATIC / f"studio-{name}.{ext}" for name in ("evidence", "impact") for ext in ("js", "css")]
        paths += list(DATA.glob("*.json"))
        for path in paths:
            content = path.read_text().lower()
            self.assertNotRegex(content, r"https?://|(?<!:)//[\w.-]+\.[a-z]{2,}", str(path))
            for phrase in banned:
                self.assertIn(phrase, headline)
                self.assertNotIn(phrase, content, str(path))
        impact = self.payloads["impact"]
        self.assertEqual(impact["label"], "Projection")
        self.assertNotIn("capture_fraction", json.dumps(impact))
        self.assertNotIn("prize_high", impact["global"])
        self.assertTrue(impact["global"]["terminal_revenue_check"])
        self.assertTrue(impact["what_must_be_true"])
        self.assertTrue(impact["who_for"])

    def test_css_is_scoped_layout_only_and_uses_shared_visual_classes(self):
        self.require_development_sources("experiments/fleet/orders/studio/CONTRACT.md")
        contract = (ROOT / "experiments/fleet/orders/studio/CONTRACT.md").read_text()
        shared_css = (STATIC / 'studio.css').read_text()
        layout_properties = {
            'display', 'box-sizing', 'width', 'height', 'min-width', 'max-width', 'min-height', 'max-height',
            'margin', 'margin-top', 'margin-right', 'margin-bottom', 'margin-left',
            'padding', 'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
            'flex', 'flex-direction', 'flex-wrap', 'flex-basis', 'flex-grow', 'flex-shrink',
            'align-items', 'align-self', 'align-content', 'justify-content', 'justify-items', 'place-items',
            'gap', 'row-gap', 'column-gap', 'grid-template-columns', 'grid-template-rows', 'grid-column', 'grid-row',
            'position', 'top', 'right', 'bottom', 'left', 'inset', 'overflow', 'overflow-x', 'overflow-y',
            'white-space', 'overflow-wrap', 'word-break', 'text-align', 'order',
        }
        for name in ("evidence", "impact"):
            css = (STATIC / f"studio-{name}.css").read_text()
            for token in re.findall(r"var\((--[\w-]+)\)", css):
                self.assertTrue(token in contract or re.search(re.escape(token) + r'\s*:', css + shared_css), token)
            for selector in re.findall(r"\.([a-zA-Z][\w-]*)", css):
                self.assertTrue(selector.startswith(f"st-{name}-"), selector)
            without_comments = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
            if name == 'impact':
                # The leader explicitly requested custom native-range styling;
                # every other module rule remains layout-only.
                without_comments = re.sub(r'\.st-impact-range[^{}]*\{[^{}]*\}', '', without_comments)
            else:
                # v3 actor colour is confined to the small icon tile.
                without_comments = re.sub(r'\.st-evidence-icon-(?:tile|reader|checker)\s*\{[^{}]*\}', '', without_comments)
            properties = set(re.findall(r'([\w-]+)\s*:\s*[^;{}]+[;}]', without_comments))
            self.assertTrue(properties)
            self.assertLessEqual(properties, layout_properties)
            self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b|\brgba?\(")
            self.assertNotRegex(css, r'(?i)(?:linear|radial|conic)-gradient\(')

    def test_impact_native_ranges_use_theme_tokens_and_adaptive_thumbs(self):
        css = (STATIC / 'studio-impact.css').read_text()
        def rule(selector):
            declarations = [body for selectors, body in re.findall(r'([^{}]+)\{([^{}]+)\}', css)
                            if selector in [item.strip() for item in selectors.split(',')]]
            self.assertTrue(declarations, selector)
            return '\n'.join(declarations)
        for selector in ('.st-impact-range::-webkit-slider-runnable-track', '.st-impact-range::-moz-range-track'):
            declaration = rule(selector)
            self.assertRegex(declaration, r'height:\s*4px')
        self.assertIn('var(--st-surface)', rule('.st-impact-range-wrap::before'))
        self.assertIn('var(--st-brand)', rule('.st-impact-range-wrap::after'))
        for selector in ('.st-impact-range::-webkit-slider-thumb', '.st-impact-range::-moz-range-thumb'):
            declaration = rule(selector)
            self.assertRegex(declaration, r'width:\s*16px')
            self.assertRegex(declaration, r'height:\s*16px')
            self.assertIn('var(--st-bg)', declaration)
            self.assertIn('var(--st-line-strong)', declaration)
        self.assertIn('var(--st-brand)', rule('.st-impact-range::-moz-range-progress'))
        self.assertIn('--st-impact-range-fill', rule('.st-impact-range-wrap::after'))

    def test_actor_colour_is_scoped_to_icon_tiles(self):
        css = (STATIC / 'studio-evidence.css').read_text()
        self.assertIn('var(--st-c-reader)', css)
        self.assertIn('var(--st-c-checker)', css)
        for selector, declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', css):
            if re.search(r'(?<![-\w])(?:color|background)\s*:|--c\s*:', declarations):
                self.assertRegex(selector.strip(), r'^\.st-evidence-icon-(?:tile|reader|checker)$')


if __name__ == "__main__":
    unittest.main()
