"""The one sealed v3 score, on the adapter select_adapter picked.

Wraps gpu_run.score_final without editing it, because the recipe freeze hashes it:
- ADAPTER points at the selected folder;
- the final freeze binds that folder in place of adapter-v3, plus the selection;
- admission waits, in this process and for each sealed child, which retries if denied.

Tuned, untrained and the stopped v2 reference all run on the same sealed cases with
the same mask. results/ holds the sealed arms, gates and the adoption file. Gates
are reported whatever they say; a failed gate is a result, not an error.
Usage: python -m experiments.mvp.reader.v3.score_selected
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from experiments.mvp.reader.v3 import freeze as frozen_files, gpu_run, train
from experiments.mvp.reader.v3.finish_run import FINAL, RESULTS, RETRY_EXIT, log, wait_for_admission
from experiments.mvp.reader.v3.select_adapter import FREEZE, OUTPUT, RULE, SAMPLE
from experiments.mvp.reader.v3.train import HERE, READER, sha256

SUMMARY = RESULTS / 'sealed-summary.json'
STOPPED_START = HERE / 'adapter-v3'
ADMIT = train.check_admission  # the raising check; patched names below wrap this one


def admit():
    return wait_for_admission(check=ADMIT, note=log)


class WaitingSubprocess:
    """subprocess for gpu_run: sealed children retry; everything else passes through."""

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def run(self, command, check=False, **options):
        if 'experiments.mvp.reader.v3.evaluate' not in command:
            return subprocess.run(command, check=check, **options)
        position = command.index('experiments.mvp.reader.v3.evaluate')
        child = [*command[:position], 'experiments.mvp.reader.v3.finish_run', '--check-child',
                 *command[position + 1:]]
        while True:
            admit()
            done = subprocess.run(child, **options)
            if done.returncode != RETRY_EXIT:
                break
        if check and done.returncode:
            raise subprocess.CalledProcessError(done.returncode, child)
        return done


def selected_inputs(adapter, original):
    """freeze.inputs with the selected folder standing in for adapter-v3."""
    def inputs(stage, selected=None):
        files = [adapter / path.name if path.parent == STOPPED_START else path
                 for path in original(stage, selected)]
        if stage == 'final':
            files += [OUTPUT, FREEZE, RULE, SAMPLE, STOPPED_START / 'adapters.safetensors']
            files += [path for path in (FINAL / 'adapters.safetensors', FINAL / 'STOPPED.json')
                      if path.exists()]  # a crashed run has no STOPPED.json
        return files
    return inputs


def selected_freeze(adapter, recipe_freeze=gpu_run.freeze):
    frozen_files.inputs = selected_inputs(adapter, frozen_files.inputs)

    def run(stage, check=False):
        if stage == 'recipe':
            return recipe_freeze(stage, check=check)
        arguments, sys.argv = sys.argv, ['freeze', '--stage', stage, *(['--check'] if check else [])]
        try:
            return frozen_files.main()
        finally:
            sys.argv = arguments
    return run


def sealed_summary(selection, gates, adoption):
    return {'selected': selection['selected'], 'R1': gates['R1'], 'R2': gates['R2'], 'R3': gates['R3'],
            'all_gates_pass': gates['all_pass'],
            'no_regression_exact': adoption.get('no_worse_exact_match'),
            'no_regression_loosening': adoption.get('no_worse_accepted_loosening'),
            'adoption_status': adoption['status'], 'adopt_v3': adoption['adopt_v3'], 'gates': gates,
            'caveat': selection['caveat']}


def main():
    selection = json.loads(OUTPUT.read_text())
    adapter = Path(selection['selected']['path'])
    if sha256(adapter / 'adapters.safetensors') != selection['selected']['adapter_sha256']:
        raise ValueError('selected adapter changed')
    gpu_run.ADAPTER = adapter
    gpu_run.freeze = selected_freeze(adapter)
    gpu_run.subprocess = WaitingSubprocess()
    train.check_admission = admit
    gpu_run.score_final(HERE / 'sealed/spark-summary.json', READER / 'adapter-v2')
    read = lambda name: json.loads((gpu_run.RESULTS / name).read_text())
    adoption = 'adoption-ready.json' if (gpu_run.RESULTS / 'adoption-ready.json').exists() else 'adoption-pending.json'
    summary = sealed_summary(selection, read('gates.json'), read(adoption))
    SUMMARY.write_text(json.dumps(summary, indent=2) + '\n')
    log(event='sealed-summary', **{key: summary[key] for key in
                                   ('R1', 'R2', 'R3', 'no_regression_exact', 'no_regression_loosening', 'adoption_status')})


if __name__ == '__main__':
    main()
