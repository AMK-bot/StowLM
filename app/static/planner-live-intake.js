/* Live cargo intake. The call view is the only source of cargo and model results. */
(function () {
  'use strict';
  const ROLES = [['booking', 'Booking'], ['shipping_instructions', 'Shipping instructions'], ['vgm', 'VGM'], ['dg_declaration', 'DG declaration'], ['gate_slip', 'Gate slip'], ['reefer_list', 'Reefer list'], ['oog_list', 'OOG list'], ['amendment', 'Amendment'], ['correspondence', 'Correspondence'], ['unspecified', 'Unspecified']];
  window.Planner.stage({
    id: 'intake', mode: 'live', order: 10, title: 'Cargo', features: [],
    mount(panel, data, ctx) {
      const esc = ctx.esc;
      let call = ctx.call || null, pending = false, encodingPending = false, error = '', active = false, timer = null;
      let files = [], paste = '', pasteName = 'pasted-papers.txt', role = 'unspecified', bookingRef = '', complete = false;
      let answers = {}, shown = false, lastPublished = '', observedStart = null, previousCall = null;
      let statusView = null, statusChecking = true, statusError = '', statusRequest = 0;
      const paidPaperRequests = new Map();
      const concurrencyNotes = new Map();
      const openQuestionGroups = new Map();
      const openPaperGroups = new Map();
      const selectedGeneratorBoxes = new Set();
      let eligibleGeneratorBoxes = new Map();
      const q = (selector) => panel.querySelector(selector);
      const chip = (text, kind) => `<span class="pl-intake-chip" data-kind="${kind || ''}">${esc(text)}</span>`;
      const options = (selected) => ROLES.map(([id, label]) => `<option value="${id}"${id === selected ? ' selected' : ''}>${label}</option>`).join('');
      const roleLabel = (id) => (ROLES.find(([value]) => value === id) || ['', id || 'Unspecified'])[1];
      const number = (value, places) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(places == null ? 1 : places) : null;
      const advice = (value) => String(value == null ? '' : value).replace(/(?:^|[.;]?\s+)(?:check|see|consult)\s+(?:the\s+)?spend ledger\.?/gi, '; see spent and held in the API key panel.');
      function aggregateReadStage() {
        return [...((call && call.stages) || [])].reverse().find((stage) => stage.stage === 'intake' && !/ · booking |booking schedule|earlier booking reads/i.test(stage.who || '') && !['skipped', 'unavailable'].includes(stage.status));
      }
      function paidReason() {
        if (statusChecking) return 'Checking Meta Model API key and paid-call consent.';
        if (statusError) return `Cannot check Meta Model API key and paid-call consent: ${statusError}`;
        const key = statusView && statusView.key;
        if (!key || typeof key.present !== 'boolean' || typeof key.paid_calls_accepted !== 'boolean') return statusView && statusView.error ? `Meta Model API key status unavailable: ${statusView.error}` : 'Meta Model API key and paid-call consent status is unavailable. Open API key and paid calls to check setup.';
        if (key.present !== true) return 'No Meta Model API key: enter yours in the app, or put MODEL_API_KEY in .env (see .env.example).';
        if (key.paid_calls_accepted !== true) return 'Accept paid calls in the API key panel before using Muse Spark.';
        return null;
      }
      function acceptStatus(view) {
        ++statusRequest; statusView = view || null; statusChecking = false; statusError = ''; render();
      }
      async function refreshStatus() {
        const request = ++statusRequest; statusChecking = true; statusError = ''; render();
        try {
          const view = await ctx.fx.get('status');
          if (request === statusRequest) { statusView = view || null; statusChecking = false; render(); }
        } catch (err) {
          if (request === statusRequest) { statusView = null; statusChecking = false; statusError = err && err.message ? err.message : String(err); render(); }
        }
        return !paidReason();
      }
      function generatedPapers() {
        const out = new Map();
        for (const doc of (call && call.case && call.case.papers) || []) out.set(doc.path || doc.name, doc);
        for (const doc of (call && call.papers && call.papers.documents) || []) {
          if (doc.origin !== 'generated') continue;
          const key = doc.path || [...out.keys()].find((path) => String(path).split('/').pop() === doc.name && (out.get(path).booking_ref || null) === (doc.booking_ref || null)) || `${doc.booking_ref || ''}:${doc.name}`;
          const previous = out.get(key) || {};
          out.set(key, { ...previous, ...doc, path: doc.path || previous.path });
        }
        return [...out.values()];
      }
      function documentPath(doc) {
        const path = doc.path || 'papers/generated/' + doc.name;
        return path.startsWith('papers/generated/') && !path.split('/').includes('..') ? path : null;
      }
      function filesHTML() {
        const groups = new Map(), standalone = [];
        for (const doc of generatedPapers()) {
          if (!doc.booking_ref && (doc.path === 'papers/generated/load-list.csv' || doc.name === 'load-list.csv')) { standalone.push(doc); continue; }
          const key = doc.booking_ref ? 'booking:' + doc.booking_ref : 'unbooked';
          if (!groups.has(key)) groups.set(key, { ref: doc.booking_ref || '__unbooked', label: doc.booking_ref ? 'Booking ' + doc.booking_ref : 'Call papers', docs: [] });
          groups.get(key).docs.push(doc);
        }
        const paper = (doc) => {
          const path = documentPath(doc), name = doc.name || String(doc.path || '').split('/').pop();
          const url = path ? '/api/functional/calls/' + encodeURIComponent(call.id) + '/file/' + path.split('/').map(encodeURIComponent).join('/') : null;
          return `<div class="pl-intake-file"><span class="pl-intake-file-info"><b>${esc(name)}</b><span class="pl-intake-meta">Synthetic document · ${esc(roleLabel(doc.role))}</span></span>${url ? `<a class="pl-intake-file-state" href="${esc(url)}" download>Download</a>` : ''}</div>`;
        };
        return `<div class="pl-live-paper-files" data-live-paper-files>${standalone.length ? `<div class="pl-intake-files" data-live-load-list-paper>${standalone.map(paper).join('')}</div>` : ''}${[...groups].map(([key, group]) => {
          const suffix = group.ref.match(/-(B\d+)$/), short = suffix && suffix[1];
          const label = short && [...groups.values()].filter((item) => item.ref.endsWith('-' + short)).length === 1 ? 'Booking ' + short : group.label;
          return `<details class="pl-live-paper-booking" data-live-paper-booking="${esc(group.ref)}" data-live-paper-group-key="${esc(key)}" data-live-paper-call="${esc(call.id)}"${openPaperGroups.get(call.id + '\n' + key) ? ' open' : ''}><summary title="${esc(group.label)}">${esc(label)} · ${group.docs.length} papers</summary><div class="pl-intake-files">${group.docs.map(paper).join('')}</div></details>`;
        }).join('')}</div>`;
      }
      function rememberPaperGroups() {
        if (!call) return;
        panel.querySelectorAll('[data-live-paper-booking]').forEach((element) => {
          if (element.dataset.livePaperCall !== call.id) return;
          const open = typeof element.open === 'boolean' ? element.open : element.getAttribute('open') != null;
          openPaperGroups.set(call.id + '\n' + element.dataset.livePaperGroupKey, open);
        });
      }
      function lastIntakeStage() {
        const stages = [...((call && call.stages) || [])].reverse().filter((stage) => stage.stage === 'intake');
        return stages.find((stage) => !/ · booking /.test(stage.who || '')) || stages[0];
      }
      function unavailable() {
        const stage = lastIntakeStage();
        const availability = (statusView && statusView.availability && statusView.availability.spark) || (call && call.availability && call.availability.spark);
        if (stage && stage.status === 'unavailable' && !(statusView && statusView.availability && statusView.availability.spark && statusView.availability.spark.available === true)) return stage.notes || 'No reason was supplied.';
        return availability && availability.available === false ? availability.reason || 'No reason was supplied.' : null;
      }
      function intakeIndex() {
        const papers = (call && call.papers) || {};
        const index = papers.intake_index || papers.index || null;
        return Array.isArray(papers.bookings) && papers.bookings.length ? { ...(index || {}), bookings: papers.bookings } : index;
      }
      function indexIncomplete() {
        const index = intakeIndex();
        return !!(index && Array.isArray(index.bookings) && (!index.bookings.length || index.bookings.some((item) => !['done', 'needs_answers', 'kept', 'reused'].includes(item.status) && !(item.status == null && item.view && item.view.complete === true))));
      }
      function bookingReason(item) {
        const reason = item.reason || (item.error && typeof item.error === 'object' ? item.error.reason : item.error) || item.notes;
        if (reason && /wasn't sent:/.test(reason)) return advice(reason);
        const facts = bookingCapFacts(item);
        const refusal = capRefusalText(facts, item.booking_ref);
        if (refusal) return refusal;
        if (reason) return advice(reason);
        if (!['failed', 'unavailable'].includes(item.status)) return '';
        const stage = [...((call && call.stages) || [])].reverse().find((record) => record.stage === 'intake' && record.notes && (record.booking_ref != null ? String(record.booking_ref) : (String(record.who || '').split(' · booking ')[1] || '').split(' · ')[0]) === String(item.booking_ref == null ? 'no booking reference' : item.booking_ref));
        return stage ? advice(stage.notes) : '';
      }
      function bookingCapFacts(item) {
        const papers = (call && call.papers) || {}, intake = papers.intake || {};
        return item.cap_refusal || [papers.cap_refusal, intake.cap_refusal, call && call.cap].find((value) => value && value.booking_ref === item.booking_ref);
      }
      function capRefusalText(facts, bookingRef) {
        const reason = facts && typeof facts.reason === 'string' ? advice(facts.reason) : '';
        if (reason && /wasn't sent:|kept for a retry; nothing has been planned from a partial reading/.test(reason)) return reason;
        if (!facts || facts.sent !== false || facts.charged_usd !== 0) return reason;
        const ref = facts.booking_ref == null ? bookingRef : facts.booking_ref;
        if (ref == null || ![facts.spent_usd, facts.hold_usd, facts.cap_usd].every((value) => typeof value === 'number' && Number.isFinite(value) && value >= 0)) return reason;
        const hold = String(Number(facts.hold_usd.toFixed(6)));
        return `Booking ${ref} wasn't sent: $${facts.spent_usd.toFixed(2)} spent plus this call's worst-case hold of $${hold} would pass your $${facts.cap_usd.toFixed(2)} cap. Nothing was charged for it.`;
      }
      function concurrencyNote() {
        const stages = [...((call && call.stages) || [])].reverse();
        const stage = stages.find((item) => item.stage === 'intake' && !/ · booking /.test(item.who || '') && /concurr|parallel|at (?:a |the same )time|at once|simultaneous/i.test(item.notes || ''));
        if (stage && call) concurrencyNotes.set(call.id, stage.notes);
        return advice(stage ? stage.notes : call ? concurrencyNotes.get(call.id) || '' : '');
      }
      function retryBody() {
        if (!call) return null;
        if (paidPaperRequests.has(call.id)) return paidPaperRequests.get(call.id);
        const index = intakeIndex(), choice = index && index.choice;
        if (choice && typeof choice === 'object') return choice.use_generated_papers === true ? { use_generated_papers: true } : null;
        if (['generated', 'generated_papers', 'use_generated_papers'].includes(choice)) return { use_generated_papers: true };
        if (choice) return null;
        const docs = ((call.papers || {}).documents) || [];
        if (docs.some((doc) => doc.origin === 'uploaded' || doc.origin === 'pasted')) return null;
        if (docs.length && docs.every((doc) => doc.origin === 'generated')) return { use_generated_papers: true };
        return null;
      }
      function bookingRows() {
        const index = intakeIndex();
        if (index && Array.isArray(index.bookings)) return index.bookings;
        const intake = (call.papers || {}).intake || {};
        const progress = (call.papers || {}).progress || intake.progress || {};
        let bookings = Array.isArray(progress) ? progress : progress.bookings || intake.bookings || [];
        if (!Array.isArray(bookings) || !bookings.length) {
          bookings = ((call && call.stages) || []).filter((stage) => stage.stage === 'intake' && (stage.booking_ref || / · booking /.test(stage.who || ''))).map((stage) => ({ ...stage, booking_ref: stage.booking_ref || String(stage.who).split(' · booking ')[1].split(' · ')[0] }));
        }
        return bookings;
      }
      function progressHTML() {
        const latest = new Map();
        bookingRows().forEach((record) => latest.set(record.booking_ref || '', record));
        return [...latest.values()].map((record) => {
          const item = { ...(record.view || {}), ...record };
          const kept = item.kept === true || item.reused === true || ['kept', 'reused'].includes(item.status);
          const time = [item.read_wall_s, record.view && record.view.read_wall_s, item.wall_s, record.view && record.view.wall_s].find((value) => number(value) !== null);
          const cost = [item.read_cost_usd, record.view && record.view.read_cost_usd, item.cost_usd, record.view && record.view.cost_usd].find((value) => number(value) !== null);
          const reason = bookingReason(item), boxes = typeof item.boxes === 'number' ? item.boxes : record.view && record.view.boxes;
          return `<p class="pl-intake-meta" data-live-booking-progress="${esc(item.booking_ref || '')}" data-booking-status="${esc(item.status || '')}">Booking ${esc(item.booking_ref || 'unspecified')} · ${esc(item.status || (item.complete === true ? 'done' : 'pending'))}${typeof boxes === 'number' ? ' · ' + esc(boxes) + ' boxes' : ''}${number(time) !== null ? ' · ' + number(time) + ' s from start to answer, including any wait for spend room' : ''}${number(cost, 4) !== null ? ' · $' + number(cost, 4) : ''}${kept ? ' · kept from earlier read' : ''}${reason ? ' · ' + esc(reason) : ''}</p>`;
        }).join('');
      }
      function readTimingHTML(intake, count) {
        const sum = typeof intake.read_wall_s === 'number' ? intake.read_wall_s : intake.wall_s;
        const stage = aggregateReadStage();
        const elapsed = [intake.read_elapsed_s, intake.elapsed_s, stage && stage.wall_s].find((value) => number(value) !== null);
        const note = concurrencyNote(), match = note.match(/(?:up to\s+)?(\d+)\s+bookings?\s+at\s+(?:a|the same)\s+time/i);
        const concurrency = [intake.max_concurrency, intake.read_concurrency, intake.concurrency, match && Number(match[1])].find((value) => Number.isInteger(value) && value > 0);
        const total = Number.isInteger(intake.total_bookings) ? intake.total_bookings : intake.progress && Number.isInteger(intake.progress.total_bookings) ? intake.progress.total_bookings : count || null;
        const parts = [];
        if (number(elapsed) !== null) parts.push(`${number(elapsed)} s elapsed for Muse Spark's reading${total != null ? ' of ' + total + (total === 1 ? ' booking' : ' bookings') : ''}${concurrency != null ? ' (up to ' + concurrency + ' at a time)' : ''}`);
        if (number(sum) !== null) parts.push(`${number(sum)} s of booking time in total`);
        return parts.length ? `<p class="pl-intake-meta" data-live-read-timing>${esc(parts.join('; '))}; measured on this machine, this run</p>` : '';
      }
      function partialSummaryHTML(capFailure) {
        const rows = bookingRows();
        if (!rows.length) return '<p class="pl-intake-meta" data-live-partial-summary>Nothing has been planned from a partial reading.</p>';
        const kept = rows.filter((item) => ['done', 'needs_answers', 'kept', 'reused'].includes(item.status) || item.kept === true || item.reused === true);
        const counts = kept.map((item) => {
          const view = item.view || item;
          if (Array.isArray(view.questions)) return view.questions.length;
          return [view.question_count, view.open_questions, view.open_question_count].find((value) => Number.isInteger(value) && value >= 0);
        });
        const knownCounts = counts.filter((value) => typeof value === 'number');
        const questions = knownCounts.length === kept.length && kept.length ? `${knownCounts.filter((count) => count > 0).length} of those bookings still hold open questions (${knownCounts.reduce((sum, value) => sum + value, 0)} in all).` : '';
        const prefix = capFailure ? rows.map((item) => capRefusalText(bookingCapFacts(item), item.booking_ref)).find(Boolean) || '' : '';
        const serverFull = [prefix, ...rows.map(bookingReason), call && call.cap && call.cap.reason].find((value) => typeof value === 'string' && /kept for a retry; nothing has been planned from a partial reading/.test(value));
        const summary = serverFull || `${prefix ? prefix + ' ' : ''}${kept.length} other ${kept.length === 1 ? 'booking was read and is' : 'bookings were read and are'} kept for a retry; nothing has been planned from a partial reading.${capFailure ? ' Raise the cap in the API key panel to read the rest.' : ''}`;
        return `<section class="pl-intake-ready" data-live-partial-summary><p>${esc(advice(summary))}</p>${questions && !(serverFull && /questions/.test(serverFull)) ? `<p class="pl-intake-meta">${esc(questions)}</p>` : ''}</section>`;
      }
      function readDetailsHTML(intake) {
        if (!intake || intake.skipped) return '';
        const bookings = Array.isArray(intake.bookings) ? intake.bookings : [];
        const views = bookings.map((item) => item.view ? { ...item, ...item.view } : item);
        let readCost = intake.read_cost_usd;
        if (typeof readCost !== 'number' && views.length) {
          const costs = views.map((item) => typeof item.read_cost_usd === 'number' ? item.read_cost_usd : item.cost_usd);
          if (costs.every((cost) => typeof cost === 'number' && Number.isFinite(cost))) readCost = costs.reduce((sum, cost) => sum + cost, 0);
        }
        if (typeof readCost !== 'number' && !views.length) readCost = intake.cost_usd;
        const bookingLines = views.map((item) => {
          const time = typeof item.read_wall_s === 'number' ? item.read_wall_s : item.wall_s;
          return `<p class="pl-intake-meta" data-live-booking-read="${esc(item.booking_ref || item.id || '')}">Booking ${esc(item.booking_ref || item.id || 'unspecified')}${typeof item.boxes === 'number' ? ' · ' + esc(item.boxes) + ' boxes' : ''}${number(time) !== null ? ' · ' + number(time) + ' s from start to answer, including any wait for spend room' : ''}</p>`;
        }).join('');
        const answerTime = intake.answer_wall_s;
        return `<section class="pl-intake-ready pl-live-read-details" data-live-read-summary><b>Muse Spark · Meta Model API, cloud</b>${readTimingHTML(intake, views.length)}${number(readCost, 4) !== null ? `<p class="pl-intake-meta">$${number(readCost, 4)} · Muse Spark, this call</p>` : ''}${bookingLines}${bookingLines ? '<p class="pl-intake-meta">Read times measured on this machine, this run</p>' : ''}</section>${number(answerTime) !== null ? `<section class="pl-intake-ready pl-live-answer-details" data-live-answers-credit><b>Your answers · no model</b><p class="pl-intake-meta">${number(answerTime, 3)} s to merge your answers, no model call; measured on this machine, this run</p></section>` : ''}`;
      }
      function elapsed() {
        const stage = aggregateReadStage();
        const parsed = stage && stage.started_utc ? Date.parse(stage.started_utc) : NaN;
        const start = Number.isFinite(parsed) ? parsed : observedStart;
        const el = q('[data-live-elapsed]');
        if (el) el.textContent = start == null ? 'Elapsed time not yet supplied' : `${Math.max(0, (Date.now() - start) / 1000).toFixed(1)} s elapsed · measured on this machine, this run${Number.isFinite(parsed) ? '' : ' since this browser observed the read'}`;
      }
      function generatorEligibleBoxes(intake) {
        const eligible = new Map(), questions = (intake && intake.questions) || [], rows = (intake && intake.rows) || [];
        const boxes = new Set(questions.map((question) => question.box).filter((box) => typeof box === 'string' && box));
        boxes.forEach((box) => {
          const matching = rows.filter((row) => (row.id || row.box_id) === box);
          if (matching.length !== 1) return;
          const row = matching[0], open = questions.filter((question) => question.box === box);
          const allowed = open.every((question) => {
            const equipment = question.field === 'iso_type' && typeof row.iso_type === 'string' && question.text === `${box}: ${row.iso_type} needs equipment review; this intake supports general purpose and reefer groups.` && (question.reason == null || question.reason === 'unsupported_equipment');
            const oog = question.field === 'oog' && question.text === `${box}: listed OOG cargo requires verified dimensions; an absent model reading cannot declare it in gauge.` && (question.reason == null || question.reason === 'missing_oog_declaration');
            return equipment || oog;
          });
          if (allowed) eligible.set(box, questions.findIndex((question) => question.box === box));
        });
        return eligible;
      }
      function questionHTML(question, index) {
        const value = answers[question.id] == null ? '' : answers[question.id];
        const choices = question.choices;
        const field = Array.isArray(choices) && choices.length ? `<select data-question-id="${esc(question.id)}" id="pl-live-answer-${index}"><option value="">Choose an answer</option>${choices.map((choice) => {
          const v = choice && typeof choice === 'object' ? choice.value : choice;
          const label = choice && typeof choice === 'object' ? choice.label || choice.text || choice.value : choice;
          return `<option value="${esc(v)}"${String(v) === String(value) ? ' selected' : ''}>${esc(label)}</option>`;
        }).join('')}</select>` : `<input id="pl-live-answer-${index}" data-question-id="${esc(question.id)}" value="${esc(value)}">`;
        const generatorChoice = eligibleGeneratorBoxes.get(question.box) === index ? `<label class="pl-live-check"><input type="checkbox" data-live-generator-box="${esc(question.box)}"${selectedGeneratorBoxes.has(question.box) ? ' checked' : ''}${pending || encodingPending ? ' disabled' : ''}> Use the generator's record for this box (not read by a model)</label>` : '';
        return `<article class="pl-intake-question"><header class="pl-intake-meta">Planner review${question.booking_ref ? ' · Booking ' + esc(question.booking_ref) : ''}</header><label for="pl-live-answer-${index}">${esc(question.text)}</label>${question.box ? `<p class="pl-intake-meta">Box ${esc(question.box)}${question.field ? ' · ' + esc(question.field) : ''}</p>` : ''}${field}${generatorChoice}</article>`;
      }
      function questionGroup(question, intake) {
        if (typeof question.booking_ref === 'string' && question.booking_ref.trim()) return { key: 'booking:' + question.booking_ref, ref: question.booking_ref, label: 'Booking ' + question.booking_ref, named: true };
        const id = String(question.id || '');
        if (id.startsWith('merge::')) return { key: 'across', ref: '__across', label: 'Across bookings', named: false };
        const parts = id.split('::');
        if (parts.length === 3 && /^b\d{4,}$/.test(parts[1])) {
          const booking = (Array.isArray(intake.bookings) ? intake.bookings : []).find((item) => item.id === parts[1]);
          if (booking && typeof booking.booking_ref === 'string' && booking.booking_ref.trim()) return { key: 'booking:' + booking.booking_ref, ref: booking.booking_ref, label: 'Booking ' + booking.booking_ref, named: true };
          try {
            const prefix = decodeURIComponent(parts[0]);
            if (prefix && prefix !== parts[1]) return { key: 'booking:' + prefix, ref: prefix, label: 'Booking ' + prefix, named: true };
          } catch (_) { /* An invalid namespace supplies no booking identity. */ }
        }
        return { key: 'unspecified', ref: '__unspecified', label: 'Booking not specified', named: false };
      }
      function rememberQuestionGroups() {
        if (!call) return;
        panel.querySelectorAll('[data-live-question-booking]').forEach((element) => {
          if (element.dataset.liveQuestionCall !== call.id) return;
          const open = typeof element.open === 'boolean' ? element.open : element.getAttribute('open') != null;
          openQuestionGroups.set(call.id + '\n' + element.dataset.liveQuestionGroupKey, open);
        });
      }
      function questionsHTML(intake) {
        const questions = intake.questions || [], groups = new Map();
        questions.forEach((question, index) => {
          const group = questionGroup(question, intake);
          if (!groups.has(group.key)) groups.set(group.key, { ...group, questions: [] });
          groups.get(group.key).questions.push({ question, index });
        });
        const named = [...groups.values()].filter((group) => group.named).length;
        const unknown = [...groups.values()].filter((group) => !group.named).reduce((sum, group) => sum + group.questions.length, 0);
        const summary = named ? `${questions.length} question${questions.length === 1 ? '' : 's'} across ${named} booking${named === 1 ? '' : 's'}` : `${questions.length} question${questions.length === 1 ? '' : 's'} · ${groups.has('across') && !groups.has('unspecified') ? 'across bookings' : 'booking not specified'}`;
        return `<p class="pl-intake-meta" data-live-question-summary>${esc(summary)}</p>${named && unknown ? `<p class="pl-intake-meta">${unknown} questions are across bookings or have no booking reference.</p>` : ''}${[...groups.values()].map((group) => `<details class="pl-live-question-booking" data-live-question-booking="${esc(group.ref)}" data-live-question-group-key="${esc(group.key)}" data-live-question-call="${esc(call.id)}"${openQuestionGroups.get(call.id + '\n' + group.key) ? ' open' : ''}><summary>${esc(group.label)} · ${group.questions.length} questions</summary><div class="pl-live-question-cards">${group.questions.map(({ question, index }) => questionHTML(question, index)).join('')}</div></details>`).join('')}`;
      }
      function queuedHTML() {
        return files.map((doc, index) => `<article class="pl-intake-question"><b>${esc(doc.name)}</b><label>Paper role<select data-file-index="${index}" data-doc-field="role">${options(doc.role)}</select></label><label>Booking ref<input data-file-index="${index}" data-doc-field="booking_ref" value="${esc(doc.booking_ref || '')}"></label><label class="pl-live-check"><input type="checkbox" data-file-index="${index}" data-doc-field="complete_booking_list"${doc.complete_booking_list ? ' checked' : ''}> Complete list for this booking</label><button type="button" class="pl-intake-button" data-action="remove-paper" data-file-index="${index}">Remove</button></article>`).join('');
      }
      function inputHTML(disabled) {
        return `<div class="pl-live-paper-inputs"><p class="pl-intake-meta">You set each paper's role; the model never does</p><p class="pl-intake-meta">Synthetic papers only. Never upload real company or customer documents.</p><label>Upload cargo papers<input type="file" multiple data-live-field="files"${disabled}></label>${queuedHTML()}<label>Or paste paper text<textarea rows="5" data-live-field="paste" placeholder="Paste synthetic cargo papers">${esc(paste)}</textarea></label><label>Paper name<input data-live-field="paste-name" value="${esc(pasteName)}"></label><label>Paper role<select data-live-field="role">${options(role)}</select></label><label>Booking ref<input data-live-field="booking-ref" value="${esc(bookingRef)}"></label><label class="pl-live-check"><input type="checkbox" data-live-field="complete-booking-list"${complete ? ' checked' : ''}> Complete list for this booking</label><button type="button" class="pl-intake-button" data-action="read-uploaded"${disabled}>Read uploaded or pasted papers with Muse Spark</button></div>`;
      }
      function tableHTML(rows) {
        return `<div class="pl-intake-load-summary"><p class="pl-intake-meta">${esc(call.load_list.boxes == null ? rows.length : call.load_list.boxes)} boxes${rows.length < call.load_list.boxes ? ' · first ' + rows.length + ' shown' : ''}</p><button type="button" class="pl-intake-button pl-intake-toggle" data-action="loadlist" aria-expanded="${shown}">${shown ? 'Hide' : 'View'} load list</button></div><div class="pl-intake-load-table"${shown ? '' : ' hidden'}><table aria-label="This call's load list"><thead><tr><th>Box</th><th>Cargo</th><th>POD</th><th>Source</th></tr></thead><tbody>${rows.map((row) => {
          const kg = row.gross_mass_kg, weight = typeof kg === 'number' ? `${kg} kg` : typeof row.weight_t === 'number' ? `${row.weight_t} t` : '';
          const source = ['muse_spark', 'spark', 'intake'].includes(row.origin) ? 'Muse Spark' : ['generator', 'generator, not read by a model'].includes(row.origin) ? 'generator, not read by a model' : row.origin || (call.load_list.origin === 'intake' ? 'Muse Spark' : call.load_list.origin === 'generator' ? 'generator, not read by a model' : 'Source not supplied');
          return `<tr data-load-row="${esc(row.id || row.box_id)}"><td>${esc(row.id || row.box_id)}</td><td>${esc(row.length_ft || row.size_ft || '')}′${weight ? ' · ' + esc(weight) : ''}${row.dg ? ' · DG' : ''}${row.reefer ? ' · reefer' : ''}</td><td>${esc(row.pod || '')}</td><td>${esc(source)}</td></tr>`;
        }).join('')}</tbody></table></div>`;
      }
      function publish(intake, rows) {
        const accepted = intake && (intake.skipped === true || intake.complete === true);
        if (!accepted || (indexIncomplete() || call.state === 'reading_papers') && intake.skipped !== true || !call.load_list || !Array.isArray(rows)) return;
        const stamp = JSON.stringify([call.id, call.load_list.origin, rows]);
        if (stamp === lastPublished) return;
        lastPublished = stamp;
        const note = call.load_list.origin === 'intake' ? 'Read by Muse Spark from this call’s papers' : call.load_list.origin === 'generator' ? 'Generated load list, not read by a model' : call.load_list.origin || 'Load-list source was not supplied';
        if (ctx.loadlist && ctx.loadlist.setRows) ctx.loadlist.setRows(rows.map((row) => ({ ...row, box_id: row.box_id || row.id, size_ft: row.size_ft || row.length_ft, weight_t: row.weight_t == null && typeof row.gross_mass_kg === 'number' ? row.gross_mass_kg / 1000 : row.weight_t, dg_class: row.dg_class || (row.dg && (row.dg.imdg_class || row.dg.class)), status: 'to load' })), { title: 'Call load list', note });
        if (ctx.state) ctx.state.loadlist = rows;
        ctx.emit('loadlist:ready', rows);
        if (ctx.done) ctx.done();
      }
      function render() {
        rememberQuestionGroups();
        rememberPaperGroups();
        if (!call || !call.case) {
          panel.innerHTML = '<section class="pl-intake-panel pl-live-intake"><p class="pl-intake-meta">Create a ship and call first.</p></section>';
          if (ctx.status) ctx.status('Waiting for a call');
          return;
        }
        const intake = (call.papers || {}).intake, rows = (call.load_list && call.load_list.rows) || [];
        eligibleGeneratorBoxes = call.state === 'needs_answers' ? generatorEligibleBoxes(intake) : new Map();
        for (const box of selectedGeneratorBoxes) if (!eligibleGeneratorBoxes.has(box)) selectedGeneratorBoxes.delete(box);
        const bookings = bookingRows(), unfinished = indexIncomplete() && !(intake && intake.skipped);
        const hasSparkRead = !!(intake && !intake.skipped && (intake.mode === 'live' || intake.model || intake.receipt_sha256)) || bookings.some((item) => ['done', 'needs_answers', 'kept', 'reused'].includes(item.status) || item.view && item.view.mode === 'live') || ((call && call.stages) || []).some((stage) => stage.stage === 'intake' && stage.model && stage.status === 'done' && !['generated', 'skipped'].includes(stage.mode));
        const reading = call.state === 'reading_papers', needsAnswers = call.state === 'needs_answers';
        if (reading && observedStart == null) observedStart = Date.now();
        if (!reading) observedStart = null;
        const reason = advice(unavailable());
        const mayInput = !reading && (call.state === 'needs_papers' || call.state === 'failed');
        const keyReason = paidReason();
        const disabled = pending || encodingPending || !!reason || !!keyReason || !mayInput ? ' disabled' : '';
        let status = advice(call.message) || 'Waiting for cargo papers';
        if (intake && intake.skipped) status = `${call.load_list && call.load_list.boxes != null ? call.load_list.boxes : rows.length} boxes from the generated load list (not read by a model)`;
        else if (intake && intake.complete && !unfinished) {
          const boxes = call.load_list && call.load_list.boxes != null ? call.load_list.boxes : intake.boxes == null ? rows.length : intake.boxes;
          const origin = call.load_list && call.load_list.origin;
          status = origin === 'intake' ? `${boxes} boxes read by Muse Spark` : origin === 'generator' ? `${boxes} boxes from the generated load list (not read by a model)` : `${boxes} boxes · ${origin || 'Load-list source was not supplied'}`;
        }
        else if (needsAnswers) status = `${(intake && intake.questions || []).length} cargo questions to review`;
        else if (reading) status = 'Muse Spark is reading the papers';
        if (reading) status = 'Muse Spark is reading the papers';
        if (unfinished && !reading) status = 'Cargo paper readings are incomplete; review the bookings below';
        const liveError = lastIntakeStage();
        const failure = !reason && liveError && liveError.status === 'failed' ? advice(liveError.notes || call.message) : '';
        const note = concurrencyNote();
        const canRetry = mayInput && (unfinished || liveError && ['failed', 'unavailable'].includes(liveError.status));
        const retryPayload = retryBody();
        const retryDisabled = pending || encodingPending || !!keyReason || !retryPayload ? ' disabled' : '';
        const hasCapFacts = bookings.some((item) => { const facts = bookingCapFacts(item); return facts && facts.sent === false && facts.charged_usd === 0 && [facts.spent_usd, facts.hold_usd, facts.cap_usd].every((value) => typeof value === 'number' && Number.isFinite(value) && value >= 0); });
        const capFailure = hasCapFacts || /spend(?:ing)?\s+cap|worst-case hold|\bcap\b.{0,45}(?:exceed|reach|exhaust)|(?:exceed|reach|exhaust).{0,45}\bcap\b/i.test([error, reason, failure, call.message, ...bookings.map(bookingReason)].filter(Boolean).join(' '));
        panel.innerHTML = `<section class="pl-intake-panel pl-live-intake"><div class="pl-intake-provenance">${chip('Synthetic ship and cargo, simulated port', 'synthetic')}${intake && intake.skipped ? chip('Generated load list · software, no model call', 'generated') : hasSparkRead ? chip('Muse Spark · Meta Model API, cloud', 'spark') : ''}</div>${mayInput && keyReason ? `<div class="pl-live-key-gate"><p class="pl-intake-message" role="status">${esc(keyReason)}</p><button type="button" class="pl-intake-button" data-action="open-key-panel">API key and paid calls</button></div>` : ''}${reason ? `<p class="pl-intake-message" role="status">Muse Spark unavailable: ${esc(String(reason).replace(/\.$/, ''))}. Nothing was replayed in its place.</p>` : ''}${failure ? `<p class="pl-intake-message" role="status">${esc(failure)}</p>` : ''}${reading || bookings.length ? `<div class="pl-live-intake-progress" role="status"><b>${reading ? 'Muse Spark is reading the papers' : 'Cargo paper readings'}</b>${reading ? '<p class="pl-intake-meta" data-live-elapsed></p>' : ''}${note ? `<p class="pl-intake-meta" data-live-intake-concurrency>${esc(note)}</p>` : ''}${progressHTML()}${unfinished && !(intake && intake.skipped) ? readTimingHTML(intake || {}, bookings.length) : ''}</div>` : ''}${canRetry ? `${partialSummaryHTML(capFailure)}<div class="pl-live-intake-retry"><button type="button" class="pl-intake-button pl-intake-primary" data-action="retry-failed-bookings"${retryDisabled}>Retry the failed bookings</button>${!retryPayload ? '<p class="pl-intake-meta">Upload or paste the same papers again to retry this reading. Their exact request is not retained in this browser.</p>' : ''}</div>` : ''}${capFailure ? '<button type="button" class="pl-intake-button" data-action="raise-spend-cap">Raise the spend cap</button>' : ''}${mayInput ? `<div class="pl-live-generated-actions">${generatedPapers().length ? `<button type="button" class="pl-intake-button pl-intake-primary" data-primary data-action="read-generated"${disabled}>Read these papers with Muse Spark</button>` : ''}<button type="button" class="pl-intake-button" data-action="use-generated-load-list"${pending || encodingPending ? ' disabled' : ''}>Use the generated load list, not read by a model</button>${generatedPapers().length ? `<p class="pl-intake-meta">Clean synthetic papers: a no-model parser could read these too. Upload messier synthetic papers (scans, photos, papers that disagree) to test Muse Spark's intake.</p>` : ''}</div>` : ''}${needsAnswers && intake ? `<div class="pl-live-intake-questions"><p class="pl-intake-meta" data-live-answer-review-credit>Your answers · no model</p>${questionsHTML(intake)}${eligibleGeneratorBoxes.size ? `<button type="button" class="pl-intake-button" data-action="use-generator-records"${pending || encodingPending || !selectedGeneratorBoxes.size ? ' disabled' : ''}>Use generator records for ${selectedGeneratorBoxes.size} boxes</button>` : ''}<button type="button" class="pl-intake-button pl-intake-primary" data-action="submit-answers"${pending || encodingPending ? ' disabled' : ''}>Send answers</button><button type="button" class="pl-intake-button" data-action="use-generated-load-list"${pending || encodingPending ? ' disabled' : ''}>Use the generated load list, not read by a model</button><p class="pl-intake-meta">Spark's reading is set aside; the generator's load list is planned instead</p></div>` : ''}${filesHTML()}${mayInput && !reason ? inputHTML(disabled) : ''}${!unfinished ? readDetailsHTML(intake) : ''}${intake && (intake.complete && !unfinished || intake.skipped) ? `<section class="pl-intake-ready"><b class="pl-intake-ready-label">${esc(status)}</b></section>` : ''}${intake && rows.length && (!unfinished || intake.skipped) ? tableHTML(rows) : ''}${error ? `<p class="pl-intake-message" role="alert">${esc(error)}</p>` : ''}</section>`;
        bind(); elapsed();
        if (ctx.status) ctx.status(status);
        publish(intake, rows);
      }
      function update(view) {
        if (!view) return;
        if (previousCall !== view.id) {
          files = []; paste = ''; bookingRef = ''; answers = {}; error = ''; lastPublished = ''; observedStart = null; shown = false; openQuestionGroups.clear(); openPaperGroups.clear(); selectedGeneratorBoxes.clear(); eligibleGeneratorBoxes = new Map();
          previousCall = view.id;
        }
        call = view; render();
      }
      async function post(step, body) {
        if (pending || encodingPending || !call || !call.id) return;
        const paidPapers = step === 'papers' && body.use_generated_load_list !== true && !('use_generator_records_for' in body);
        pending = true; error = ''; const id = call.id; render();
        try {
          if (paidPapers && !await refreshStatus()) {
            if (call && call.id === id) error = paidReason();
            return;
          }
          if (!call || call.id !== id) return;
          if (paidPapers) paidPaperRequests.set(id, JSON.parse(JSON.stringify(body)));
          const view = await ctx.fx.post(`calls/${encodeURIComponent(id)}/${step}`, body);
          if (call && call.id === id && view && view.id === id) { update(view); ctx.emit('call:update', view); }
        } catch (err) {
          if (call && call.id === id) error = advice(err && err.message ? err.message : String(err));
          if (ctx.toast) ctx.toast(error);
        } finally { pending = false; render(); }
      }
      function metadata(source) {
        return { role: source.role || 'unspecified', booking_ref: String(source.booking_ref || '').trim() || null, complete_booking_list: !!source.complete_booking_list, synthetic: false };
      }
      function fileB64(file) {
        return file.arrayBuffer().then((buffer) => {
          let binary = ''; const bytes = new Uint8Array(buffer);
          for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 32768));
          return btoa(binary);
        });
      }
      async function sendUploaded() {
        if (pending || encodingPending || !call || !call.id) return;
        if (!files.length && !paste.trim()) { error = 'Upload a paper or paste its text first.'; render(); return; }
        const id = call.id, selected = files.map((doc) => ({ ...doc })), text = paste;
        const pasted = { name: pasteName || 'pasted-papers.txt', media_type: 'text/plain', text, ...metadata({ role, booking_ref: bookingRef, complete_booking_list: complete }) };
        if (selected.reduce((sum, doc) => sum + doc.file.size, 0) + new TextEncoder().encode(text).length > 10 * 1024 * 1024) { error = 'Cargo papers must total 10 MB or less.'; render(); return; }
        encodingPending = true; error = ''; render();
        try {
          const documents = await Promise.all(selected.map(async (doc) => ({ name: doc.name, media_type: doc.file.type || 'application/octet-stream', data_b64: await fileB64(doc.file), ...metadata(doc) })));
          if (!call || call.id !== id) return;
          if (text.trim()) documents.push(pasted);
          encodingPending = false;
          await post('papers', { documents });
        } catch (err) { if (call && call.id === id) error = advice(err.message || String(err)); }
        finally { encodingPending = false; render(); }
      }
      function bind() {
        const click = (name, fn) => { const element = q(`[data-action="${name}"]`); if (element) element.onclick = fn; };
        const openKeyPanel = async () => {
          try { if (typeof ctx.openKeyPanel === 'function') await ctx.openKeyPanel(); }
          catch (err) { error = err && err.message ? err.message : String(err); }
          await refreshStatus();
        };
        click('open-key-panel', openKeyPanel);
        click('raise-spend-cap', openKeyPanel);
        click('retry-failed-bookings', () => {
          if (!call || !['needs_papers', 'failed'].includes(call.state)) return;
          const body = retryBody();
          if (!body) { error = 'Upload or paste the same papers again to retry this reading.'; render(); return; }
          return post('papers', body);
        });
        click('read-generated', () => post('papers', { use_generated_papers: true }));
        click('use-generated-load-list', () => post('papers', { use_generated_load_list: true }));
        click('use-generator-records', () => {
          if (!call || call.state !== 'needs_answers') return;
          const eligible = generatorEligibleBoxes((call.papers || {}).intake);
          const boxes = [...selectedGeneratorBoxes].filter((box) => eligible.has(box));
          if (boxes.length) return post('papers', { use_generator_records_for: boxes });
        });
        click('read-uploaded', sendUploaded);
        click('submit-answers', () => {
          const questions = ((call.papers || {}).intake || {}).questions || [];
          const payload = {};
          for (const question of questions) {
            const element = [...panel.querySelectorAll('[data-question-id]')].find((el) => el.dataset.questionId === String(question.id));
            const value = element ? element.value : answers[question.id];
            if (value == null || !String(value).trim()) { error = 'Answer every cargo question before sending.'; render(); return; }
            payload[question.id] = value;
          }
          return post('answers', { answers: payload });
        });
        click('loadlist', () => { shown = !shown; render(); });
        panel.querySelectorAll('[data-live-generator-box]').forEach((element) => { element.onchange = element.oninput = () => {
          if (!panel.contains(element) || !call || call.state !== 'needs_answers' || !eligibleGeneratorBoxes.has(element.dataset.liveGeneratorBox)) return;
          if (element.checked) selectedGeneratorBoxes.add(element.dataset.liveGeneratorBox); else selectedGeneratorBoxes.delete(element.dataset.liveGeneratorBox);
          render();
        }; });
        panel.querySelectorAll('[data-live-paper-booking]').forEach((element) => { element.ontoggle = () => {
          if (!panel.contains(element) || !call || element.dataset.livePaperCall !== call.id) return;
          const open = typeof element.open === 'boolean' ? element.open : element.getAttribute('open') != null;
          openPaperGroups.set(element.dataset.livePaperCall + '\n' + element.dataset.livePaperGroupKey, open);
        }; });
        panel.querySelectorAll('[data-live-question-booking]').forEach((element) => { element.ontoggle = () => {
          if (!panel.contains(element) || !call || element.dataset.liveQuestionCall !== call.id) return;
          const open = typeof element.open === 'boolean' ? element.open : element.getAttribute('open') != null;
          openQuestionGroups.set(element.dataset.liveQuestionCall + '\n' + element.dataset.liveQuestionGroupKey, open);
        }; });
        panel.querySelectorAll('[data-question-id]').forEach((element) => { element.oninput = element.onchange = () => { answers[element.dataset.questionId] = element.value; }; });
        panel.querySelectorAll('[data-live-field]').forEach((element) => {
          const key = element.dataset.liveField;
          element.oninput = element.onchange = () => {
            if (key === 'files') { files = files.concat(Array.from(element.files || []).map((file) => ({ file, name: file.name, role: 'unspecified', booking_ref: '', complete_booking_list: false }))); render(); }
            else if (key === 'paste') paste = element.value;
            else if (key === 'paste-name') pasteName = element.value;
            else if (key === 'role') role = element.value;
            else if (key === 'booking-ref') bookingRef = element.value;
            else if (key === 'complete-booking-list') complete = element.checked;
          };
        });
        panel.querySelectorAll('[data-doc-field]').forEach((element) => { element.oninput = element.onchange = () => { const doc = files[Number(element.dataset.fileIndex)]; if (doc) doc[element.dataset.docField] = element.dataset.docField === 'complete_booking_list' ? element.checked : element.value; }; });
        panel.querySelectorAll('[data-action="remove-paper"]').forEach((element) => { element.onclick = () => { files.splice(Number(element.dataset.fileIndex), 1); render(); }; });
      }
      ctx.on('call:update', update);
      ctx.on('status:update', acceptStatus);
      if (call) update(call); else render();
      void refreshStatus();
      return {
        ownsAction: true,
        enter() { active = true; if (ctx.call && (!call || ctx.call.id !== call.id)) update(ctx.call); else render(); void refreshStatus(); if (timer == null) timer = setInterval(() => { if (active) elapsed(); }, 250); },
        start() { if (call && call.state === 'needs_papers' && !unavailable()) return post('papers', { use_generated_papers: true }); },
        leave() { active = false; if (timer != null) clearInterval(timer); timer = null; },
      };
    },
  });
})();
