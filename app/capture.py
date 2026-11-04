"""Capture the real static UI offline, using engine-generated replay states.

This harness only replaces the HTTP fetch with the identical JSON response. It
does not change the renderer or implement crane rules. Requires an installed
Chrome/Chromium; no packages or browser downloads are needed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from app.backend import build_replay
from app.inputs import load_inputs
from app.comparison import build_comparison
from app.flow import SOURCE_KEYS
from stowage.model import PlannerResult


def checked_replay(inputs) -> dict:
    """Use a fresh verdict while retaining imported status, calls and provenance."""
    source_result = inputs.metadata.get("planner_result")
    result = PlannerResult.model_validate(source_result) if source_result is not None else None
    payload = build_replay(inputs.instance, inputs.plan, planner_result=result)
    payload["metadata"].update({key: value for key, value in inputs.metadata.items() if key in SOURCE_KEYS})
    from app.explanations import explanation_for_replay
    payload['explanation'] = explanation_for_replay(payload, source_path=inputs.metadata.get('source_path'))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", help="Installed Chrome/Chromium executable")
    parser.add_argument("--output", type=Path, default=Path("experiments/ui-shots"))
    parser.add_argument("--instance")
    parser.add_argument("--plan")
    parser.add_argument("--run-folder")
    parser.add_argument("--entry")
    parser.add_argument("--compare-plan")
    parser.add_argument("--compare-run-folder")
    parser.add_argument("--compare-entry")
    args = parser.parse_args()
    chrome = args.chrome or shutil.which("chromium") or shutil.which("google-chrome")
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if chrome is None and mac_chrome.exists():
        chrome = str(mac_chrome)
    if chrome is None:
        parser.error("No installed Chrome/Chromium found; pass --chrome")
    inputs = load_inputs(instance_path=args.instance, plan_path=args.plan,
                         run_folder=args.run_folder, entry=args.entry)
    instance = inputs.instance
    payload = checked_replay(inputs)
    bundle = {"demo": payload, "catalog": {
        "instances": [{"id": instance.id, "label": instance.id, "size_class": instance.size_class}],
        "plans": [{"id": "left", "instance_id": instance.id, "label": payload["metadata"]["label"],
                   "planner": payload["metadata"]["planner"]}], "active_plan_id": "left"},
        "replays": {"left": payload}, "comparison": None}
    if args.compare_entry and not args.compare_run_folder:
        parser.error("--compare-entry requires --compare-run-folder")
    if args.compare_plan or args.compare_run_folder:
        other = load_inputs(plan_path=args.compare_plan, run_folder=args.compare_run_folder,
                            entry=args.compare_entry, selected_instance=instance)
        right = checked_replay(other)
        bundle["replays"]["right"] = right
        bundle["catalog"]["plans"].append({"id": "right", "instance_id": instance.id,
            "label": right["metadata"]["label"], "planner": right["metadata"]["planner"]})
        bundle["comparison"] = build_comparison(payload, right)
        bundle["comparison"]["left"]["replay_id"] = "left"
        bundle["comparison"]["right"]["replay_id"] = "right"
    static = Path(__file__).with_name("static")
    html = (static / "index.html").read_text()
    html = html.replace('<link rel="stylesheet" href="/style.css">',
                        "<style>" + (static / "style.css").read_text() + "</style>")
    html = html.replace('<script src="/app.js" defer></script>', "")
    # HTML recognizes script end tags without regard to case, even inside a
    # JavaScript string. Escape every imported '<' before entering HTML parsing.
    js = "const captured=" + json.dumps(bundle).replace('<', r'\u003c') + ";\n"
    js += """window.fetch=async(url)=>{const u=new URL(url,'http://offline');
const value=u.pathname==='/api/demo'?captured.demo:u.pathname==='/api/catalog'?captured.catalog:
u.pathname==='/api/replay'?captured.replays[u.searchParams.get('plan_id')]:
u.pathname==='/api/comparison'?captured.comparison:null;
return {ok:value!==null&&value!==undefined,json:async()=>value||{error:'Live solving is unavailable in offline capture'}};};\n"""
    js += (static / "app.js").read_text()
    html = html.replace("</body>", "<script>" + js.replace("</script", r"<\/script") + "</script></body>")
    args.output.mkdir(parents=True, exist_ok=True)
    last = len(payload["frames"]) - 1
    with tempfile.TemporaryDirectory(prefix="stowlm-ui-capture-") as directory:
        temp = Path(directory)
        page = temp / "preview.html"
        page.write_text(html)
        captures = [("yard-small.png", 0, ""), ("bay-small.png", last, ""),
                    ("mid-playback-small.png", last // 2, "")]
        captures.extend((f"checker-{panel}-small.png", last, f"&view=checker&panel={panel}")
                        for panel in ("stability", "strength", "dg", "violations"))
        if bundle["comparison"] is not None:
            captures.append(("comparison-small.png", last, "&view=compare"))
        for name, frame, query in captures:
            target = (args.output / name).resolve()
            subprocess.run([chrome, "--headless", "--disable-gpu", "--no-first-run",
                            "--no-default-browser-check", "--window-size=1920,1080",
                            "--hide-scrollbars", "--virtual-time-budget=1500",
                            f"--user-data-dir={temp / 'chrome-profile'}",
                            f"--screenshot={target}", page.as_uri() + f"?frame={frame}{query}"],
                           check=True, timeout=45, stdout=subprocess.DEVNULL)
            if not target.is_file():
                raise RuntimeError(f"Browser did not write {target}")
            print(f"{target} (frame {frame}/{last}, {instance.id})")


if __name__ == "__main__":
    main()
