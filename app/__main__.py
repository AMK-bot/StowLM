"""Launch the offline demo with ``uv run python -m app``."""
from __future__ import annotations

import argparse
import os
import threading
from pathlib import Path

# The checker and physics epochs hash these limits, so the server must match precompute_timeline.py to reuse its
# caches; set before numpy loads. An explicit different value is honoured and is a cache miss.
for _thread_key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_thread_key, "1")

from app.inputs import load_inputs
from app.server import create_server


def _functional_adapter(value: str | None) -> Path | None:
    """Check an adopted adapter's checksum when present; missing models stay unavailable."""
    import hashlib
    import json

    root = Path(__file__).resolve().parents[1]
    record = root / "experiments/mvp/reader/v3/results-final/final-adoption-d266.json"
    adoption = json.loads(record.read_text())["adopted"] if record.is_file() else None
    if value is None and adoption is None:
        return None
    selected = Path(value).expanduser() if value is not None else root / adoption["path"]
    adapter = selected.resolve()
    weights = adapter / "adapters.safetensors"
    adopted_path = (root / adoption["path"]).resolve() if adoption is not None else None
    if weights.is_file() and adapter == adopted_path:
        with weights.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != adoption["adapter_sha256"]:
            raise ValueError("The adopted reader adapter does not match its adoption checksum")
    return adapter


def _announce_apps(port: int, *, functional: bool, v6: bool, label: str = "") -> None:
    if v6:
        print(f"Demo app: http://127.0.0.1:{port}/planner?demo=1 (Ctrl-C to stop) · {label}", flush=True)
    elif not functional:
        print(f"StowLM demo: http://127.0.0.1:{port} (Ctrl-C to stop) · {label}", flush=True)
    if functional:
        print(f"Functional app: http://127.0.0.1:{port}/planner?live=1 (Ctrl-C to stop)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local StowLM replay and full-checker dashboard")
    parser.add_argument("--instance", help="Instance JSON (or .json.gz)")
    parser.add_argument("--plan", help="MovePlan or PlannerResult JSON (or .json.gz)")
    parser.add_argument("--run-folder", help="Evaluation run folder containing planner artifacts")
    parser.add_argument("--entry", help="Run-relative plan/result path when the folder contains several")
    parser.add_argument("--compare-plan", help="Reference MovePlan or PlannerResult for the selected instance")
    parser.add_argument("--compare-run-folder", help="Reference evaluation run folder")
    parser.add_argument("--compare-entry", help="Run-relative reference result path")
    parser.add_argument("--import-plan", action="append", default=[],
                        help="Register another content-bound artifact (repeatable; current and historical corpora)")
    parser.add_argument("--no-discovery", action="store_true",
                        help="Use only explicitly selected inputs; skip automatic corpus/run discovery")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--live-baseline", action="store_true",
                        help="Stage fallback: run the two-step baseline live when no frozen baseline is registered "
                             "(shown as 'measured live')")
    parser.add_argument("--no-companions", action="store_true",
                        help="Do not attach the base Glimmer + mask run and the Muse Spark plan for the same instance")
    parser.add_argument("--instruct-live", action="store_true",
                        help="Instruction box: allow live Muse Spark parses for unrecorded sentences (still needs "
                             "SPARK_CREDIT_READY=1 and the spend cap); default is recorded answers only")
    parser.add_argument("--brief-live", action="store_true",
                        help="Brief panel: live reading and speech through the existing paid clients and purpose caps "
                             "(still needs SPARK_CREDIT_READY=1); default serves recorded readings and makes no calls")
    parser.add_argument("--brief-live-replan", action="store_true",
                        help="Brief panel: offer a real replan (about 5 min, one CPU worker, its own process)")
    parser.add_argument("--reader", choices=("recorded", "glimmer"), default="recorded",
                        help="Brief panel reader: recorded readings, or the fine-tuned reader on this laptop (MLX)")
    parser.add_argument("--reader-adapter", help="Adapter folder for --reader glimmer")
    parser.add_argument("--lite-replay", choices=("auto", "on", "off"), default="auto",
                        help="Stage-only lite replay (no per-frame states); auto = plans over 600 lifts")
    parser.add_argument("--timeline-cache", action="append", default=[],
                        help="Extra read-only physics timeline cache directory (repeatable). Entries are used only "
                             "when both instance and plan SHA-256 match; each run folder's own timeline-cache/ is "
                             "also read automatically. New frames are written only to experiments/mvp/ui/cache.")
    parser.add_argument("--video-showcase", action="store_true",
                        help="Video launch: register the small Laem Chabang showcase calls (scalable solver + "
                             "two-step rival, feeder_1700 active, feeder_500 in the picker); see "
                             "experiments/mvp/ui/RECORDING.md")
    parser.add_argument("--stowlm-run", action="append", default=[],
                        help="Register every shipped instance (result status ok) of a StowLM run folder for the plan "
                             "picker and the video shot selectors (repeatable). Without --run-folder the first one is "
                             "the active plan. --video-showcase adds experiments/mvp/runs/stowlm-showcase* by itself.")
    parser.add_argument("--no-check-cache", action="store_true",
                        help="Always run the full checker fresh (skip the exact report memo in "
                             "experiments/mvp/ui/cache/checks; same as STOWLM_NO_CHECK_CACHE=1). With --v6 this also "
                             "forces a real launch regate, skipping the exact memo in experiments/mvp/ui/cache/regate, and "
                             "earns the label 'rechecked now'; from the memo the label gives the date and time of the "
                             "real check and says 'exact memo'")
    parser.add_argument("--v6", action="store_true",
                        help="Demo v6 launch: register the hero plans named in app/v6_hero.json; open /stage?v6=1. "
                             "Replaces --run-folder and --compare-* selection")
    parser.add_argument("--functional", action="store_true",
                        help="Serve the Functional app at /planner?live=1; combines with --v6")
    parser.add_argument("--env-file", default=str(Path(__file__).resolve().parents[1] / ".env"),
                        help="Functional app KEY=VALUE file (default: repository .env; environment takes precedence)")
    parser.add_argument("--manifest",
                        help="Film mode: freeze manifest to verify the tree against before anything loads (or set "
                             "PIPELINE_MANIFEST). Any drift refuses the start; receipts then carry the pipeline layer's pipeline_sha256 (and a film manifest's own digest beside it). Without "
                             "one the build is labelled 'Development build, not frozen'")
    parser.add_argument('--storyboard-root', help='Frozen instance corpus for pre-declared illustrative selection')
    parser.add_argument('--storyboard-split', choices=('train', 'val', 'test'))
    parser.add_argument('--storyboard-rung', choices=('tiny', 'small', 'medium', 'large', 'full'))
    parser.add_argument('--storyboard-rule', choices=('first-dg-direct-delivery',))
    parser.add_argument('--storyboard-split-manifest', help='Pinned split inventory (default experiments/splits/v2/manifest.json for campaign roots)')
    args = parser.parse_args()
    from app import v6_build
    manifest = v6_build.manifest_from(args.manifest)
    if manifest:
        v6_build.require(manifest)  # raises Refused (a plain message, exit 1) on drift or when the verifier can't load
    else:  # no manifest, no frozen claim: a value left in the shell never reaches a receipt
        os.environ.pop(v6_build.SHA_ENV, None)
        os.environ.pop(v6_build.TREE_ENV, None)
        os.environ.pop(v6_build.FILM_ENV, None)
    if args.no_check_cache:
        from app.check_cache import KILL_ENV
        os.environ[KILL_ENV] = "1"
    functional = None
    if args.functional:
        from app.functional import FunctionalApp, KeyProvider
        try:
            adapter = _functional_adapter(args.reader_adapter)
            args.reader_adapter = str(adapter) if adapter is not None else None
            env_file = Path(args.env_file).expanduser().resolve()
            key_provider = KeyProvider(env_file=env_file)
            functional = FunctionalApp(adapter_dir=args.reader_adapter, key_provider=key_provider, env_file=env_file)
        except (ValueError, OSError) as error:
            parser.error(str(error))
        if not args.v6:
            # No replay instance or default corpus discovery is needed for this app.
            from app.server import HTTPServer, make_handler
            handler = make_handler({}, functional=functional)
            try:
                server = HTTPServer(("127.0.0.1", args.port), handler)
            except OSError as error:
                functional.close()
                parser.error(str(error))
            handler.expected_port = server.server_port
            _announce_apps(server.server_port, functional=True, v6=False)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                functional.close()
                server.server_close()
            return
    try:
        selection = None
        explicit = (args.instance, args.plan, args.run_folder, args.entry, args.compare_plan, args.compare_run_folder,
                    args.compare_entry, args.storyboard_root, args.storyboard_split, args.storyboard_rung,
                    args.storyboard_rule, args.storyboard_split_manifest)
        if args.v6:
            if any(explicit):
                raise ValueError("--v6 replaces --run-folder/--compare-* selection; use one or the other")
            from app.v6 import hero
            args.instance, args.plan = hero()["instance"], hero()["before_plan"]
            args.no_discovery = args.no_companions = True
            explicit = (args.instance,)
        if not any(explicit) and not args.stowlm_run:
            # No-argument launch: the frozen default demo instance (exact solver plan, proved optimal) against the
            # frozen two-step baseline run for the same instance (app/stage_extras.py).
            from app.stage_extras import default_selection
            chosen = default_selection()
            if chosen is not None:
                args.run_folder, args.entry = chosen["run_folder"], chosen["entry"]
                args.compare_run_folder, args.compare_entry = chosen["compare_run_folder"], chosen["compare_entry"]
        extra_pairs = []
        if args.video_showcase:
            if any(explicit):
                raise ValueError("--video-showcase replaces --run-folder/--compare-* selection; use one or the other")
            from app.stage_extras import VIDEO_SHOWCASE
            main_pair, *extra_pairs = VIDEO_SHOWCASE
            args.run_folder, args.entry = main_pair["run_folder"], main_pair["entry"]
            args.compare_run_folder, args.compare_entry = main_pair["compare_run_folder"], main_pair["compare_entry"]
        from app.stage_stowlm import hero_order, shipped_entries, showcase_run_folders
        stowlm_runs = list(dict.fromkeys(str(Path(x).resolve()) for x in args.stowlm_run))
        if args.video_showcase:  # final StowLM showcase runs (task 9.2) register themselves when present on disk
            stowlm_runs += [str(p) for p in showcase_run_folders() if str(p) not in stowlm_runs]
        stowlm_pairs = [(run, entry) for run in stowlm_runs for entry in shipped_entries(run)]
        if args.video_showcase:  # D158: 29000201, 29000022, 29000102 whatever order the folder sorts in
            stowlm_pairs = hero_order(stowlm_pairs)
        for run in args.stowlm_run:
            if not shipped_entries(run):
                print(f"note: --stowlm-run {run}: no shipped instance (status ok) found", flush=True)
        if stowlm_pairs and not any(explicit) and not args.video_showcase:
            (args.run_folder, args.entry), stowlm_pairs = stowlm_pairs[0], stowlm_pairs[1:]
            explicit = (args.run_folder,)
        flags = (args.storyboard_root, args.storyboard_split, args.storyboard_rung, args.storyboard_rule)
        if any(flags) or args.storyboard_split_manifest:
            if not all(flags) or args.instance or args.run_folder:
                raise ValueError('Supply all --storyboard-* flags without --instance or --run-folder')
            from app.selection import select_storyboard
            args.instance, selection = select_storyboard(args.storyboard_root,
                split=args.storyboard_split, rung=args.storyboard_rung, rule=args.storyboard_rule,
                split_manifest=args.storyboard_split_manifest)
        inputs = load_inputs(instance_path=args.instance, plan_path=args.plan,
                             run_folder=args.run_folder, entry=args.entry)
        if selection:
            inputs.metadata['storyboard_selection'] = selection
        comparison_inputs = []
        if args.compare_entry and not args.compare_run_folder:
            raise ValueError("--compare-entry requires --compare-run-folder")
        if args.compare_plan or args.compare_run_folder:
            other = load_inputs(instance_path=args.instance, plan_path=args.compare_plan,
                                run_folder=args.compare_run_folder, entry=args.compare_entry,
                                selected_instance=inputs.instance)
            if other.instance != inputs.instance:
                raise ValueError("Reference plan must use exactly the same instance and starting world")
            comparison_inputs.append(other)
        comparison_inputs.extend(load_inputs(plan_path=path) for path in args.import_plan)
        for pair in extra_pairs:  # further showcase calls for the plan picker (each with its own two-step rival)
            first = load_inputs(run_folder=pair["run_folder"], entry=pair["entry"])
            rival = load_inputs(run_folder=pair["compare_run_folder"], entry=pair["compare_entry"],
                                selected_instance=first.instance)
            if rival.instance != first.instance:
                raise ValueError("Showcase rival must use exactly the same instance and starting world")
            comparison_inputs.extend((first, rival))
        for run, entry in stowlm_pairs:
            if (Path(run).resolve(), entry) == (Path(args.run_folder).resolve() if args.run_folder else None, args.entry):
                continue
            try:
                comparison_inputs.append(load_inputs(run_folder=run, entry=entry))
            except (ValueError, OSError) as error:
                print(f"note: StowLM run {run} {entry} not registered: {error}", flush=True)
        if args.video_showcase:
            from app.stage_extras import instruction_inputs
            extra, notes = instruction_inputs()  # D144 recorded solver replan under a DG instruction
            comparison_inputs.extend(extra)
            for note in notes:
                print(f"note: {note}", flush=True)
            # Shots 5 and 7 stay on the tiny default call (real base-mask x-ray trace, cached Spark explanation):
            # register it, its frozen two-step and its companions too. Each is replayed and re-checked when shown.
            from app.stage_extras import companion_inputs, default_selection
            tiny = default_selection()
            if tiny is not None:
                first = load_inputs(run_folder=tiny["run_folder"], entry=tiny["entry"])
                comparison_inputs.append(first)
                if tiny["compare_run_folder"]:
                    comparison_inputs.append(load_inputs(run_folder=tiny["compare_run_folder"],
                                                         entry=tiny["compare_entry"], selected_instance=first.instance))
                extra, notes = companion_inputs(first.instance, exclude_planners=(first.metadata.get("planner"),))
                comparison_inputs.extend(extra)
                for note in notes:
                    print(f"note: {note}", flush=True)
        if not args.no_companions and inputs.plan is not None:
            from app.stage_extras import companion_inputs
            extra, notes = companion_inputs(inputs.instance, exclude_planners=(inputs.metadata.get("planner"),))
            comparison_inputs.extend(extra)
            for note in notes:
                print(f"note: {note}", flush=True)
        if args.v6:
            from app.v6 import register_hero
            for note in register_hero(inputs, comparison_inputs):
                print(f"note: {note}", flush=True)
        stage_options = {}
        if args.live_baseline:
            stage_options["live_baseline"] = True
        if args.timeline_cache:
            stage_options["extra_cache_dirs"] = tuple(args.timeline_cache)
        if args.v6:
            stage_options["memo_bundles"] = True
        server = create_server(inputs.instance, inputs.plan, port=args.port,
                               source_metadata=inputs.metadata, comparison_inputs=comparison_inputs,
                               discover=not args.no_discovery,
                               stage_options=stage_options or None,
                               lite_replay={"auto": None, "on": True, "off": False}[args.lite_replay])
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if args.v6:  # before the first request: from here no stored hero plan leaves the server until its gates pass
        from app.v6 import guard_plans
        guard_plans(server.RequestHandlerClass.demo_service)
    server.RequestHandlerClass.functional_app = functional
    server.RequestHandlerClass.instruct_live = args.instruct_live  # read by app/instruct.py
    if args.reader == "glimmer" and not args.functional:  # Functional availability reports missing model files.
        from app.brief import glimmer_reader
        try:
            glimmer_reader(args.reader_adapter)
        except ValueError as error:
            parser.error(str(error))
    server.RequestHandlerClass.brief_options = {"live": args.brief_live, "live_replan": args.brief_live_replan,
                                                "reader": args.reader, "reader_adapter": args.reader_adapter,
                                                "v6": args.v6}  # app/brief.py
    _announce_apps(server.server_port, functional=functional is not None, v6=args.v6, label=v6_build.current()['label'])
    if args.v6:  # the page can open straight away; this says when its first load has nothing left to wait for
        from app.v6 import warm
        threading.Thread(target=lambda: print(warm(server.RequestHandlerClass), flush=True), name="stowlm-v6-warm", daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if functional is not None:
            functional.close()
        server.server_close()


if __name__ == "__main__":
    main()
