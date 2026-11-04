"""Scale probe (D102/D174): the 9.1 recipe, unchanged, through eval-c1/run_c1.py, plus a heartbeat of how far the decode
got. Read-only on src/: it only wraps generate_backtracking in this process. Writes <run-dir>/progress.jsonl (time,
actions, tokens, distinct boxes aboard, memory) and <run-dir>/decode-<id>.json (final actions and decode stats, also on
a timeout). The trace replay is skipped: it costs a second full mask pass after a timeout and isn't part of the recipe.
Usage: uv run python experiments/mvp/strategy/scale/run_scale.py <run_c1 args>"""
import json, resource, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT/'experiments/eval-c1'), str(ROOT/'src')]
STEP_EVERY = 25


def rss_gb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9


def boxes_aboard(actions):
    return len({getattr(a, 'box', None) for a in actions} - {None})


def main():
    import mlx.core as mx
    import llm.stowlm.backtrack as bt
    import pooled_planner
    import run_c1

    run_dir = Path(sys.argv[sys.argv.index('--run-dir') + 1])
    run_dir.mkdir(parents=True, exist_ok=True)
    log = (run_dir/'progress.jsonl').open('a')
    original = bt.generate_backtracking

    def write(row):
        log.write(json.dumps(row) + '\n')
        log.flush()

    def wrapped(model, prompt, decoder, *, check_deadline=None, **kw):
        t0 = time.perf_counter()
        inner_id = id(decoder)
        write(dict(event='decode_start', t=time.time(), prompt_tokens=len(prompt), decoder=inner_id))
        calls = [0]
        last = [t0]

        def beat():
            calls[0] += 1
            if calls[0] % STEP_EVERY == 0:
                now = time.perf_counter()
                write(dict(event='beat', elapsed_s=round(now - t0, 1), steps=calls[0], tokens=len(decoder.tokens),
                           actions=len(decoder.actions), boxes=boxes_aboard(decoder.actions),
                           s_per_step=round((now - last[0])/STEP_EVERY, 3), peak_gb=round(mx.get_peak_memory()/1e9, 2),
                           active_gb=round(mx.get_active_memory()/1e9, 2), rss_max_gb=round(rss_gb(), 2)))
                last[0] = now
            if check_deadline is not None:
                check_deadline()
        outcome = {}
        try:
            result, stats = original(model, prompt, decoder, check_deadline=beat, **kw)
            outcome = dict(status=result.status, stats=stats)
            return result, stats
        except BaseException as exc:
            outcome = dict(status='exception', exception=type(exc).__name__)
            raise
        finally:
            dead = getattr(decoder, 'dead_end', None)
            write(dict(event='decode_end', elapsed_s=round(time.perf_counter() - t0, 1), steps=calls[0],
                       tokens=len(decoder.tokens), actions=len(decoder.actions), boxes=boxes_aboard(decoder.actions),
                       decoder_status=decoder.status, dead_end=None if dead is None else str(dead)[:600],
                       peak_gb=round(mx.get_peak_memory()/1e9, 2), rss_max_gb=round(rss_gb(), 2), **outcome))
            (run_dir/f'decode-{inner_id}.json').write_text(json.dumps(dict(
                actions=[str(a) for a in decoder.actions], dead_end=None if dead is None else str(dead)[:2000],
                decoder_status=decoder.status, **outcome), default=str))

    bt.generate_backtracking = wrapped
    pooled_planner.PooledStowLMPlanner._write_trace = lambda self, instance, metrics: {'trace_note': 'skipped: scale probe'}
    run_c1.main()


if __name__ == '__main__':
    main()
