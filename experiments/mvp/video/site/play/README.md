# StowLM: Play it yourself (a recorded run)

This folder is a recorded run of the StowLM Functional app (`/planner?live=1`): the same page, scripts and styles as the app,
with every answer the server gave recorded from one run of the app on 2026-11-04 and replayed here. Nothing is
installed and no request leaves this folder. Synthetic ships and cargo, simulated port.

The recorded track: feeder_500, medium section, fill 12 %, random_yard, laem_chabang_like, fresh seed
1014265040 (call `c20261104-030907-b8bb31`). Muse Spark read the call's synthetic cargo papers, 3 bookings at once ($0.24 in all), with no open questions: the 17 boxes planned are Muse Spark's load list. The spoken instruction "Keep bay three empty on this call." (a macOS text-to-speech clip, labelled generated voice) went to Muse Voice Transcribe, which heard "Keep bay three empty empty on this call..". The StowLM Reader refused that sentence (it is built to refuse what it can't read as one of its four rules), so the planner corrected the transcript to "Keep bay three empty on this call.". The StowLM Reader (fine-tuned Muse Glimmer 30B) read the planner's corrected sentence as "Nothing loads into bay 03.", and the planner confirmed it. Engine 0.13.0 planned
and the full checker decided.

You press the same buttons as in the app. Long stages replay faster than they ran, and the page says so; every time and number
shown is the one measured in the recorded run. The inputs are fixed: changing any of them runs nothing and points to the
install steps. No API key is needed or accepted. The spoken clip's audio isn't included in this copy (generated speech, D309);
its transcript is. Stress test and Ask copilot aren't in the Functional app yet, and say so here as they do there.

To run the app on your own inputs, follow the install steps at https://github.com/AMK-bot/StowLM#setup-and-run, then:

    uv run python -m app --functional --port 8794
    # open http://127.0.0.1:8794/planner?live=1

Built by `experiments/mvp/ui/static_play/build.py` on 2026-11-04. Serve this folder with any static server
(`python -m http.server`), or open it on the project's GitHub Pages site.
