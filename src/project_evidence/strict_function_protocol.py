"""Fixed data carrier only; the existing adapter owns HTTP, fees and guards."""
from copy import deepcopy
import json

from .agent_protocol import FOLLOW_UP_GOAL, ModelError
from .followup_request import context_view, instruction_contract
from .response_protocol import strict_schema

ENDPOINT = 'https://api.deepseek.com/beta/chat/completions'
CARRIER = 'submit_action'
OLD_TRANSPORT_RULE = '不加解释、围栏、usage、原生工具调用或其他字段。'
WIRE_INSTRUCTION = (
    '\n运输约定：本轮只返回一次submit_action函数调用，把唯一action外层的完整JSON对象作为函数参数。'
    'submit_action只是运输名称，没有任何执行权限；不得原生调用其他函数。'
    '三个本机只读工具的请求仍放在action.tool_calls中，交给原有受控循环校验后执行。'
    'action内的字段、证据计划、行号列表与后附turn合同保持，不能添加usage、思考或额外字段。'
)

def messages(context, contract, guide):
    # guide is the host-owned checked text frozen by _load_guidance, never an
    # observation or user string. Unknown transport wording fails locally.
    if context.goal != FOLLOW_UP_GOAL or guide.count(OLD_TRANSPORT_RULE) != 1:
        raise ModelError('invalid_response', request_sent=False)
    guide = guide.replace(OLD_TRANSPORT_RULE, '参数中不加解释、围栏、usage或其他字段。', 1)
    system = context.system_instruction + guide + WIRE_INSTRUCTION + json.dumps(
        instruction_contract(contract), ensure_ascii=False)
    return [dict(role='system', content=system),
            dict(role='user', content=json.dumps(context_view(context), ensure_ascii=False))]

def request_body(model, messages, schema, max_output_tokens):
    if model != 'deepseek-flash' or type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 4000:
        raise ModelError('output_limit', request_sent=False)
    return dict(model=model, messages=deepcopy(messages), stream=False, temperature=0,
        thinking=dict(type='disabled'), max_tokens=max_output_tokens,
        tools=[dict(type='function', function=dict(name=CARRIER, strict=True,
            description='Return one existing bounded action as data; this function executes nothing.',
            parameters=strict_schema(schema)))],
        tool_choice=dict(type='function', function=dict(name=CARRIER)))

def receipt_values(envelope):
    receipt = envelope['usage']
    values = [receipt[key] for key in ('prompt_tokens','completion_tokens','total_tokens')]
    if any(type(n) is not int or n < 0 for n in values) or values[0]+values[1] != values[2]:
        raise ValueError('Invalid fixed provider receipt')
    return values[0], values[1]

def action_text(envelope, usage):
    def stop(label='response.native_tool_calls', code='invalid_response'):
        raise ModelError(code, usage=usage, diagnostics=(label,))
    choices = envelope.get('choices')
    if not isinstance(choices,list) or len(choices) != 1 or not isinstance(choices[0],dict):
        stop('response.choices_invalid')
    choice = choices[0]
    if choice.get('finish_reason') != 'tool_calls':
        stop('response.finish_reason_invalid', 'output_limit' if choice.get('finish_reason') == 'length' else 'invalid_response')
    message = choice.get('message')
    if not isinstance(message,dict) or message.get('role') != 'assistant':
        stop('response.choices_invalid')
    if message.get('reasoning_content') not in (None,''):
        stop('response.unexpected_reasoning')
    if message.get('content') not in (None,''):
        stop('response.choices_invalid')
    calls = message.get('tool_calls')
    if not isinstance(calls,list) or len(calls) != 1 or not isinstance(calls[0],dict):
        stop()
    call = calls[0]
    if call.get('type') != 'function' or not isinstance(call.get('id'),str) or not 1 <= len(call['id']) <= 128:
        stop()
    function = call.get('function')
    if not isinstance(function,dict) or set(function) != {'name','arguments'} or function.get('name') != CARRIER:
        stop()
    value = function.get('arguments')
    if not isinstance(value,str) or not value.strip():
        stop('response.empty_content')
    return value
