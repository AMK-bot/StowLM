"""Demo v6, second pass: confirmation is a server event, "rechecked" says what really happened, the live reader decodes through
the mask, and a fake x-ray never plays in the film. Fast and offline: no Chrome, no engine, no GPU, no network."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import types
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import brief, check_cache, v6
from app.server import HTTPServer, make_handler

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app/static"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
BOUND = {"sentence_sha256": "s" * 64, "rule_sha256": "r" * 64, "instance_sha256": "i" * 64}


class Catalog:
    active_plan_id = "plan-1"

    def catalog(self):
        return {"plans": []}


@pytest.fixture
def post(monkeypatch):
    """POST to a real handler with live replans on, a fixed reading and no process behind the job."""
    monkeypatch.setattr(v6, "reading_binding", lambda: dict(BOUND))
    monkeypatch.setattr(v6, "CONFIRMATIONS", v6.Confirmations())
    started = []

    class Live(v6.LiveReplan):
        def start(self, handler, token=None):
            if not (getattr(handler, "brief_options", None) or {}).get("live_replan"):
                raise PermissionError("off")
            started.append(v6.CONFIRMATIONS.require(token))
            v6.CONFIRMATIONS.spend(token)
            return {"id": "job", "status": "running"}

    monkeypatch.setattr(v6, "LIVE", Live())
    handler = make_handler({"ok": True}, Catalog(), stage_options={})
    handler.brief_options = {"live_replan": True}
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    handler.expected_port = httpd.server_port
    import threading
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def send(route: str, body) -> tuple[int, dict]:
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/v6/{route}", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)
    send.started = started
    yield send
    httpd.shutdown()
    httpd.server_close()


# ------------------------------------------------------------------ logic 2 #15: the token
def test_a_replan_with_no_token_or_a_made_up_token_is_refused(post):
    assert post("replan", {})[0] == 403
    assert post("replan", {"token": "0" * 32})[0] == 403
    assert post.started == []


def test_confirm_then_replan_carries_the_source_and_the_token_spends_once(post):
    status, body = post("confirm", BOUND)
    assert status == 200 and body["source"] == v6.CONFIRM_SOURCE
    assert post("replan", {"token": body["token"]})[0] == 202
    assert post.started[0] == {**BOUND, "source": v6.CONFIRM_SOURCE}
    assert post("replan", {"token": body["token"]})[0] == 403


@pytest.mark.parametrize("key", list(BOUND))
def test_a_page_holding_another_reading_cannot_confirm(post, key):
    status, body = post("confirm", {**BOUND, key: "x" * 64})
    assert status == 403 and "token" not in body


@pytest.mark.parametrize("key", list(BOUND))
def test_a_token_for_another_sentence_rule_or_instance_is_refused(post, monkeypatch, key):
    token = post("confirm", BOUND)[1]["token"]
    monkeypatch.setattr(v6, "reading_binding", lambda: {**BOUND, key: "x" * 64})
    status, body = post("replan", {"token": token})
    assert status == 403 and "another" in body["error"]
    assert post.started == []


def test_confirm_takes_only_the_three_hashes(post):
    assert post("confirm", {**BOUND, "source": "me"})[0] == 400
    assert post("confirm", {})[0] == 400


def test_the_real_binding_changes_with_sentence_rule_and_ship(monkeypatch):
    """Through the hero's instruction, the source of the reading on screen, and each hash moves alone."""
    monkeypatch.setattr(v6, "_worded_reading", lambda config: (_ for _ in ()).throw(LookupError("no reading")))  # the instruction as the reading's source
    first = v6.reading_binding()
    assert set(first) == set(BOUND) and all(re.fullmatch(r"[0-9a-f]{64}", value) for value in first.values())
    config, instruction = v6.hero(), v6.hero()["instruction"]
    other_rule = {"schema_version": instruction["rule"]["schema_version"], "constraint": {"type": "close_bay", "bay": 3}}

    def moved(**changes) -> set[str]:
        monkeypatch.setattr(v6, "hero", lambda: {**config, **changes})
        now = v6.reading_binding()
        return {key for key in first if now[key] != first[key]}

    assert moved(instruction={**instruction, "sentence": instruction["sentence"] + " Now."}) == {"sentence_sha256"}
    assert moved(instruction={**instruction, "rule": other_rule}) == {"rule_sha256"}
    assert moved(instance_sha256="0" * 64) == {"instance_sha256"}
    assert moved(file_sha256="0" * 64) == {"instance_sha256"}
    assert moved() == set()


def test_a_named_recording_binds_its_own_sentence(monkeypatch):
    now = v6.hero_reading()
    monkeypatch.setattr(v6, "hero_reading", lambda: {**now, "sentence": "Another wording of the same rule."})
    assert v6.reading_binding()["sentence_sha256"] == v6._sha256("Another wording of the same rule.")


def test_the_page_s_own_hashes_earn_a_token_for_each_reading_it_can_confirm():
    """Unpatched: what /api/v6/reading and the live button hand the page are what the server accepts, and they are two readings."""
    desk = v6.Confirmations()
    hero_binding = v6.reading()["binding"]
    assert desk.require(desk.confirm(hero_binding)["token"])["sentence_sha256"] == hero_binding["sentence_sha256"]
    live = v6._live_view()
    assert live["binding"]["sentence_sha256"] != hero_binding["sentence_sha256"]
    assert desk.require(desk.confirm(live["binding"])["token"])["rule_sha256"] == live["binding"]["rule_sha256"]
    with pytest.raises(PermissionError):
        desk.confirm({**hero_binding, "rule_sha256": live["binding"]["rule_sha256"]})


@pytest.mark.parametrize("reading_of", [lambda: v6.hero_reading(), lambda: v6.live_reading()], ids=["hero", "live"])
def test_worker_takes_its_source_from_the_token_and_refuses_another_reading(monkeypatch, tmp_path, reading_of):
    from experiments.mvp.pipeline import runner
    seen = {}
    monkeypatch.setattr(runner, "run_packet", lambda packet, **options: seen.update(options))
    monkeypatch.setattr(runner, "assemble", lambda packet: None)
    monkeypatch.setattr("experiments.mvp.reader.schema.context_from_instance", lambda instance: {"id": "ship"})
    clean = reading_of()
    now = v6._binding_of(clean)
    (tmp_path / "confirmation.json").write_text(json.dumps({**now, "source": v6.CONFIRM_SOURCE}))
    v6.replan_worker(str(tmp_path))
    assert seen["confirmation_source"] == v6.CONFIRM_SOURCE
    assert seen["confirm"]({"sentence": clean["sentence"], "parsed": clean["answer"]}) is True
    assert seen["confirm"]({"sentence": "Close bay 1.", "parsed": clean["answer"]}) is False
    (tmp_path / "confirmation.json").write_text(json.dumps({**now, "rule_sha256": "x", "source": v6.CONFIRM_SOURCE}))
    with pytest.raises(SystemExit):
        v6.replan_worker(str(tmp_path))


def test_the_ui_posts_a_real_confirmation_and_carries_its_token():
    reading, replan = (STATIC / "stage-v6reading.js").read_text(), (STATIC / "stage-v6replan.js").read_text()
    assert "'/api/v6/confirm'" in reading and "JSON.stringify(d.binding || {})" in reading and "V.confirmation = body" in reading
    assert "post('/api/v6/confirm', d.live.binding)" in replan and "post('/api/v6/replan', { token: ok.token })" in replan and "body: '{}'" not in replan
    assert replan.index("if (!said.ok || !ok || !ok.token)") < replan.index("post('/api/v6/replan'"), "no token, no replan request"


def test_no_receipt_is_sealed_by_a_callback_that_says_yes_to_everything():
    assert "lambda reading: True" not in (ROOT / "app/v6.py").read_text()


# ------------------------------------------------------------------ logic 2 #17: "rechecked"
def replay(source):
    return {"checker": {"passed": True, "check_source": source}, "frames": [{"counts": {"weighted_nonproductive": 10}}]}


@pytest.mark.parametrize(("sources", "words"), [
    (("fresh",), "rechecked now"), (("memo",), "rechecked (exact memo)"),
    (("fresh", "fresh"), "rechecked now"), (("fresh", "memo"), "rechecked (exact memo)"),
    ((None,), None), (("fresh", None), None), ((), None)])
def test_the_words_follow_what_happened_to_each_plan(sources, words):
    assert v6.recheck_words(*[replay(source) for source in sources]) == words


def test_a_stored_flag_or_a_loaded_report_claims_no_recheck():
    assert v6.recheck_words({"checker": {"passed": True}}) is None
    assert v6.recheck_words({"report": {"passed": True, "check_source": "fresh"}}) is None


def test_the_cache_says_how_each_report_came(monkeypatch, tmp_path):
    import app.check_cache as cache
    report = SimpleNamespace(model_dump=lambda mode: {}, parts={}, violations=[])
    monkeypatch.setattr(cache, "require_current", lambda: None)
    monkeypatch.setattr(cache.full, "check", lambda *_: report)
    monkeypatch.setenv(cache.KILL_ENV, "1")
    cache.cached_check(None, None)
    assert cache.last_source() == "fresh"
    monkeypatch.delenv(cache.KILL_ENV)
    monkeypatch.setattr(cache, "checker_epoch", lambda: "e")
    monkeypatch.setattr(cache, "content_digest", lambda _: "d")
    monkeypatch.setattr(cache, "_load", lambda *_a, **_k: report)
    plan = SimpleNamespace(id="p")
    key = cache.cache_key("d", "d", "e")
    (tmp_path / f"{key}.json").write_text("{}")
    cache.cached_check(None, plan, cache_dir=tmp_path)
    assert cache.last_source() == "memo"


def test_check_source_never_touches_the_physics_epoch():
    """app/checker.py is hashed into every physics frame's epoch: the source is stamped on by its callers."""
    from app import stage_physics
    assert "app/checker.py" in stage_physics._FRAME_SOURCES and "check_source" not in (ROOT / "app/checker.py").read_text()
    assert "last_source()" in (ROOT / "app/backend.py").read_text() and "last_source()" in (ROOT / "app/stage_fast.py").read_text()


def test_the_gate_withholds_a_verdict_once_the_sources_moved(monkeypatch):
    hero = v6.Hero()
    hero._gate = {"status": "shown", "recheck": "rechecked now"}
    monkeypatch.setattr(check_cache, "epoch_drift", lambda: None)
    assert hero.gate(None)["status"] == "shown"
    monkeypatch.setattr(check_cache, "epoch_drift", lambda: check_cache.DRIFT_REASON)
    assert hero.gate(None) == {"status": "not_shown", "why": [check_cache.DRIFT_REASON]}


def test_the_replan_block_says_the_gate_s_words_and_nothing_stored_does(monkeypatch):
    for word, expect in (("rechecked now", ", rechecked now"), ("rechecked (exact memo)", ", rechecked (exact memo)"), (None, "")):
        monkeypatch.setattr(v6.HERO, "gate", lambda _service, word=word: {"status": "before_only", "before": {"recheck": word}})
        block = v6.replan(Catalog())
        assert block["before_note"] == v6.hero()["before_note"] + expect
    assert "rechecked" not in json.dumps(v6.hero())


def test_rechecked_now_appears_only_where_the_gate_decides_it():
    """Every path a stored plan's label can take. Two constants hold the words: the gate's table and the package's "fresh" label."""
    paths = [*ROOT.glob("app/v6*.py"), ROOT / "app/brief.py", ROOT / "app/v6_hero.json", ROOT / "app/static/stage-shots.js",
             *STATIC.glob("stage-v6*.js"), STATIC / "stage-director.js"]
    found = [line.strip() for path in paths for line in path.read_text().splitlines() if "rechecked now" in line.lower()]
    assert sum(line.startswith(("RECHECKS =", "RECHECKED =")) for line in found) == 2, found
    # The rest: the filter that takes the words off stored package files, and prose. Nothing else may write them.
    assert all(line.startswith(("RECHECKS =", "RECHECKED =", "_DATED = re.compile(", '"""', "/*")) for line in found), found
    source = (ROOT / "app/v6.py").read_text()
    assert source.count('RECHECKS["fresh"]') == 1 and 'RECHECKS["fresh"] if cleared(name) and dig(entries, name, "checked_utc")' in source  # only the launch regate's own check
    assert "ran && d.fresh ? d.fresh : d.label" in (STATIC / "stage-v6package.js").read_text()  # the package's, only after verify's checker passed


def test_the_stored_package_files_never_reach_the_screen_saying_rechecked_now(monkeypatch):
    from app import v6_package
    monkeypatch.setattr(v6_package, "verification_state", lambda directory: {"status": "passed", "reason": None, "package_sha256": "p", "checked_on": None})
    monkeypatch.setattr(v6_package, "require_hero_plan", lambda directory: None)  # the plan match is test_v6_hero_rule.py's
    assert v6_package.view()["note"].count("rechecked now") == 0
    assert b"rechecked now" not in v6.ASSETS["bay-plan.html"][0].read_bytes()


SPARK = {"parser": "SparkParser", "model": "muse-spark-1"}
GLIMMER = {"parser": "GlimmerParser", "model": "muse-glimmer-30b", "adapter_sha256": "a" * 64}


@pytest.mark.parametrize(("recorded", "evidence", "reader", "stands_in"), [
    (False, None, v6.NO_READER["label"], "no model has read"),
    (True, SPARK, "Muse Spark · Meta Model API · cloud", "recorded reading stands in"),
    (True, GLIMMER, "StowLM Reader · fine-tuned Muse Glimmer 30B · on this laptop", None)])
def test_the_replan_rule_keeps_its_stand_in_label_until_the_reader_really_read_it(monkeypatch, recorded, evidence, reader, stands_in):
    now = {**v6.hero_reading(), "recorded": recorded, "evidence": evidence}
    monkeypatch.setattr(v6, "hero_reading", lambda: now)
    rule = v6._replan_rule()
    assert rule["reader"] == reader
    assert (stands_in in rule["stand_in_note"]) if stands_in else rule["stand_in_note"] is None
    assert ("reader" in v6.stand_ins()) is bool(stands_in)


# ------------------------------------------------------------------ logic 3 X1: the live reader is masked
class Tokenizer:
    eos_token_ids = {7, 3}

    def encode(self, prompt, add_special_tokens=True):
        assert add_special_tokens is False
        return [1, 2, 3]


def fake_mlx(monkeypatch, generate):
    package, sampling = types.ModuleType("mlx_lm"), types.ModuleType("mlx_lm.sample_utils")
    package.generate, package.load = generate, lambda *_a, **_k: ("model", Tokenizer())
    sampling.make_sampler = lambda temp: ("sampler", temp)
    monkeypatch.setitem(sys.modules, "mlx_lm", package)
    monkeypatch.setitem(sys.modules, "mlx_lm.sample_utils", sampling)


class Processor:
    def __init__(self):
        self.validated = None

    def validate_output(self, text):
        self.validated = text
        return text


@pytest.fixture
def masked(monkeypatch, tmp_path):
    """glimmer_reader with fake MLX: returns the parser and what generate received."""
    from experiments.mvp.reader import evaluate, v3
    import experiments.mvp.reader.v3.decoder as decoder
    calls, processor = {}, Processor()
    answer = json.dumps({"schema_version": 1, "refusal": {"refused": True}})

    def generate(model, tokenizer, **options):
        calls.update(options)
        return answer

    fake_mlx(monkeypatch, generate)
    monkeypatch.setattr(evaluate, "render_prompt", lambda tokenizer, messages: "prompt")

    def factory(tokenizer, context, prompt_len, stop):
        calls["made"] = (context, prompt_len, stop)
        return processor

    monkeypatch.setattr(decoder, "make_logits_processor", factory)
    (tmp_path / "adapters.safetensors").write_bytes(b"weights")
    return brief.glimmer_reader(tmp_path)(), calls, processor


def test_the_live_reader_generates_through_the_v3_mask(masked):
    parser, calls, processor = masked
    context = {"id": "ship", "bays": [1]}
    assert parser.generate("Close bay 1.", context)
    assert calls["logits_processors"] == [processor], "the live reader must decode through the mask"
    assert calls["prompt"] == [1, 2, 3] and calls["max_tokens"] == 256 and calls["sampler"] == ("sampler", 0)
    assert calls["made"] == (context, 3, 3)
    assert processor.validated is not None


def test_a_truncated_masked_answer_fails_and_is_not_repaired(masked):
    parser, _, processor = masked
    processor.validate_output = lambda text: (_ for _ in ()).throw(ValueError("incomplete"))
    with pytest.raises(ValueError, match="incomplete"):
        parser.generate("Close bay 1.", {"id": "ship", "bays": [1]})


def test_no_app_path_generates_without_the_processor():
    assert "logits_processors=[processor]" in (ROOT / "app/brief.py").read_text()
    calls = re.findall(r"\bgenerate\(model, tokenizer|mlx_generate", (ROOT / "app/brief.py").read_text())
    assert calls == ["generate(model, tokenizer"]


# ------------------------------------------------------------------ logic 3 X2: the fake never plays in the film
def fixture():
    return json.loads((v6.XRAY / "fixtures/fake-illegal-bay.json").read_text())


def recorded_trace():
    """The fake's steps under a stubbed validator: only the fields this module reads matter here."""
    trace = fixture()
    trace["source"] = {**trace["source"], "kind": "recorded", "label": "Recorded answer, selected from 200 sentences"}
    trace["stage_record"] = {**(trace.get("stage_record") or {}), "adapter_sha256": v6._adoption()["adopted"]["adapter_sha256"]}  # the adopted reader's
    return trace


@pytest.fixture
def no_validation(monkeypatch):
    import experiments.mvp.pipeline.xray as package
    monkeypatch.setattr(package, "validate_trace", lambda trace: trace)


def test_the_fake_is_labelled_and_not_for_the_film(monkeypatch):
    monkeypatch.setattr(v6, "_trace_path", lambda: v6.XRAY / "fixtures/fake-illegal-bay.json")  # a real trace may be on disk too
    data = v6.xray()
    assert data["real"] is False and data["film"] is False and "FAKE" in data["source_label"]
    assert data["caption_label"] == data["source_label"] and "Recorded answer" not in data["caption_label"]


def test_the_file_name_does_not_make_a_trace_real(monkeypatch, tmp_path, no_validation):
    path = tmp_path / "real-trace.json"
    path.write_text(json.dumps(fixture()))
    monkeypatch.setattr(v6, "hero", lambda: {"xray_trace": str(path)})
    data = v6.xray()
    assert data["real"] is False and data["film"] is False


def test_the_path_is_configured_then_named_then_none(monkeypatch, tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    monkeypatch.setattr(v6, "XRAY", tmp_path)
    mine = tmp_path / "mine.json"
    monkeypatch.setattr(v6, "hero", lambda: {})
    assert v6._trace_path().name == "fake-illegal-bay.json"
    (fixtures / "before-fine-tuning-trace.json").write_text("{}")
    assert v6._trace_path().name == "before-fine-tuning-trace.json"
    (fixtures / "real-trace.json").write_text("{}")
    assert v6._trace_path().name == "real-trace.json"
    monkeypatch.setattr(v6, "hero", lambda: {"xray_trace": str(mine)})
    assert v6._trace_path().name == "real-trace.json"
    mine.write_text("{}")
    assert v6._trace_path() == mine


def test_a_recording_plays_under_its_own_label(monkeypatch, tmp_path, no_validation):
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(recorded_trace()))
    monkeypatch.setattr(v6, "hero", lambda: {"xray_trace": str(path)})
    data = v6.xray()
    assert data["real"] is True and data["film"] is True
    assert data["caption_label"] == data["source_label"] == "Recorded answer, selected from 200 sentences"


def test_a_recording_with_no_block_is_not_for_the_film(monkeypatch, tmp_path, no_validation):
    trace = recorded_trace()
    for step in trace["steps"]:
        step["top_k"] = [{**row, "blocked": False, "reason": None} for row in step["top_k"]]
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(trace))
    monkeypatch.setattr(v6, "hero", lambda: {"xray_trace": str(path)})
    assert v6.xray()["film"] is False


def test_a_base_model_recording_still_reads_recorded_answer(monkeypatch, tmp_path, no_validation):
    trace = recorded_trace()
    trace["source"]["label"] = "Glimmer before fine-tuning, selected from 200 sentences"
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(trace))
    monkeypatch.setattr(v6, "hero", lambda: {"xray_trace": str(path)})
    assert v6.xray()["caption_label"] == "Recorded answer · Glimmer before fine-tuning, selected from 200 sentences"


@pytest.mark.parametrize("shape", [[], {"steps": []}, {"source": {"label": "x"}}, {"source": "x", "steps": [{}]},
                                   {"source": {"label": "x"}, "steps": [{"top_k": "?"}]}, {"source": {"label": "x"}, "steps": ["?"]}])
def test_a_changed_shape_is_a_clean_gap_never_the_fake(monkeypatch, tmp_path, shape):
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(shape))
    monkeypatch.setattr(v6, "hero", lambda: {"xray_trace": str(path)})
    block = v6.feature(v6.xray)
    assert block["available"] is False and "reason" in block


@needs_node
@pytest.mark.parametrize("data", [{"film": False}, {}])
def test_the_film_refuses_before_it_draws_anything(data):
    code = f"""
      global.window = globalThis; let def;
      window.StageV6 = {{ chapter(c) {{ def = c; }} }};
      require({json.dumps(str(STATIC / 'stage-v6xray.js'))});
      console.log(JSON.stringify(def.mount(null, {json.dumps(data)}, {{ interactive: false }})));"""
    assert json.loads(subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True).stdout) == {"gap": True}


def test_shot_6_reads_its_label_from_the_reading_and_falls_back_to_the_card():
    """Film-judge edit 4: the trace became the figures card; the confirm beat's label is the reading's own, and no reading means the mask card."""
    shots = (STATIC / "stage-shots.js").read_text()
    shot6 = shots[shots.index('"title": "X-ray and confirm"'):]
    shot6 = shot6[:shot6.index('"title": "The plan changes"')]
    assert '"chapter": "reading"' in shot6 and '"type": "pair"' in shot6 and '"text": "{{reader_label}}"' in shot6 and '"card": "mask"' in shot6
    director = (STATIC / "stage-director.js").read_text()
    assert "put('reader_label', reading.label" in director and "chapters.map(async (id) => { if (!V6STATE || !(await FLOW.prepare(id))) gaps.push(id); })" in director
