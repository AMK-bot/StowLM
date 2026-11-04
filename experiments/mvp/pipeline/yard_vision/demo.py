"""Build a static, network-free player from recorded generated cases."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from .freeze import HERE
from .render import overlay_segments, render_yard


def build_demo():
    manifest = json.loads((HERE / 'fixtures/frozen.json').read_text())
    rows = []
    for case in manifest['cases']:
        directory = HERE / 'fixtures' / case['id']
        result_path = directory / 'clean_render.yard_read.json'
        if not result_path.exists():
            continue
        result = json.loads(result_path.read_text())
        evidence = json.loads((directory / 'clean_render.evidence.json').read_text())
        score = json.loads((directory / 'clean_render.score.json').read_text())
        base = json.loads((directory / 'base.json').read_text())
        truth = json.loads((directory / 'truth.json').read_text())
        with Image.open(directory / 'clean.png') as image:
            width, height = image.size
        segments = []
        for segment in evidence.get('segments', []):
            box = segment['box']
            sw, sh = segment.get('image_size', [width, height])
            segments.append({'bbox': [v * (width / sw if i % 2 == 0 else height / sh) for i, v in enumerate(box)]})
        overlay_segments(directory / 'clean.png', segments, directory / 'sam.png')
        if result['state']:
            render_yard(result['state'], base, directory / 'recovered.png', title='Recovered yard | Recorded SAM + Muse Spark')
        audit = json.loads((directory / 'pair_audit.json').read_text()) if (directory / 'pair_audit.json').exists() else None
        rows.append({**case, 'read': result, 'evidence': {k: evidence.get(k) for k in ('stacks', 'complete_view', 'quay_clear')},
                     'score': score, 'truth': truth, 'audit': audit})
    data = json.dumps(rows, ensure_ascii=False).replace('</', '<\\/')
    html = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Generated yard experiment — recorded replay</title><style>
body{font:16px system-ui;background:#0e202c;color:#eef5f8;margin:28px;max-width:1600px}button,select{font:inherit;padding:9px;background:#23475a;color:white;border:1px solid #7298ac;border-radius:6px}h1{font-size:28px}h2{font-size:18px;margin-bottom:6px}.note{color:#b7d1df}.warning{background:#65412c;padding:14px;border-radius:8px}img{width:100%;background:#d7dbd9;border-radius:8px;cursor:zoom-in}a{color:#9edbff}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}table{border-collapse:collapse;width:100%;margin:20px 0}td,th{padding:7px;text-align:left;border-bottom:1px solid #3c5969}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#173344;padding:14px;border-radius:8px}@media(max-width:800px){.grid{grid-template-columns:1fr}}</style>
<h1>From a generated yard picture to a proposed yard state</h1>
<p class="note">Recorded SAM 3.1 + Muse Spark observations. All images are generated; all cargo is synthetic. Replay uses no network, GPU or paid call. A complete read still requires the planner's confirmation.</p>
<select id="case" aria-label="Recorded case"></select> <button id="next">Next case</button><p id="summary"></p>
<div class="grid"><section><h2>Generated camera input</h2><a id="inputlink"><img id="input" alt="Generated yard input"></a></section><section><h2>SAM candidate boxes</h2><a id="samlink"><img id="sam" alt="SAM candidate box outlines"></a></section><section><h2>Recovered yard — recorded proposal</h2><a id="recoveredlink"><img id="recovered" alt="Recovered yard"></a><pre id="questions"></pre></section><section><h2>True yard — scoring only</h2><a id="truthlink"><img id="truth" alt="True synthetic yard"></a></section></div>
<table><thead><tr><th>Stack</th><th>True bottom → top</th><th>Read bottom → top</th></tr></thead><tbody id="comparison"></tbody></table>
<h2>Muse Image repaint trial</h2><p id="audit" class="warning"></p><a id="photolink"><img id="photo" alt="Generated Muse Image repaint"></a>
<p class="note">SAM boxes are candidates, not checker verdicts. Compressed masks are retained in the evidence; outlines show returned bounding boxes. Changed or unverifiable repaints are discarded before recovery scoring.</p>
<script type="application/json" id="cases">''' + data + '''</script><script>
const cases=JSON.parse(document.getElementById('cases').textContent); const select=document.getElementById('case');
function esc(v){return String(v??'?').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
cases.forEach((c,i)=>{const o=document.createElement('option');o.value=i;o.textContent=c.id+' · '+c.split;select.append(o)});
function image(id,path){document.getElementById(id).src=path;document.getElementById(id+'link').href=path;}
function show(){const c=cases[Number(select.value)],p='fixtures/'+c.id+'/'; const s=c.score;
document.getElementById('summary').textContent=c.id+' · '+c.split+' · '+c.read.status+' · SAM boxes '+s.box_count.observed+'/'+s.box_count.true+' · heights '+s.stack_height.correct+'/'+s.stack_height.total+' · IDs '+s.id.correct+'/'+s.id.total+' · silent wrong stacks '+s.silent_wrong_stacks+' · '+s.wall_s.toFixed(2)+' s · historical $'+s.cost_usd.toFixed(6);
image('input',p+'clean.png');image('sam',p+'sam.png');image('truth',p+'clean.png');
document.getElementById('recovered').hidden=!c.read.state; if(c.read.state)image('recovered',p+'recovered.png');
const q=document.getElementById('questions');q.hidden=!c.read.questions.length;q.textContent=c.read.questions.map(x=>x.prompt).join(String.fromCharCode(10));
const reads=new Map((c.evidence.stacks||[]).map(x=>[x.key,x]));document.getElementById('comparison').innerHTML=c.truth.stacks.map(t=>'<tr><td>'+esc(t.key)+'</td><td>'+t.boxes.map(esc).join(' → ')+'</td><td>'+(reads.get(t.key)?.boxes||[]).map(esc).join(' → ')+'</td></tr>').join('');
const a=c.audit;document.getElementById('audit').textContent=a?(a.preserved?'Preservation audit passed. ':'DISCARDED: ')+a.reason:'No repaint audit recorded.';
document.getElementById('photo').hidden=!a;if(a)image('photo',p+a.photo_file);
}select.onchange=show;document.getElementById('next').onclick=()=>{select.value=(Number(select.value)+1)%cases.length;show()};if(cases.length)show();
</script></html>'''
    (HERE / 'demo.html').write_text(html)
    return {'cases': len(rows), 'path': str(HERE / 'demo.html')}


if __name__ == '__main__':
    print(json.dumps(build_demo()))
