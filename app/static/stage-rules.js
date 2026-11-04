/* Status aggregation shared by the stage page, the gauges and the video director, so they cannot disagree.
 * Fail-closed: only the literal checker statuses count as evidence. A not_run, a missing or an unknown status is
 * "unavailable", and nothing unavailable may render as a pass. Vocabulary: build_rules() in app/stage.py. */
(function () {
  'use strict';
  const num = (v) => typeof v === 'number' && isFinite(v);
  const BUCKET = new Map([['pass', 'pass'], ['fail', 'fail'], ['n/a', 'na']]);
  const RANK = { na: 0, pass: 1, unavailable: 2, fail: 3 };
  const bucket = (status) => BUCKET.get(status) || 'unavailable';
  const ruleBucket = (r) => (r && num(r.violations) && r.violations > 0 ? 'fail' : bucket(r && r.status));
  const empty = () => ({ pass: 0, fail: 0, unavailable: 0, na: 0, total: 0 });
  const add = (t, b, n = 1) => { t[b] += n; t.total += n; };

  // Worst bucket in a tally. Nothing to count is not a pass.
  function verdict(t) {
    if (t.fail) return 'fail';
    if (t.unavailable) return 'unavailable';
    if (t.pass) return 'pass';
    return t.na ? 'na' : 'unavailable';
  }
  function familyTally(f) {
    const t = empty(), rules = (f && f.rules) || [];
    for (const r of rules) add(t, ruleBucket(r));
    // A family with no rule rows is no evidence; a family status worse than its rows is kept, never overruled.
    const declared = f && f.status != null ? bucket(f.status) : null;
    if (!rules.length) add(t, declared === 'fail' ? 'fail' : 'unavailable');
    else if (declared && RANK[declared] >= RANK.unavailable && RANK[declared] > RANK[verdict(t)]) add(t, declared);
    return t;
  }
  function tally(families) {
    const t = empty();
    for (const f of families || []) {
      const ft = familyTally(f);
      for (const b of ['pass', 'fail', 'unavailable', 'na']) add(t, b, ft[b]);
    }
    return t;
  }
  const summary = (t) => `${t.pass} pass · ${t.fail} fail · ${t.unavailable} unavailable · ${t.na} not applicable`;
  function badge(t) {
    const applicable = t.total - t.na, v = verdict(t);
    if (v === 'fail') return { text: `FAILED ${t.fail}`, cls: 'fail' };
    if (v === 'unavailable') return { text: t.pass ? `UNAVAILABLE ${t.unavailable}/${applicable}` : 'NOT CHECKED', cls: 'warn' };
    if (v === 'pass') return { text: `${t.pass}/${applicable} PASS`, cls: 'ok' };
    return { text: 'N/A', cls: '' };
  }

  // Physics panel badge. Green needs both part verdicts to be literally true on a frame without an error.
  function physicsBadge(ph, f) {
    if (!ph) return { text: 'PENDING', cls: 'warn', title: '' };
    if (!f) return { text: 'NOT MEASURED', cls: 'warn', title: 'no keyframe at or before this lift' };
    if (f.error) return { text: 'NOT MEASURED', cls: 'warn', title: String(f.error) };
    if (f.stability_passed === false || f.strength_passed === false) {
      return { text: 'OUTSIDE LIMITS', cls: 'fail', title: [...(f.stability_violations || []), ...(f.strength_violations || [])].join('\n') };
    }
    if (f.stability_passed === true && f.strength_passed === true) return { text: 'WITHIN LIMITS', cls: 'ok', title: '' };
    return { text: 'NOT MEASURED', cls: 'warn', title: 'stability or strength verdict missing from this frame' };
  }

  // Shear / bending curve label: "within limits" needs limits for the plotted points and the strength part's verdict.
  function curveStatus(v) {
    const ys = v.ys || [];
    if (!ys.length) return { text: '', bad: false };
    let bad = false, points = 0, compared = 0;
    ys.forEach((y, i) => {
      if (!num(y)) return;
      points++;
      const lo = v.lo ? v.lo[i] : null, hi = v.hi ? v.hi[i] : null;
      if (num(lo) || num(hi)) compared++;
      if ((num(lo) && y < lo) || (num(hi) && y > hi)) bad = true;
    });
    if (bad) return { text: 'OUTSIDE LIMITS', bad };
    return { text: points && compared === points && v.passed === true ? 'within limits' : 'limits not checked', bad };
  }

  // Securing card status (bundle.securing, build_securing in app/stage.py), first match wins. Green needs a literal
  // passed === true; a ratio over the limit is never hidden by a missing state, and a part that did not run is never
  // called "over limit".
  function securingState(sc) {
    const notChecked = (why) => ({ state: 'not_checked', label: `NOT CHECKED: ${why}`, cls: 'warn', reason: why });
    if (!sc) return notChecked('no securing result in this bundle');
    const limit = num(sc.limit) ? sc.limit : 1;
    const over = (sc.checks || []).filter((c) => num(c.ratio) && c.ratio > limit + 1e-10);
    if (over.length) {
      const w = over.reduce((a, c) => (c.ratio > a.ratio ? c : a));
      return { state: 'over_limit', label: `OVER LIMIT: ${w.label} ${w.ratio.toFixed(2)}`, cls: 'bad', reason: sc.reason || `${w.label} over the limit` };
    }
    if (sc.state === 'violation' || (num(sc.violations) && sc.violations > 0)) {
      const rule = (sc.violation_rules || [])[0] || 'lashing rule';
      return { state: 'violation', label: `VIOLATION: ${rule}`, cls: 'bad', reason: sc.reason || rule };
    }
    if (sc.state === 'not_checked' || sc.available !== true || !(sc.checks || []).length) return notChecked(sc.reason || 'lashing part did not run');
    if (sc.passed === true) return { state: 'ok', label: 'ALL WITHIN LIMIT', cls: 'ok', reason: null };
    return notChecked(sc.reason || 'no checker verdict for securing');
  }

  // Header tiles (bundle.kpi): baseline minus this plan, positive = this plan did less. The direction is a word, so a
  // tile can never read as "$−X saved". Every value is for this slice's own load list, never a whole call.
  function kpiTiles(k) {
    const blank = { value: '—', worse: false, available: false };
    const word = (v, txt, less, more, pre = '') => (num(v) ? { available: true, worse: v < 0, value: `${pre}${txt} ${v > 0 ? less : v < 0 ? more : '· tie'}` } : blank);
    const intOf = (v) => Math.round(Math.abs(v)).toLocaleString('en-US');
    const hours = (v) => { const a = Math.abs(v); return a > 0 && a < 0.005 ? '<0.01' : a.toFixed(a < 1 ? 2 : 1); };
    // Sub-line sign = this plan minus two-step, to match the tile's name: + is more lifts than two-step.
    const delta = (saved) => (saved > 0 ? `−${intOf(saved)}` : saved < 0 ? `+${intOf(saved)}` : '0');
    if (!k) return { moves: { ...blank, sub: 'not available' }, hours: blank, usd: blank, thb: blank };
    const moves = word(k.moves_saved, num(k.moves_saved) ? intOf(k.moves_saved) : '', 'fewer', 'more');
    const split = num(k.lifts_saved_now) && num(k.later_lifts_saved) ? `${delta(k.lifts_saved_now)} now · ${delta(k.later_lifts_saved)} later ports · ` : '';
    return {
      moves: { ...moves, sub: moves.available ? `${split}this slice` : 'not available' },
      hours: word(k.crane_hours, num(k.crane_hours) ? hours(k.crane_hours) : '', 'fewer', 'more'),
      usd: word(k.usd_per_call, num(k.usd_per_call) ? `$${intOf(k.usd_per_call)}` : '', 'less', 'more'),
      thb: word(k.thb_per_call, num(k.thb_per_call) ? `฿${intOf(k.thb_per_call)}` : '', 'less', 'more', '≈ '),
    };
  }

  // docs/headline.md: the long form of the two-step label, kept on every panel that uses the short form.
  const TWO_STEP_LONG = 'a simple two-step planner, modelled from one field expert\'s description';
  window.StageRules = { TWO_STEP_LONG, bucket, familyTally, tally, verdict, summary, badge, physicsBadge, curveStatus, securingState, kpiTiles };
})();
