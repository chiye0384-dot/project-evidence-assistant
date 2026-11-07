"""Bounded model-only request projection. No reservation or dispatch.

The caller supplies the sealed lexer and the host's accepted references. Raw
tool feedback, frozen payload, output schema and default request stay intact.
"""
from copy import deepcopy
import json,re
from project_evidence.agent_protocol import ModelContext,ModelError,FOLLOW_UP_GOAL
from project_evidence.followup_read_context import planning_context_view
from project_evidence.followup_request import context_view
from project_evidence.source_policy import require_allowed_path,normalize_github_repository,SourceError
from project_evidence.preparation_size_diagnostics import attach,context_sizes

FORMAT='java_scope_edge_view_v1'
FIELDS=('evidence_id','path','public_url','commit','blob_oid','sha256','start_line','end_line','truncated')
NOTICE=('仅为本任务已接受完整Java前缀的受限词法位置视图，不是Java符号解析、完整调用图或语义证明。'
    '资源/header/body/catch分开标出，不能从缺少位置推断机制不存在。方法名和参数数量匹配只提供调用候选。'
    '原始片段/来源范围/截断状态不变，视图不是引用资格或新增权限；必须自行选择本块完整支持结论的原行。'
    'prose不得因此补改已冻结source_lines、借其他块证据、删关键事实或修复原答案。'
    'unsupported/skipped仅说明本视图未生成，不能提升为fact或自动重新读取。')

def fail(code='invalid_response',diagnostic=None):
    raise ModelError(code,request_sent=False,diagnostics=() if diagnostic is None else (diagnostic,))
def encode(value):return json.dumps(value,ensure_ascii=False,allow_nan=False)

def encode_model_view(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))

def compact_line_prefix_padding(baseline, observed):
    """Remove only two generated prefix spaces; every raw source suffix stays exact."""
    result=deepcopy(baseline)
    for observation in result['observations']:
        raw=observation['result']
        if observation['name']!='read_excerpt' or raw.get('status')!='ok':continue
        reference,text,_=observed[raw['evidence_id']]
        start=reference['start_line'];lines=text.split('\n')
        expected='\n'.join(f'{start+i} | {line}' for i,line in enumerate(lines))
        if raw['excerpt']!=expected:fail()
        raw['excerpt']='\n'.join(f'{start+i}|{line}' for i,line in enumerate(lines))
    return result

def project_request(request,context,*,phase,payload=None,enabled=False,protocol='two_phase',
                    accepted=None,lexer=None,context_limit=24000,request_limit=128000,
                    size_pass=None,size_previous_floor=0,size_upper_bound=None):
    if type(enabled) is not bool:fail()
    if not enabled:return deepcopy(request)
    if protocol!='two_phase' or context.goal!=FOLLOW_UP_GOAL:fail('not_authorized')
    if phase not in {'plan_or_tools','prose'} or (phase=='plan_or_tools' and payload is not None):fail()
    if type(context_limit) is not int or not 1<=context_limit<=24000:fail()
    if type(request_limit) is not int or not 1<=request_limit<=128000:fail()
    try:
        current=ModelContext.model_validate(context.model_dump())
        baseline=planning_context_view(current) if phase=='plan_or_tools' else context_view(current)
        messages=request['messages']
        if len(messages)!=2 or messages[0]['role']!='system' or messages[1]['role']!='user':fail()
        original=json.loads(messages[1]['content'])
        if original!=dict(phase=phase,context=baseline,payload=payload):fail()
        if (type(accepted) is not dict or lexer.FORMAT!=FORMAT
                or not isinstance(lexer.Unsupported,type) or not issubclass(lexer.Unsupported,ValueError)
                or not callable(lexer.build_view)):fail()
        observed={};views=[];skipped=[]
        for observation in current.observations:
            raw=observation.result
            if observation.name!='read_excerpt' or raw.get('status')!='ok':continue
            eid=raw.get('evidence_id')
            if not isinstance(eid,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',eid):fail()
            reference={k:raw[k] for k in FIELDS}
            if accepted.get(eid)!=reference:fail()
            require_allowed_path(raw['path'])
            if raw['path'].rsplit('/',1)[-1]=='JudgeTaskMapper.java':fail('not_authorized')
            if reference['commit']!=current.commit or normalize_github_repository(reference['public_url'])!=current.public_url:fail()
            if type(reference['truncated']) is not bool:fail()
            if not re.fullmatch(r'[0-9a-f]{40}',reference['blob_oid']) or not re.fullmatch(r'[0-9a-f]{64}',reference['sha256']):fail()
            start,end=reference['start_line'],reference['end_line'];text=raw['excerpt']
            if type(start) is not int or type(end) is not int or not 1<=start<=end<=1000000:fail()
            if not isinstance(text,str) or len(text.split('\n'))!=end-start+1:fail()
            if 'last_line_truncated' in raw and type(raw['last_line_truncated']) is not bool:fail()
            if eid in observed:
                if observed[eid]!=(reference,text,raw.get('last_line_truncated',False)):fail()
                continue
            observed[eid]=(reference,text,raw.get('last_line_truncated',False))
        # Finish the complete provenance check before any lexical work, even
        # for an initially valid read followed by an extraneous old reference.
        if set(accepted)!=set(observed):fail()
        baseline=compact_line_prefix_padding(baseline,observed)
        for eid,(reference,text,last_partial) in observed.items():
            reason=('truncated' if reference['truncated'] or last_partial else
                    'non_java' if not reference['path'].endswith('.java') else
                    'prefix_unavailable' if reference['start_line']!=1 else None)
            if reason:
                skipped.append(dict(evidence_id=eid,status=reason));continue
            try:views.append(lexer.build_view(dict(evidence_id=eid,start_line=reference['start_line'],excerpt=text)))
            except lexer.Unsupported:
                skipped.append(dict(evidence_id=eid,status='unsupported_or_view_limit'))
        extra=dict(format=FORMAT,notice=NOTICE,views=views,skipped=skipped,semantic_verified=False)
        augmented={**deepcopy(baseline),'java_scope_edge_view':extra}
        wrapper=dict(phase=phase,context=augmented,payload=payload)
        # Both host phase context and the actual model-only projection count.
        # Refusal happens before reservation/transport; never omit new units or
        # silently fall back to a shorter baseline when explicitly enabled.
        host=dict(context=current.model_dump(),phase=phase,payload=payload,java_scope_edge_view=extra)
        if max(len(encode_model_view(wrapper)),len(encode(host)))>context_limit:
            values=dict(context_limit=context_limit,previous_floor=size_previous_floor,
                model_wrapper_chars=len(encode_model_view(wrapper)),host_with_view_chars=len(encode(host)),
                **context_sizes(current,phase,payload))
            if size_upper_bound is not None:values['upper_bound']=size_upper_bound
            _size_raise(size_pass+'_projection' if size_pass in ('upper','actual') else None,phase,**values)
        result=deepcopy(request);result['messages'][1]['content']=encode_model_view(wrapper)
        if len(encode(result).encode('utf-8'))>request_limit:
            fail('output_limit','request.byte_limit')
        return result
    except ModelError as error:
        size=getattr(error,'_preparation_base_view_chars',None)
        if type(size) is int and size_pass in ('upper','actual'):
            values=dict(context_limit=context_limit,previous_floor=size_previous_floor,**context_sizes(context,phase,payload))
            if size_upper_bound is not None:values['upper_bound']=size_upper_bound
            attach(error,size_pass+'_base_view',phase,base_view_chars=size,**values)
        raise
    except (KeyError,ValueError,TypeError,AttributeError,SourceError):fail()


def projected_chars(request, context, phase, payload):
    wrapper = json.loads(request['messages'][1]['content'])
    host = dict(context=context.model_dump(), phase=phase, payload=payload,
                java_scope_edge_view=wrapper['context']['java_scope_edge_view'])
    return max(len(encode_model_view(wrapper)), len(encode(host)))




def _size_build(build,context,phase,payload,pass_name,limit,floor,bound=None):
    try:return build(context)
    except ModelError as error:
        size=getattr(error,'_preparation_base_view_chars',None)
        values=dict(context_limit=limit,previous_floor=floor,**context_sizes(context,phase,payload))
        if bound is not None:values['upper_bound']=bound
        if type(size) is int:attach(error,pass_name+'_base_view',phase,base_view_chars=size,**values)
        raise

def _size_raise(stage,phase,**values):
    error=ModelError('output_limit',request_sent=False,diagnostics=('context.projected_limit',))
    raise attach(error,stage,phase,**values)


def prepare_request(context, *, phase, payload, accepted, build_request, lexer,
                    context_limit, previous_context_chars):
    """Use a five-digit remaining counter as a conservative sizing bound.

    Final construction is measured again. Optional legacy hints can change at
    a threshold; if that invalidates the bound, refuse instead of omitting
    material or dispatching a request the host did not account for.
    """
    if type(context_limit) is not int or not 1 <= context_limit <= 24000:fail()
    if type(previous_context_chars) is not int or not 0 <= previous_context_chars <= context_limit:fail()
    data = context.model_dump()
    data['remaining']['context_chars'] = 24000
    try:
        upper = ModelContext.model_validate(data)
    except ValueError:
        _size_raise('upper_model',phase,context_limit=context_limit,previous_floor=previous_context_chars,
            **context_sizes(data,phase,payload))
    upper_request = project_request(_size_build(build_request,upper,phase,payload,'upper',context_limit,previous_context_chars), upper, phase=phase, payload=payload,
        enabled=True, accepted=accepted, lexer=lexer, context_limit=context_limit,
        size_pass='upper',size_previous_floor=previous_context_chars)
    bound = max(previous_context_chars, projected_chars(upper_request, upper, phase, payload))
    data['remaining']['context_chars'] = context_limit - bound
    actual = ModelContext.model_validate(data)
    request = project_request(_size_build(build_request,actual,phase,payload,'actual',context_limit,previous_context_chars,bound), actual, phase=phase, payload=payload,
        enabled=True, accepted=accepted, lexer=lexer, context_limit=context_limit,
        size_pass='actual',size_previous_floor=previous_context_chars,size_upper_bound=bound)
    actual_size=projected_chars(request, actual, phase, payload)
    if actual_size > bound:
        _size_raise('final_bound',phase,context_limit=context_limit,previous_floor=previous_context_chars,
            upper_bound=bound,actual_projected_chars=actual_size,**context_sizes(actual,phase,payload))
    return request, actual, bound
