"""Bounded, declared evidence coordinates in new wire responses only.

Role completeness and block-local selection are checked. Neither source
semantics nor the truth of a model-assigned role is established here.
"""
from .agent_protocol import ModelError

CLAIM_ROLES = {
    'call': ('expression',),
    'branch': ('condition', 'target'),
    'scope': ('scope_start', 'scope_end', 'target'),
    'implementation': ('implementation', 'target'),
    'value': ('definition',),
    'source': ('source',),
    'gap': (),
    'unverified': (),
}
ROLES = tuple(dict.fromkeys(role for roles in CLAIM_ROLES.values() for role in roles))


def plan_schema():
    return {
        'type': 'object', 'additionalProperties': False,
        'required': ['claim_kind', 'anchors'],
        'properties': {
            'claim_kind': {'type': 'string', 'enum': list(CLAIM_ROLES),
                           'description': 'One central claim type; source is direct text/documentation/inference, gap is unknown, unverified is an unsupported candidate only.'},
            'anchors': {'type': 'array', 'minItems': 0, 'maxItems': 8, 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['role', 'evidence_id', 'line_number'],
                'properties': {
                    'role': {'type': 'string', 'enum': list(ROLES)},
                    'evidence_id': {'type': 'string', 'minLength': 1, 'maxLength': 80},
                    'line_number': {'type': 'integer', 'minimum': 1, 'maximum': 1_000_000},
                }},
                'description': 'Explicit coordinates, not reasoning. Each must also occur in this block source_lines. Include all required roles for claim_kind; no automatic citations.'},
        },
    }


def validate_plan(block, usage):
    """Reject malformed/self-inconsistent plans, without repairing citations.

    Runs after explicit line decoding and before canonical validation. The
    caller removes the wire-only plan only after this check succeeds.
    """
    def reject(reason, detail=None):
        # Only implementation-owned labels leave this function. Never export
        # the rejected plan, field names/values, coordinates or model prose.
        diagnostics = ('action.value_error', reason)
        if detail is not None:
            diagnostics += (detail,)
        raise ModelError('invalid_response', usage=usage, diagnostics=diagnostics)

    plan = block.get('evidence_plan')
    if not isinstance(plan, dict):
        reject('wire.plan_invalid', 'wire.plan_object')
    if set(plan) != {'claim_kind', 'anchors'}:
        reject('wire.plan_invalid', 'wire.plan_fields')
    kind, anchors = plan['claim_kind'], plan['anchors']
    if not isinstance(kind, str) or kind not in CLAIM_ROLES:
        reject('wire.plan_invalid', 'wire.plan_kind')
    if not isinstance(anchors, list) or len(anchors) > 8:
        reject('wire.plan_invalid', 'wire.plan_anchors')
    if kind in ('gap', 'unverified'):
        expected_support = 'gap' if kind == 'gap' else 'candidate'
        if block.get('support') != expected_support:
            reject('wire.plan_invalid', 'wire.plan_support')
        if anchors or block.get('source_lines') or block.get('evidence_ids'):
            reject('wire.plan_invalid', 'wire.plan_gap_refs')
        return
    if block.get('support') == 'gap':
        reject('wire.plan_invalid', 'wire.plan_support')
    selected = {(ref.get('evidence_id'), line) for ref in block['source_lines']
                if isinstance(ref.get('evidence_id'), str) for line in ref['lines']}
    seen, roles = set(), set()
    for anchor in anchors:
        if not isinstance(anchor, dict):
            reject('wire.plan_invalid', 'wire.plan_anchor_object')
        if set(anchor) != {'role', 'evidence_id', 'line_number'}:
            reject('wire.plan_invalid', 'wire.plan_anchor_fields')
        role, eid, line = anchor['role'], anchor['evidence_id'], anchor['line_number']
        if not isinstance(role, str) or role not in CLAIM_ROLES[kind]:
            reject('wire.plan_invalid', 'wire.plan_role')
        if not isinstance(eid, str) or not 1 <= len(eid) <= 80:
            reject('wire.plan_invalid', 'wire.plan_evidence_id')
        if type(line) is not int or not 1 <= line <= 1_000_000:
            reject('wire.plan_invalid', 'wire.plan_line_number')
        key = (role, eid, line)
        if key in seen:
            reject('wire.plan_invalid', 'wire.plan_duplicate')
        seen.add(key)
        roles.add(role)
        if (eid, line) not in selected:
            reject('wire.plan_unselected')
    if roles != set(CLAIM_ROLES[kind]):
        reject('wire.plan_incomplete')
