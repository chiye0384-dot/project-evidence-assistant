"""Host-owned finite phase failure labels, never exception or model text."""

PHASES = frozenset({'plan_or_tools', 'prose'})
PHASE_STEPS = frozenset({'action', 'terminal', 'tools', 'identity', 'freeze_plan', 'rendering_payload', 'assemble'})
PHASE_REASONS = frozenset({
    'action_kind', 'terminal_kind', 'plan_identity', 'plan_shape', 'plan_blocks',
    'plan_block_shape', 'claim_summary', 'phase_payload_limit', 'prose_shape',
    'block_count', 'prose_block_shape_or_order', 'schema_rejected',
    'wire_rejected', 'value_rejected', 'implementation_error',
})

# Existing decoder labels only. Do not forward general model/transport/schema
# diagnostics or derive labels from exception messages, model keys or values.
WIRE_DIAGNOSTICS = frozenset({
    'wire.blocks_type', 'wire.block_type', 'wire.references_type',
    'wire.reference_type', 'wire.reference_keys',
    'wire.line_list_type', 'wire.line_list_empty', 'wire.line_list_size',
    'wire.line_list_syntax', 'wire.line_list_range',
    'wire.plan_invalid', 'wire.plan_unselected', 'wire.plan_incomplete',
    'wire.plan_object', 'wire.plan_fields', 'wire.plan_kind',
    'wire.plan_anchors', 'wire.plan_support', 'wire.plan_gap_refs',
    'wire.plan_anchor_object', 'wire.plan_anchor_fields', 'wire.plan_role',
    'wire.plan_evidence_id', 'wire.plan_line_number', 'wire.plan_duplicate',
})


def safe_wire_diagnostics(value):
    """Bound both work and output; mutable ModelError fields are untrusted."""
    if type(value) not in (list, tuple):
        return []
    clean = []
    for label in value[:8]:
        if type(label) is str and label in WIRE_DIAGNOSTICS and label not in clean:
            clean.append(label)
    return clean


class PhaseContractError(ValueError):
    """Only implementation-selected labels can leave a phase contract check."""
    def __init__(self, reason):
        self.reason = reason if isinstance(reason, str) and reason in PHASE_REASONS else 'implementation_error'
        super().__init__('Phase contract rejected')
