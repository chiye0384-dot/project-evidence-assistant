"""Finite local prose-shape labels; never rejected keys, values or prose."""
from .phase_diagnostics import PhaseContractError

SHAPE_DIAGNOSTICS=frozenset({'prose.action_not_object','prose.blocks_missing','prose.top_fields','prose.blocks_not_object'})

def safe_shape_diagnostics(value):
    if type(value) not in (tuple,list):return []
    result=[]
    for label in value[:4]:
        if type(label) is str and label in SHAPE_DIAGNOSTICS and label not in result:result.append(label)
    return result

def prose_shape_error(action):
    if not isinstance(action,dict):label='prose.action_not_object'
    elif 'blocks' not in action:label='prose.blocks_missing'
    elif set(action)!={'blocks'}:label='prose.top_fields'
    elif not isinstance(action['blocks'],dict):label='prose.blocks_not_object'
    else:label=None
    error=PhaseContractError('prose_shape')
    error.shape_diagnostics=(label,) if label is not None else ()
    return error
