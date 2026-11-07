"""Exact indexed prose carrier for two_phase only; no output repair or I/O."""
from copy import deepcopy

from .followup_two_phase_schema import phase_schema as _phase_schema
from .followup_two_phase import assemble as _assemble, rendering_payload
from .phase_diagnostics import PhaseContractError
from .prose_shape_diagnostics import prose_shape_error

PROSE_NOTICE = ('prose阶段的blocks使用对象，字段为冻结计划中的block_1至block_N，'
                'N为冻结块数；每个字段都必须存在且仅包含block_index、heading、text。'
                'block_index必须等于字段编号，文字只解释该块冻结结论，不能交换块含义、改引用、'
                '增删字段或使用旧blocks数组。宿主不补块、不猜测缺失内容；缺证据可按原合同返回gap或clarify。')


def phase_schema(context, phase, block_count=None):
    base = _phase_schema(context, phase, block_count)
    if phase != 'prose':
        return base
    result = deepcopy(base)
    block = result['oneOf'][0]['properties']['blocks']['items']
    indexed = {}
    for index in range(1, block_count + 1):
        item = deepcopy(block)
        item['properties']['block_index'] = {'type': 'integer', 'const': index}
        indexed['block_' + str(index)] = item
    result['oneOf'][0]['properties']['blocks'] = dict(
        type='object', additionalProperties=False, required=list(indexed), properties=indexed)
    return result


def assemble(frozen, action):
    plan = rendering_payload(frozen)['plan']
    count = len(plan['blocks'])
    if not isinstance(action, dict) or set(action) != {'blocks'} or not isinstance(action['blocks'], dict):
        raise prose_shape_error(action)
    required = {'block_' + str(index) for index in range(1, count + 1)}
    if set(action['blocks']) != required:
        raise PhaseContractError('block_count')
    drafts = []
    for index in range(1, count + 1):
        value = action['blocks']['block_' + str(index)]
        if (not isinstance(value, dict) or set(value) != {'block_index', 'heading', 'text'}
                or type(value['block_index']) is not int or value['block_index'] != index):
            raise PhaseContractError('prose_block_shape_or_order')
        drafts.append(deepcopy(value))
    # Explicit field identity is independent of JSON member order. Missing or
    # legacy array content is never filled, sorted, salvaged or retried.
    return _assemble(frozen, dict(blocks=drafts))
