"""Isolated opt-in planning wire; exact rendering over model-selected coordinates.

No I/O, selection, source lookup, text deletion, response repair or retry.
Enabled only by selected_parts_v1. Original freeze and validators remain.
"""
from copy import deepcopy
import json
import re

from project_evidence.followup_wire import LINE_PATTERN
from project_evidence.followup_prose_wire import phase_schema as original_schema
from project_evidence.followup_two_phase import freeze_plan as original_freeze
from project_evidence.phase_diagnostics import PhaseContractError

FORMAT = 'selected_parts_v1'

NOTICE = ('隔离候选规划载体：仅citation_plan块用claim_parts代替claim_summary，其余证据字段与权限不变。'
    'claim_parts按顺序连接，text片段为{kind:"text",text:"原文字"}，position片段为'
    '{kind:"position",reference_index:1,line_index:1}；两个index从1开始，分别指本块source_lines中的引用'
    '及该引用line_numbers中已明确选择的第几个行号。位置片段仅展开为对应的“第N行”，不选新行、不跨块查找。'
    '先选择完整支持结论的行再组合片段，所选位置仍须支持结论语义。text可保留必要行号、业务数值、版本、'
    '阈值和代码字面量，原文逐字保留，其中显式行号仍由原同块校验检查，不禁止或统一隐去编号。'
    '指针越界、缺必要证据、类型错误均停止；宿主不补引、不删改文字、不猜行、不重试。'
    '展开后形成内部claim_summary，再经全部原证据/角色/同块行校验冻结。prose合同不变，必要定位必须在最终答案保留。')

def need(value):
    if not value:raise PhaseContractError('claim_summary')

def bounded(value):
    try:raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
    except (TypeError,ValueError):raise PhaseContractError('claim_summary') from None
    if len(raw)>128000:raise PhaseContractError('phase_payload_limit')

def parts_schema():
    return dict(type='array',minItems=1,maxItems=16,items={'oneOf':[
        dict(type='object',additionalProperties=False,required=['kind','text'],properties=dict(
            kind=dict(type='string',const='text'),text=dict(type='string',minLength=1,maxLength=1200))),
        dict(type='object',additionalProperties=False,required=['kind','reference_index','line_index'],properties=dict(
            kind=dict(type='string',const='position'),reference_index=dict(type='integer',minimum=1,maximum=16),
            line_index=dict(type='integer',minimum=1,maximum=20)))
    ]},description='Ordered literal text and block-local selected-line pointers. Rendered summary remains at most1200 characters; original citation/semantic criteria apply.')

def phase_schema(context,phase,block_count=None):
    original=original_schema(context,phase,block_count)
    if phase!='plan_or_tools':return original
    result=deepcopy(original)
    def visit(value):
        if isinstance(value,list):
            for item in value:visit(item)
        elif isinstance(value,dict):
            properties=value.get('properties')
            if isinstance(properties,dict) and 'claim_summary' in properties:
                properties.pop('claim_summary');properties['claim_parts']=parts_schema()
                value['required']=['claim_parts' if name=='claim_summary' else name for name in value['required']]
            for item in value.values():visit(item)
    visit(result)
    return result

def expand_plan(value):
    """Deterministic decoding of a new declared wire, never repair an old claim."""
    bounded(value)
    need(isinstance(value,dict) and set(value)=={'kind','topic','section_id','title','blocks','gaps'} and value['kind']=='citation_plan')
    need(isinstance(value['blocks'],list) and 1<=len(value['blocks'])<=12)
    result=deepcopy(value)
    fields={'category','support','evidence_plan','evidence_ids','source_lines','claim_parts'}
    for block in result['blocks']:
        need(isinstance(block,dict) and set(block)==fields)
        parts=block.pop('claim_parts');need(isinstance(parts,list) and 1<=len(parts)<=16)
        need(isinstance(block['source_lines'],list) and len(block['source_lines'])<=16)
        text=[]
        for part in parts:
            need(isinstance(part,dict))
            if part.get('kind')=='text':
                need(set(part)=={'kind','text'} and isinstance(part['text'],str) and 1<=len(part['text'])<=1200)
                text.append(part['text'])
            else:
                need(part.get('kind')=='position' and set(part)=={'kind','reference_index','line_index'})
                ri,li=part['reference_index'],part['line_index']
                need(type(ri) is int and type(li) is int and 1<=ri<=16 and 1<=li<=20)
                need(ri<=len(block['source_lines']))
                ref=block['source_lines'][ri-1]
                need(isinstance(ref,dict) and set(ref)=={'evidence_id','line_numbers'})
                raw=ref['line_numbers'];need(isinstance(raw,str) and re.fullmatch(LINE_PATTERN,raw) is not None)
                selected=raw.split(',');need(li<=len(selected))
                text.append('第'+selected[li-1]+'行')
        rendered=''.join(text);need(1<=len(rendered)<=1200)
        block['claim_summary']=rendered
    bounded(result)
    return result

def freeze_plan(value,observed_references,check_citations):
    return original_freeze(expand_plan(value),observed_references,check_citations)
