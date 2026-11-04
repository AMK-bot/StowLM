# Pipeline freeze

See [FREEZE.md](FREEZE.md) for the two scoped freeze commands, pinned inputs, and launch
verification. Nothing is frozen by installing this folder or running its tests.

`build.build_manifest(root, no_weights=False, scope='pipeline')` returns a JSON-able inventory.
Pipeline scope excludes app/UI and generated products; film scope pins them,
requires a verified pipeline manifest, and checks product receipt pipeline hashes.
Defaults are `manifest-pipeline.json` and `manifest-film.json`.
`verify.verify(manifest_path)` returns a JSON-able list of file drifts.
`verify.require_frozen(manifest_path, *, root=None, full_weights=False)` rejects
dry or drifted manifests and returns the manifest and tree identities. Neither
loads a model, invokes a paid API, or applies a patch.

The unit tests use temporary fake trees and the synthetic JUnit recording in
`fixtures/suites.xml`; they need no network or GPU. A dry build measures hashing
time on the live tree. It excludes base-model weights and cannot stand in for the
final freeze. Full suite outcomes are measured only by `suites.sh` at freeze time.
Under D277, build the pipeline manifest, let p111 rebuild the hero package, then
resume `freeze.sh --scope pipeline --after-hero` to run both suites.

Hashes bind bytes, not correctness. Fast launch verification checks all weight
file sizes and modification times; the sealed guard rehashes their contents.
Only public signing keys are pinned. Generic JUnit cannot
identify non-strict xpasses separately; the counts report this limitation.
