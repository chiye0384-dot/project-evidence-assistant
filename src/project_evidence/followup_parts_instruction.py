"""Host-owned planning wording only; no schema, data, output or budget edits."""
from .agent_protocol import ModelError

GUIDE_BEFORE='再写一条有界claim_summary。claim_summary是待核实的公开源码结论摘要，不是思维过程。'
GUIDE_AFTER='再按当前schema写有界claim_parts；这些片段按序连接成一条待核实的公开源码结论摘要，不是思维过程。'
ADVICE_BEFORE='claim_summary用具体方法、条件、数据和结果写出一个可核对的结论，'
ADVICE_AFTER='claim_parts按序连接后的摘要用具体方法、条件、数据和结果写出一个可核对的结论，'
SCOPE_NOTICE='context.claim_contract描述展开后的内部摘要；本阶段提交字段只有claim_parts，内部claim_summary不是提交字段。'

def planning_instruction(instruction):
    if (not isinstance(instruction,str) or instruction.count(GUIDE_BEFORE)!=1
            or instruction.count(ADVICE_BEFORE)!=1):
        raise ModelError('invalid_response',request_sent=False)
    return instruction.replace(GUIDE_BEFORE,GUIDE_AFTER,1).replace(ADVICE_BEFORE,ADVICE_AFTER,1)+'\n'+SCOPE_NOTICE
