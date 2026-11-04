"""Batched heterogeneous-prompt greedy audit using installed mlx-lm batching.

No GPU imports occur until generate_audit_batch. Each map retains its independent
production/lookahead/securing decoder; every resulting plan still needs checking.
"""
from llm.exit.outcome_epoch import process_snapshot, assert_current as assert_audit_epoch
_LOADED_AUDIT_EPOCH=process_snapshot()

from pathlib import Path
import time


def drain_generator(generator,decoders,*,on_result):
    """Small CPU-testable UID routing contract; the generator owns KV row drops."""
    emitted={uid:[] for uid in decoders}
    finished=set()
    while len(finished)<len(decoders):
        responses=generator.next_generated()
        if not responses:raise RuntimeError('audit batch exhausted before every UID finished')
        for response in responses:
            uid=response.uid
            if uid not in decoders or uid in finished:
                raise RuntimeError('audit batch returned unknown or duplicate finished UID')
            emitted[uid].append(int(response.token))
            if response.finish_reason is not None:
                decoder=decoders[uid]
                status=decoder.status if decoder.status!='running' else 'max_tokens'
                finished.add(uid)
                on_result(uid,decoder,status,emitted[uid])


def generate_audit_batch(paths,parent,*,batch_size=4,max_tokens=20000,corpus_root=None,
                         identity_role='audit',on_result=None):
    assert_audit_epoch(_LOADED_AUDIT_EPOCH)
    if batch_size<1 or max_tokens<1:raise ValueError('audit batch size and max_tokens must be positive')
    if not paths:return []
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if rule_freeze_v1_enabled():raise RuntimeError('audit requires rule-freeze switch OFF')
    from llm.exit.identity import identity_guard,DEFAULT_CORPUS
    from stowage.io import load_instance
    instances=[load_instance(p) for p in paths]
    guard=identity_guard(corpus_root or DEFAULT_CORPUS)
    for instance,path in zip(instances,paths):
        guard.require_allowed(instance,role=identity_role,input_path=path)
    from llm.exit import runtime
    from llm.stowlm.planner import StowLMPlanner
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.format_prompt import encode_prompt
    from llm.stowlm.sft_data import codec_for
    from stowage.feasibility import FeasibilityConfig,make_feasibility_engine
    from stowage.actions import get_action_space
    started=time.monotonic()
    with runtime._LOCK:
        planner=StowLMPlanner(adapter_path=str(parent))
        model=runtime._resident_for(planner)
        from mlx_lm.generate import BatchGenerator
        from llm.stowlm.mask_mlx import MaskLogitsProcessor
        tokenizer=load_tokenizer();decoders=[];prompts=[];processors=[];stop_tokens=[]
        for instance in instances:
            codec=codec_for(instance,tokenizer)
            if len(codec.end_plan_tokens)!=1:
                raise RuntimeError('batched audit requires single-token END_PLAN')
            prompt=list(encode_prompt(instance,tokenizer))
            engine=make_feasibility_engine(instance,get_action_space(),
                config=FeasibilityConfig(extra={'global_screen':0}))
            decoder=runtime.decoder_factory()(instance,engine,codec,None,1)[0]
            decoders.append(decoder);prompts.append(prompt)
            processors.append([MaskLogitsProcessor(decoder,len(prompt),codec.end_plan_tokens[0])])
            stop_tokens.append(tuple(codec.end_plan_tokens))
        if len(set(stop_tokens))!=1:raise ValueError('audit stop tokens differ across maps')
        generator=BatchGenerator(model,max_tokens=max_tokens,stop_tokens=[list(stop_tokens[0])],
            completion_batch_size=batch_size,prefill_batch_size=batch_size,prefill_step_size=512)
        rows=[None]*len(paths)
        try:
            uids=generator.insert(prompts,logits_processors=processors)
            if len(uids)!=len(paths) or len(set(uids))!=len(paths):
                raise RuntimeError('audit insertion UID count or uniqueness mismatch')
            indices=dict(zip(uids,range(len(paths))))
            def completed(uid,decoder,status,tokens):
                from llm.exit.securing_guard import securing_metrics
                index=indices[uid]
                plan=decoder.engine.to_plan() if status=='complete' else None
                if plan is not None:
                    plan=plan.model_copy(update={'provenance':plan.provenance.merge(planner.provenance)})
                row=dict(status=status,plan=plan,tokens=tokens,
                         elapsed_s=time.monotonic()-started,batch_size=batch_size,
                         securing=securing_metrics([decoder]),
                         memory_config=runtime._RESIDENT.get('mlx_cache') if runtime._RESIDENT else None)
                rows[index]=row
                if on_result:on_result(index,row)
            drain_generator(generator,dict(zip(uids,decoders)),on_result=completed)
        finally:generator.close()
    assert_audit_epoch(_LOADED_AUDIT_EPOCH)
    return rows
