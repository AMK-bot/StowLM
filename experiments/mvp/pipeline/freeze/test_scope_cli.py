"""Readonly wrapper previews; these tests never enter suite execution."""
from pathlib import Path
import shlex
import subprocess

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.parametrize('scope', ['pipeline', 'film'])
def test_wrapper_scope_preview_routes_build_and_guard(scope):
    result = subprocess.run(['bash', str(HERE / 'freeze.sh'), '--scope', scope, '--print-commands'],
                            capture_output=True, text=True, check=True, timeout=5)
    commands = [shlex.split(line) for line in result.stdout.splitlines()]
    assert len(commands) == (3 if scope == 'film' else 2)
    if scope == 'film':
        assert commands[0][-1] == str(HERE / 'suites' / scope)
    build, guard = commands[-2:]
    assert build[build.index('--scope') + 1] == scope
    assert guard[-1] == str(HERE / f'manifest-{scope}.json')
    assert ('--pipeline-manifest' in build) == (scope == 'film')


def test_pipeline_suite_resume_checks_hero_before_suites_without_rebuilding():
    result = subprocess.run(['bash', str(HERE / 'freeze.sh'), '--after-hero', '--print-commands'],
                            capture_output=True, text=True, check=True, timeout=5)
    commands = [shlex.split(line) for line in result.stdout.splitlines()]
    assert len(commands) == 4
    guard, hero, suites, final_guard = commands
    assert guard == final_guard
    assert '--require-frozen' in guard
    assert hero[hero.index('--pipeline-manifest') + 1] == str(HERE / 'manifest-pipeline.json')
    assert hero[hero.index('--only') + 1] == 'experiments/mvp/pipeline/package/fixtures/hero-run'
    assert suites[-1] == str(HERE / 'suites/pipeline')
    assert not any('build.py' in part for command in commands for part in command)


def test_wrapper_defaults_to_pipeline_without_executing():
    result = subprocess.run(['bash', str(HERE / 'freeze.sh'), '--print-commands'],
                            capture_output=True, text=True, check=True, timeout=5)
    assert '--scope pipeline' in result.stdout
    assert 'manifest-pipeline.json' in result.stdout


@pytest.mark.parametrize('arguments', [['--scope'], ['--scope', 'unknown'], ['--unknown'],
                                       ['--scope', 'film', '--after-hero']])
def test_wrapper_rejects_bad_scope_arguments_before_work(arguments):
    result = subprocess.run(['bash', str(HERE / 'freeze.sh'), *arguments],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
