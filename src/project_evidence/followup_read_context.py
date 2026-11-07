"""Optional two_phase planning hints over the unchanged observed-source view.

No source reads, citation selection, evidence promotion or budget expansion.
The complete baseline view is returned when optional hints cannot fit.
"""
from copy import deepcopy
import json

from .followup_request import context_view
from .tools import MAX_EXCERPT_LINES, MAX_EXCERPT_CHARS

READ_NOTICE = ('只有本任务truncated=false的完整片段ID才可作为引用候选；这不证明语义。'
               'truncated=true的片段即使展示了部分完整行，也不得用于source_lines或evidence_plan。'
               '读取后半段不会改变前一片段的截断标记。需要时在原预算内选择必要的更小完整范围，'
               '以本次返回的ID和范围为准；不得自动合并、替换旧引用或放宽检查。'
               '相同内容和范围可能复用证据ID；同ID截断状态发生变化会被当前宿主拒绝，'
               '不能假设按原返回范围重读一定可恢复。不能完整读取或预算不足时保留缺口。')

CLAIM_NOTICE = ('citation_plan中每块的claim_summary在计划冻结前也接受显式行号检查，'
                '不是只在prose阶段检查。写本块摘要前，先按实际源码选择支持本块各项结论的'
                'evidence_ids、source_lines和evidence_plan；摘要如明确提及第几行或行号范围，'
                '所提及的每一行都必须已被本块source_lines选中，且属于本任务完整读到的片段。'
                '读过但未选中的行、另一块的行或历史正文的引用不能替本块补齐。'
                '该规则同样适用于prose的heading和text；坐标通过不证明语义。'
                '宿主不会自动补行、改摘要或重试。不得通过删必要事实、统一隐去行号或扩大范围'
                '来逃避核对；缺必要证据时在原预算和权限内补查，否则保留缺口。')


def planning_context_view(context):
    baseline = context_view(context)
    view = deepcopy(baseline)
    for item in view['read_manifest']:
        item['citation_eligible'] = item['truncated'] is False
    view['read_contract'] = dict(max_lines=MAX_EXCERPT_LINES,max_chars=MAX_EXCERPT_CHARS,
                                 notice=READ_NOTICE)
    if len(json.dumps(view,ensure_ascii=False)) > 24_000:
        view = baseline
    with_claim = {**view,'claim_contract':dict(applies_to='citation_plan.blocks[*].claim_summary',notice=CLAIM_NOTICE)}
    if len(json.dumps(with_claim,ensure_ascii=False)) > 24_000:
        return view
    return with_claim
