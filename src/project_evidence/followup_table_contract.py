"""Host-owned selected table request contract; no source or evidence-directory access."""
from . import followup_parts_instruction as wording
from .followup_claim_parts import NOTICE as PARTS_NOTICE,phase_schema as parts_schema
from .followup_evidence_plan import CLAIM_ROLES
from .followup_prose_kind import LEGACY as PROSE_LEGACY,DECLARED as PROSE_DECLARED

FORMAT='selected_table_v1'
class ContractReject(ValueError):
    def __init__(self):super().__init__('table_contract.rejected')
def need(value):
    if not value:raise ContractReject()

RULES=('evidence_order必须为本块全部source_lines序号恰好一次的排列，保留其独立顺序。'
    '锚点仅含role、reference_index、line_index，两个index同position从1开始，且指向本块已选行；'
    '保留锚点顺序和claim_kind必要角色，最多8个，不自动补齐。缺口及无依据候选仍按原空引用规则。'
    '其他字段或类型非法即停；不新增模板，text/position与两种prose合同不变。'
    'literal中未选行的不一致仍可能生成，由原规则拒绝。')

REPLACEMENTS=(
    ('guide-gap-empty', 'prompts/follow-up-two-phase.txt',
     '这两类的evidence_plan.anchors、evidence_ids、source_lines都必须是空数组[]',
     '这两类的evidence_plan.anchors、evidence_order、source_lines都必须是空数组[]'),
    ('guide-submission-fields', 'prompts/follow-up-two-phase.txt',
     '每块先选category、support、evidence_plan、evidence_ids、source_lines，再按当前schema写有界claim_parts',
     '每块先选category、support、source_lines、evidence_order、evidence_plan，再按当前schema写有界claim_parts'),
    ('wording-coordinate-location', 'src/project_evidence/followup_claim_wording.py',
     '通常将位置坐标保留在本块source_lines和evidence_plan中；',
     '结构化绝对行号只在本块source_lines中选取，evidence_plan锚点只指本块已选位置；'),
    ('internal-versus-submitted', 'src/project_evidence/followup_parts_instruction.py', wording.SCOPE_NOTICE,
     'context.claim_contract及其中claim_summary、evidence_ids描述展开后的内部合同，不是本草案提交字段；'
     'citation_plan块提交category、support、source_lines、evidence_order、evidence_plan、claim_parts；'
     '内部evidence_plan锚点的evidence_id/line_number由提交的本块指针展开。'),
    ('parts-prefix-scope', 'src/project_evidence/followup_claim_parts.py',
     '隔离候选规划载体：仅citation_plan块用claim_parts代替claim_summary，其余证据字段与权限不变。',
     '选行表设计草案：source_lines是唯一显式选行表；evidence_order声明内部证据ID顺序，'
     'evidence_plan锚点提交本块指针，claim_parts展开为内部摘要；权限及原校验不变。'),
)

ANCHOR_BEFORE=('Explicit coordinates, not reasoning. Each must also occur in this block source_lines. '
    'Include all required roles for claim_kind; no automatic citations.')
ANCHOR_AFTER=('One-based block-local selected-line pointers, not absolute coordinates. '
    'Preserve anchor order and all required roles; at most8 anchors, with no automatic selection.')
ORDER_DESCRIPTION=('Permutation of1..len(source_lines), empty with no references. '
    'Preserves expanded evidence_ids order; evidence_ids is not a submitted field.')

def rewrite_system(system,*,prose_format='kind_prose_v1'):
    need(isinstance(system,str))
    need(prose_format in ('indexed','kind_prose_v1'))
    need(system.endswith('\n'+PARTS_NOTICE))
    need(RULES not in system)
    result=system
    for _,_,before,after in REPLACEMENTS:
        need(result.count(before)==1 and after not in result)
        result=result.replace(before,after,1)
    if prose_format=='kind_prose_v1':
        need(result.count(PROSE_LEGACY)==1 and PROSE_DECLARED not in result)
        result=result.replace(PROSE_LEGACY,PROSE_DECLARED,1)
    return result+'\n'+RULES

def restore_system(system,*,prose_format='kind_prose_v1'):
    need(isinstance(system,str) and system.endswith('\n'+RULES))
    need(prose_format in ('indexed','kind_prose_v1'))
    result=system[:-len('\n'+RULES)]
    if prose_format=='kind_prose_v1':
        need(result.count(PROSE_DECLARED)==1 and PROSE_LEGACY not in result)
        result=result.replace(PROSE_DECLARED,PROSE_LEGACY,1)
    for _,_,before,after in reversed(REPLACEMENTS):
        need(result.count(after)==1 and before not in result)
        result=result.replace(after,before,1)
    return result

def _table_schema(context):
    value=parts_schema(context,'plan_or_tools')
    def visit(node):
        if isinstance(node,list):
            for item in node:visit(item)
        elif isinstance(node,dict):
            props=node.get('properties',{})
            if 'claim_parts' in props:
                ids=props.pop('evidence_ids')
                props['evidence_order']=dict(type='array',minItems=ids.get('minItems',0),maxItems=ids.get('maxItems',16),
                    items=dict(type='integer',minimum=1,maximum=16),uniqueItems=True)
                node['required']=['evidence_order' if name=='evidence_ids' else name for name in node['required']]
                def anchors(n):
                    if isinstance(n,list):
                        for item in n:anchors(item)
                    elif isinstance(n,dict):
                        p=n.get('properties',{})
                        if set(p)=={'role','evidence_id','line_number'}:
                            p.pop('evidence_id');p.pop('line_number')
                            p.update(reference_index=dict(type='integer',minimum=1,maximum=16),
                                line_index=dict(type='integer',minimum=1,maximum=20))
                            n['required']=list(p)
                        for item in n.values():anchors(item)
                anchors(props['evidence_plan'])
            for item in node.values():visit(item)
    visit(value)
    return value

def schema(context):
    """Same162 A typed shape; only clarify descriptions and generation order."""
    value=_table_schema(context)
    stats=dict(blocks=0,anchors=0)
    def walk(node):
        if isinstance(node,list):
            for item in node:walk(item)
        elif isinstance(node,dict):
            props=node.get('properties',{})
            if 'evidence_order' in props and 'claim_parts' in props:
                need(set(props)=={'category','support','source_lines','evidence_order','evidence_plan','claim_parts'})
                props['evidence_order']['description']=ORDER_DESCRIPTION
                names=('category','support','source_lines','evidence_order','evidence_plan','claim_parts')
                node['properties']={name:props[name] for name in names};node['required']=list(names)
                stats['blocks']+=1
            if node.get('description')==ANCHOR_BEFORE:
                node['description']=ANCHOR_AFTER;stats['anchors']+=1
            for item in node.values():walk(item)
    walk(value)
    need(stats==dict(blocks=3,anchors=len(CLAIM_ROLES)))
    return value

