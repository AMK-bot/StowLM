# Local StowLM Reader setup

The code export contains no model weights or adapter tensors. The Reader runs
on Apple silicon with MLX and the locked Python 3.12 environment.

Follow [Live StowLM Reader on Apple silicon](../README.md#live-stowlm-reader-on-apple-silicon)
in the root README for the pinned Glimmer base conversion recipe, separately
published adopted adapter, its Hugging Face repository and immutable revision,
and checksum checks. Base conversion has not been shown to reproduce historical
weight bytes.

The base belongs in `models/glimmer-30b-4bit/`. Install the adopted adapter in
`models/stowlm-reader/`, then start the Functional app from the repository root:

```sh
uv sync --python 3.12 --locked
uv run python -m app --functional --reader-adapter models/stowlm-reader
```

Open `/planner?live=1` on the printed localhost address. See
[Functional app setup](../README.md#functional-app-fresh-synthetic-inputs) for
the recipient's own cloud key, paid-call consent and launch cap. The local
Reader does not need a cloud key; Muse Spark and Muse Voice do.

Separately, `uv run python -m stowage.data_setup` installs the full checker's
SHA256-checked public hull data. It does not install model weights. An offline
user can supply `--archive /path/to/kcs_offsets.ZIP` instead of downloading it.

The server starts with missing weights or adapter and reports the unavailable
Reader. Continuing with no instruction requires the planner's explicit choice.
The Demo app's `/planner?demo=1` playback uses stored plans and recorded answers
and needs no local model or API key. No live inference is established by this
setup guide.
