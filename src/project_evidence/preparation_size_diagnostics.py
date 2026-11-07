"""Isolated draft: fixed preparation-stage enums and bounded integer sizes only."""
import json
STAGES=frozenset({'upper_model','upper_base_view','upper_projection','actual_base_view','actual_projection','final_bound'})
PHASES=frozenset({'plan_or_tools','prose'})
NUMBERS=frozenset({'model_compact_chars','model_spaced_chars','phase_compact_chars','base_view_chars',
    'model_wrapper_chars','host_with_view_chars','previous_floor','upper_bound','actual_projected_chars',
    'context_limit','request_utf8_bytes','request_limit'})
REQUIRED={
    'upper_model':{'model_spaced_chars'},
    'upper_base_view':{'base_view_chars'},'actual_base_view':{'base_view_chars'},
    'upper_projection':{'model_wrapper_chars','host_with_view_chars'},
    'actual_projection':{'model_wrapper_chars','host_with_view_chars'},
    'final_bound':{'actual_projected_chars','upper_bound','previous_floor'},
}
def safe_size(value):
    if type(value) is not dict or not set(value)<={'phase','stage'}|NUMBERS:return None
    phase,stage=value.get('phase'),value.get('stage')
    if type(phase) is not str or phase not in PHASES or type(stage) is not str or stage not in STAGES:return None
    if not {'context_limit'}|REQUIRED[stage]<=set(value):return None
    if any(type(v) is not int or not 0<=v<=262145 for k,v in value.items() if k in NUMBERS):return None
    if not 1<=value['context_limit']<=24000:return None
    if 'request_limit' in value and not 1<=value['request_limit']<=128000:return None
    if any(value.get(k,0)>value['context_limit'] for k in ('previous_floor','upper_bound')):return None
    result={k:v for k,v in value.items()}
    if len(json.dumps(result,ensure_ascii=False).encode())>640:return None
    return result
def context_sizes(context,phase,payload):
    try:
        value=context.model_dump() if hasattr(context,'model_dump') else context
        compact=lambda v:json.dumps(v,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        return dict(model_compact_chars=len(compact(value)),model_spaced_chars=len(json.dumps(value,ensure_ascii=False,allow_nan=False)),
            phase_compact_chars=len(compact(dict(context=value,phase=phase,payload=payload))))
    except Exception:return {}
def _supported_error(error):
    try:
        labels=getattr(error,'diagnostics',())
        return (getattr(error,'code',None)=='output_limit' and getattr(error,'request_sent',None) is False
            and type(labels) is tuple and all(type(x) is str for x in labels) and 'context.projected_limit' in labels)
    except Exception:return False
def error_size(error):
    if not _supported_error(error):return None
    return safe_size(getattr(error,'preparation_size',None))
def attach(error,stage,phase,**numbers):
    if not _supported_error(error):return error
    clean=safe_size(dict(phase=phase,stage=stage,**numbers))
    if clean is not None:error.preparation_size=clean
    return error
