"""Explicit two-phase follow-up data contract; no I/O, retries or publication.

Plans contain public claim summaries, not private reasoning. Coordinate checks
are supplied by the engine so rejected prose keeps the original safe diagnosis.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json

from .agent_protocol import Usage
from .followup_wire import decode_action
from .learning_contract import FollowUpExplanation
from .phase_diagnostics import PhaseContractError


@dataclass(frozen=True)
class PhaseResponse:
    action: dict
    usage: Usage


@dataclass(frozen=True)
class FrozenPlan:
    payload: bytes
    sha256: str


def _bounded(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')
    if len(raw) > 128000:
        raise PhaseContractError('phase_payload_limit')
    return raw


def _canonical(plan, drafts):
    blocks = []
    for frame, draft in zip(plan['blocks'], drafts):
        block = {key: deepcopy(value) for key, value in frame.items() if key != 'claim_summary'}
        block.update(heading=draft['heading'], text=draft['text'])
        blocks.append(block)
    action = dict(kind='final', final_output=dict(kind='follow_up', topic=plan['topic'],
        section_id=plan['section_id'], title=plan['title'], blocks=blocks, gaps=deepcopy(plan['gaps'])))
    return FollowUpExplanation.model_validate(decode_action(action, None)['final_output'])


def freeze_plan(value, observed_references, check_citations):
    if (not isinstance(value, dict) or set(value) != {'kind','topic','section_id','title','blocks','gaps'}
            or value['kind'] != 'citation_plan'):
        raise PhaseContractError('plan_shape')
    if not isinstance(value['blocks'], list) or not 1 <= len(value['blocks']) <= 12:
        raise PhaseContractError('plan_blocks')
    expected = {'category','support','evidence_plan','evidence_ids','source_lines','claim_summary'}
    for block in value['blocks']:
        if not isinstance(block, dict) or set(block) != expected:
            raise PhaseContractError('plan_block_shape')
        if not isinstance(block['claim_summary'], str) or not 1 <= len(block['claim_summary']) <= 1200:
            raise PhaseContractError('claim_summary')
    plan = deepcopy(value)
    plan['observed_references'] = deepcopy(observed_references)
    # A validation scaffold only. It never becomes a final result or saved body.
    scaffold = _canonical(plan, [dict(heading='规划声明', text=b['claim_summary']) for b in plan['blocks']])
    check_citations(scaffold)
    raw = _bounded(plan)
    return FrozenPlan(raw, hashlib.sha256(raw).hexdigest())


def _load(frozen):
    if not isinstance(frozen, FrozenPlan) or hashlib.sha256(frozen.payload).hexdigest() != frozen.sha256:
        raise PhaseContractError('plan_identity')
    return json.loads(frozen.payload)


def rendering_payload(frozen):
    value = dict(plan_sha256=frozen.sha256, plan=_load(frozen))
    _bounded(value)
    return value


def assemble(frozen, prose):
    plan = _load(frozen)
    if not isinstance(prose, dict) or set(prose) != {'blocks'} or not isinstance(prose['blocks'], list):
        raise PhaseContractError('prose_shape')
    if len(prose['blocks']) != len(plan['blocks']):
        raise PhaseContractError('block_count')
    for index, draft in enumerate(prose['blocks'], 1):
        if (not isinstance(draft, dict) or set(draft) != {'block_index','heading','text'}
                or type(draft['block_index']) is not int or draft['block_index'] != index):
            raise PhaseContractError('prose_block_shape_or_order')
    _bounded(prose)
    # Final source/explicit-line checks belong to _Runner._final, preserving its
    # original source_lines_rejected event. Shape checks do not publish anything.
    return _canonical(plan, prose['blocks'])
