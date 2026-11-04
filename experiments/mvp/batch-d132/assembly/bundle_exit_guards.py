"""Archive ExIt fast guards, retargeting source loads to the installed package."""
import hashlib
import json
from pathlib import Path

from snapshot import ROOT

HERE = Path(__file__).resolve().parent


def main():
    names = [
        'experiments/mvp/exit/patches/d132/tests/test_outcome_epoch.py',
        'experiments/mvp/exit/patches/d132/tests/test_checker_epoch_ack.py',
        'experiments/mvp/exit/patches/d134-dataset/tests/test_dataset_pool.py',
        'experiments/mvp/exit/patches/d135-dpo-checkpoint/tests/test_checkpoint_suffix.py',
    ]
    output = HERE / 'package/guards/exit-v2'
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for name in names:
        source = ROOT / name
        original = source.read_bytes()
        text = original.decode()
        anchors = ['STAGE=Path(__file__).resolve().parents[1]', 'STAGE = Path(__file__).resolve().parents[1]']
        assert sum(text.count(anchor) for anchor in anchors) == 1
        for anchor in anchors:
            text = text.replace(anchor, "import llm.exit\nSTAGE = Path(llm.exit.__file__).resolve().parent")
        if source.name == 'test_checkpoint_suffix.py':
            assert "@pytest.mark.parametrize('source',['original','patched'])" in text
            text = text.replace("@pytest.mark.parametrize('source',['original','patched'])",
                                "@pytest.mark.parametrize('source',['patched'])")
        target = output / source.name
        target.write_text(text)
        records.append({'original_path': name, 'original_sha256': hashlib.sha256(original).hexdigest(),
                        'bundled_path': str(target.relative_to(ROOT)),
                        'bundled_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                        'adaptation': 'STAGE points to actual imported llm.exit; checkpoint positive branch only'})
        assert source.read_bytes() == original, 'Owner test changed during bundling'
    (HERE / 'exit-guard-source-pins.json').write_text(json.dumps({'guards': records,
        'boundary_exclusions': ['test_real_mvp_dataset_bytes_serial_vs_spawned',
                                'test_spawned_pool_order_fault_and_environment'],
        'reason': 'Boundary fast tests stay serial and independent of owner patch fixtures; integration qualified separately'},
        indent=2, sort_keys=True) + '\n')
    print(json.dumps({'bundled': len(records)}))


if __name__ == '__main__':
    main()
