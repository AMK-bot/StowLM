"""Actual MLX CPU save/rename/load suffix probe; no model or GPU evaluation."""
from pathlib import Path
import json
import os
import tempfile
import time


def main():
    started=time.monotonic()
    try:
        import mlx.core as mx
    except ImportError as error:
        result=dict(status='unavailable',backend='actual mlx.core',error=str(error),
            gpu_evaluation=False,wall_s=time.monotonic()-started)
        Path(__file__).with_name('mlx-cpu-result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
        raise SystemExit(2) from error
    mx.set_default_device(mx.cpu)
    assert mx.default_device()==mx.cpu
    with tempfile.TemporaryDirectory(prefix='d136-safetensors-cpu-') as directory:
        root=Path(directory)
        arrays={'layer.lora_a':mx.array([[.25,-.5]],dtype=mx.float32)}
        mx.eval(arrays)
        bad=root/'0000001_adapters.safetensors.tmp'
        mx.save_safetensors(str(bad),arrays)
        actual=Path(str(bad)+'.safetensors')
        assert not bad.exists() and actual.exists()
        try:os.replace(bad,root/'bad-final.safetensors')
        except FileNotFoundError:pass
        else:raise AssertionError('original filename unexpectedly renamed')
        for kind in ('adapters','optimizer'):
            temporary=root/f'.tmp-0000001_{kind}.safetensors'
            final=root/f'0000001_{kind}.safetensors'
            mx.save_safetensors(str(temporary),arrays)
            assert temporary.exists()
            os.replace(temporary,final)
            restored=mx.load(str(final))
            mx.eval(restored)
            assert restored['layer.lora_a'].tolist()==[[.25,-.5]]
        result=dict(passed=True,backend='actual mlx.core',device=str(mx.default_device()),
            original_failure_reproduced=True,patched_adapter_and_optimizer_reload=True,
            gpu_evaluation=False,wall_s=time.monotonic()-started)
        target=Path(__file__).with_name('mlx-cpu-result.json')
        target.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))


if __name__=='__main__':main()
