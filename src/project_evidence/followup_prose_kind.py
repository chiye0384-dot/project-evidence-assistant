"""Isolated new prose wire. No I/O, guessing, fallback, repair or retries."""
from copy import deepcopy
import json
from project_evidence.followup_prose_wire import phase_schema as literal_schema,assemble as original_assemble
from project_evidence.followup_claim_parts import phase_schema as parts_schema
from project_evidence.phase_diagnostics import PhaseContractError
from project_evidence.followup_prose_wire import PROSE_NOTICE
from project_evidence.followup_two_phase import rendering_payload

FORMAT='kind_prose_v1'
FLAT_FORMAT='flat_prose_v1'
FLAT_KEYS = {'block_index': 'i', 'heading': 'h', 'text': 't'}
FLAT_NOTICE = (
    '本轮正文使用平铺字段。成功action只含kind=prose和blocks。'
    '若冻结N块，blocks必须且只能包含i1,h1,t1至iN,hN,tN。'
    'iK为整数K，hK为第K块标题，tK为第K块正文。blocks各字段直接对应值，不再套块对象。'
    '严格对应各块冻结结论及引用，保持完整事实与原文字长度。缺证据可返回原final gap或clarify。'
)
NOTICE=('新正文载体kind_prose_v1：成功正文action必须且只能包含kind与blocks两个字段，kind固定为字符串prose。'
    'blocks仍是block_1至block_N的冻结索引对象，每块仍只能含block_index、heading、text。'
    '不能增加status、final_output或其他顶层字段；不能把kind放入blocks或单块。'
    '缺证据仍可按原合同返回kind=final且final_output为gap或clarify；不是成功正文。'
    '不增加/交换块、不改引用、语义标准或预算，不输出内部思考。宿主只解码明确的新载体；'
    '未知字段、缺少/错误kind或旧仅blocks载体均拒绝，不补标签、不删字段、不修答或重试。')
LEGACY='prose阶段：引用计划和块顺序已经冻结。只返回blocks，'
DECLARED='prose阶段：引用计划和块顺序已经冻结。成功正文action只返回kind与blocks，kind必须为prose，'

def phase_schema(context,phase,block_count=None,*,planning_format='literal'):
    if planning_format not in ('literal','selected_parts_v1'):raise PhaseContractError('prose_shape')
    base=(literal_schema if planning_format=='literal' else parts_schema)(context,phase,block_count)
    if phase!='prose':return base
    result=deepcopy(base);prose=result['oneOf'][0]
    prose['properties']['kind']=dict(type='string',const='prose')
    prose['required']=['kind','blocks']
    return result

def prose_instruction(instruction):
    if not isinstance(instruction,str) or instruction.count(LEGACY)!=1:raise PhaseContractError('prose_shape')
    return instruction.replace(LEGACY,DECLARED,1)+'\n'+NOTICE

def assemble(frozen,action):
    # Only this exact declared wire is decoded. Legacy or unknown fields fail
    # before projection; this is not a generic field-stripping repair hook.
    if (not isinstance(action,dict) or set(action)!={'kind','blocks'}
        or type(action.get('kind')) is not str or action['kind']!='prose'):
        raise PhaseContractError('prose_shape')
    try:raw=json.dumps(action,ensure_ascii=False,allow_nan=False).encode('utf-8')
    except (ValueError,TypeError):raise PhaseContractError('prose_shape') from None
    if len(raw)>128000:raise PhaseContractError('phase_payload_limit')
    return original_assemble(frozen,dict(blocks=deepcopy(action['blocks'])))


def flat_phase_schema(context, phase, block_count=None, *, planning_format='literal'):
    result = phase_schema(context, phase, block_count, planning_format=planning_format)
    if phase != 'prose':
        return result
    slots = result['oneOf'][0]['properties']['blocks']
    flat = {}
    for index, block in enumerate(slots['properties'].values(), 1):
        flat.update({f'{FLAT_KEYS[name]}{index}': field for name, field in block['properties'].items()})
    slots.update(properties=flat, required=list(flat))
    return result


def flat_prose_instruction(text):
    if text.count(PROSE_NOTICE) != 1:
        raise PhaseContractError('prose_shape')
    transformed = prose_instruction(text).replace(PROSE_NOTICE, FLAT_NOTICE, 1)
    if not transformed.endswith('\n' + NOTICE):
        raise PhaseContractError('prose_shape')
    return transformed.removesuffix('\n' + NOTICE)


def assemble_flat(frozen, action):
    """Decode only the explicitly selected flat carrier; never infer a format."""
    if type(action) is not dict or set(action) != {'kind', 'blocks'} or action['kind'] != 'prose':
        raise PhaseContractError('prose_shape')
    try:
        size = len(json.dumps(action, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    except (TypeError, ValueError):
        raise PhaseContractError('prose_shape') from None
    if size > 128_000:
        raise PhaseContractError('phase_payload_limit')
    count = len(rendering_payload(frozen)['plan']['blocks'])
    required = {f'{prefix}{index}' for index in range(1, count + 1) for prefix in FLAT_KEYS.values()}
    blocks = action['blocks']
    if type(blocks) is not dict or set(blocks) != required:
        raise PhaseContractError('block_count')
    decoded = {f'block_{index}': {name: blocks[f'{prefix}{index}'] for name, prefix in FLAT_KEYS.items()}
               for index in range(1, count + 1)}
    return assemble(frozen, dict(kind='prose', blocks=decoded))
