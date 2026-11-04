"""Complete model-oriented run-contract keys for classical reference producers."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def main():
    for method in ('twostep', 'solver', 'exact', 'bounds'):
        path = ROOT / f'experiments/mvp/runs/test-{method}/manifest.json'
        manifest = json.loads(path.read_text())
        assert manifest['complete'], f'Run is incomplete: {method}'
        versions = manifest['component_versions']
        manifest.update(
            base_model_revision=None, quantisation=None, adapter_lineage=None,
            adapter_data_mode=None, adapter_stubs=None, mlx_version=None,
            mlx_lm_version=None, mask_version=None,
            feasibility_version=versions['feasibility'], checker_version=versions['checker'],
            prompt_version=None, format_version=None, epoch_id=None,
            instance_root_sha256=None,
            decoding=dict(temperature=None, top_p=None, samples_per_instance=None,
                          seed=manifest['config']['seed'], pool_size=None, c2_enabled=False),
            dead_ends=None,
            contract_scope='Classical reference: neural identity and decoding fields are not applicable; no neural trace is fabricated. MVP inputs use SHA256SUMS pins, not the historical harness epoch.',
        )
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + '\n')
        print(f'{method}: complete, {manifest["n_records"]} records')


if __name__ == '__main__':
    main()
