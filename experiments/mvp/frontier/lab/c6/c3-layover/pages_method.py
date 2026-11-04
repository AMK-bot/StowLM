"""Fresh cold C4 pages with its unchanged recorded numerical work profile.

Only failed checked recipe retention differs: the comparator exposes its best
failed concrete plan for tier classification after doing the same work.
"""
import importlib.util
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
PROFILE_PATH=HERE/'pages_copy/PROFILE.json'


def _module():
    name='_c6_fresh_frozen_pages'
    if name not in sys.modules:
        path=HERE/'pages_copy/method.py'
        sys.path.insert(0,str(path.parent))
        spec=importlib.util.spec_from_file_location(name,path)
        module=importlib.util.module_from_spec(spec)
        sys.modules[name]=module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _context(context):
    if '_frozen_pages_context' not in context:
        profile=json.loads(PROFILE_PATH.read_text())
        native=profile['native_settings']
        effective=dict(work_config=profile['work_config'],
            finishing_config=profile['finishing_config'],
            finalize_s=native['finalize_s'],tries=native['tries'],seed=native['seed'],
            budget_s=60.)
        # Preserve the original C4 work dictionaries even when primary LP work
        # options use a different schema. The problem is still the same input.
        inner=dict(context,work_config=effective['work_config'],
            finishing_config=effective['finishing_config'],
            finalize_s=effective['finalize_s'],tries=effective['tries'],
            observed_plans=context.setdefault('observed_plans',[]))
        context['_frozen_pages_context']=inner
        context['effective_page_profile']=effective
    return context['_frozen_pages_context']


def propose(instance, *, seed, budget_s, context):
    inner=_context(context)
    effective=context['effective_page_profile']
    return _module().propose(instance,seed=effective['seed'],
        budget_s=effective['budget_s'],context=inner)


def finish(instance,plan,*,seed,finalize_s,label,context):
    inner=_context(context)
    return _module().finish(instance,plan,seed=context['effective_page_profile']['seed'],finalize_s=inner['finalize_s'],
                            label=label,context=inner)
