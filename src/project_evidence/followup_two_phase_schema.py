"""Stage schemas for the explicit two_phase follow-up protocol."""
from copy import deepcopy

from .agent_protocol import FindArguments,SearchArguments,ReadArguments,GapOutput,ClarificationOutput
from .followup_evidence_plan import CLAIM_ROLES
from .learning_contract import FollowUpExplanation
from .followup_wire import schema_projection


def _block_schema(block):
    """Express finite role/support relations without changing local acceptance.

    Every alternative keeps the original wire fields. Role completeness, empty
    unsupported references, uniqueness and observed block-local coordinates
    still require the original validators; strict transport omits array caps.
    """
    def plan_for(kind):
        plan = deepcopy(block['properties']['evidence_plan'])
        plan['properties']['claim_kind'] = {'type': 'string', 'const': kind}
        if CLAIM_ROLES[kind]:
            plan['properties']['anchors']['items']['properties']['role']['enum'] = list(CLAIM_ROLES[kind])
        else:
            plan['properties']['anchors']['maxItems'] = 0
        return plan

    supported = deepcopy(block)
    properties = supported['properties']
    properties['category']['enum'].remove('gap')
    properties['support']['enum'].remove('gap')
    properties['evidence_plan'] = {'oneOf': [plan_for(kind) for kind, roles in CLAIM_ROLES.items() if roles]}
    branches = [supported]
    for kind, support in [('gap', 'gap'), ('unverified', 'candidate')]:
        branch = deepcopy(block)
        properties = branch['properties']
        if kind == 'unverified':
            properties['category']['enum'].remove('gap')
        properties['support'] = {'type': 'string', 'const': support}
        properties['evidence_plan'] = plan_for(kind)
        properties['evidence_ids']['maxItems'] = 0
        properties['source_lines']['maxItems'] = 0
        branches.append(branch)
    return {'oneOf': branches}


def terminal_schema():
    return dict(type='object',additionalProperties=False,required=['kind','final_output'],properties=dict(
        kind=dict(type='string',const='final'),final_output={'oneOf':[GapOutput.model_json_schema(),ClarificationOutput.model_json_schema()]}))

def phase_schema(context,phase,block_count=None):
    if phase=='prose':
        if type(block_count) is not int or not 1<=block_count<=12:raise ValueError('block_count')
        prose=dict(type='object',additionalProperties=False,required=['blocks'],properties=dict(blocks=dict(type='array',
            minItems=block_count,maxItems=block_count,items=dict(type='object',additionalProperties=False,
                required=['block_index','heading','text'],properties=dict(
                    block_index=dict(type='integer',minimum=1,maximum=block_count),
                    heading=dict(type='string',minLength=1,maxLength=80),text=dict(type='string',minLength=1,maxLength=1200))))))
        return {'oneOf':[prose,terminal_schema()]}
    if phase!='plan_or_tools':raise ValueError('phase')
    plan=schema_projection(FollowUpExplanation.model_json_schema())
    definitions=plan.pop('$defs')
    plan['properties']['kind']={'type':'string','const':'citation_plan'}
    plan['properties']['topic']={'type':'string','const':context.anchor.topic}
    plan['properties']['section_id']={'type':'string','const':context.anchor.section_id}
    block=definitions['FollowUpBlock']
    block['properties'].pop('heading');block['properties'].pop('text')
    block['properties']['claim_summary']=dict(type='string',minLength=1,maxLength=1200,
        description='One bounded source claim, not private reasoning. Its coordinates must support it; it remains unverified.')
    block['required']=list(block['properties'])
    definitions['FollowUpBlock'] = _block_schema(block)
    alternatives=[]
    for name,arguments in [('find_files',FindArguments),('search_text',SearchArguments),('read_excerpt',ReadArguments)]:
        alternatives.append(dict(type='object',additionalProperties=False,required=['id','name','arguments'],properties=dict(
            id=dict(type='string',pattern='^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$'),name=dict(type='string',const=name),
            arguments=arguments.model_json_schema())))
    tools=dict(type='object',additionalProperties=False,required=['kind','tool_calls'],properties=dict(
        kind=dict(type='string',const='tools'),tool_calls=dict(type='array',minItems=1,maxItems=3,items={'oneOf':alternatives})))
    return {'oneOf':[tools,plan,terminal_schema()],'$defs':definitions}
