"""Isolated new prose wire. No I/O, guessing, fallback, repair or retries."""
from copy import deepcopy
import json
from project_evidence.followup_prose_wire import phase_schema as literal_schema,assemble as original_assemble
from project_evidence.followup_claim_parts import phase_schema as parts_schema
from project_evidence.phase_diagnostics import PhaseContractError

FORMAT='kind_prose_v1'
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
