"""Exact, bounded citation encoding for new follow-up model responses only.

These functions neither choose source lines nor verify semantic entailment.
Historical/runtime content continues to use the original explicit line lists.
"""
from copy import deepcopy
import re

from .agent_protocol import ModelError
from .followup_evidence_plan import plan_schema, validate_plan

LINE_PATTERN = r"^(?:[1-9][0-9]{0,5}|1000000)(?:,(?:[1-9][0-9]{0,5}|1000000)){0,19}$"


def schema_projection(schema):
    result = deepcopy(schema)
    definitions = result.get("$defs", {})
    reference = definitions.get("SourceLineReference")
    if reference is None:
        return result
    reference["properties"].pop("lines")
    reference["properties"]["line_numbers"] = {
        "type": "string", "pattern": LINE_PATTERN, "minLength": 1, "maxLength": 159,
        "description": "One to20 explicitly selected decimal source line numbers, separated only by ASCII commas. No spaces, ranges or padding. Preserve selected order; do not omit required evidence to fit.",
    }
    reference["required"] = ["evidence_id", "line_numbers"]
    block = definitions["FollowUpBlock"]
    # Provider generation order: select support before composing a single claim.
    # This is guidance, not a semantic guarantee or automatic citation generator.
    block["properties"]["evidence_plan"] = plan_schema()
    order = ("category", "support", "evidence_plan", "evidence_ids", "source_lines", "heading", "text")
    block["properties"] = {name: block["properties"][name] for name in order}
    block["required"] = list(order)
    return result


def decode_action(action, usage):
    """Decode only explicit decimal tokens, without salvage or legacy fallback."""
    result = deepcopy(action)
    if result.get("kind") != "final":
        return result
    output = result.get("final_output")
    if not isinstance(output, dict) or output.get("kind") != "follow_up":
        return result

    def reject(reason, label="lines.value_error"):
        # Fixed implementation-owned labels only. Preserve the original primary
        # category, rejection, usage and no-retry behavior; export no input data.
        raise ModelError("invalid_response", usage=usage, diagnostics=(label, reason))

    blocks = output.get("blocks")
    if not isinstance(blocks, list):
        reject("wire.blocks_type")
    for block in blocks:
        if not isinstance(block, dict):
            reject("wire.block_type")
        if not isinstance(block.get("source_lines"), list):
            reject("wire.references_type")
        for reference in block["source_lines"]:
            if not isinstance(reference, dict):
                reject("wire.reference_type")
            if set(reference) != {"evidence_id", "line_numbers"}:
                reject("wire.reference_keys")
            value = reference["line_numbers"]
            if not isinstance(value, str):
                reject("wire.line_list_type")
            if not value:
                reject("wire.line_list_empty", "lines.too_short")
            if len(value) > 159:
                reject("wire.line_list_size", "lines.too_long")
            parts = value.split(",")
            if len(parts) > 20:
                reject("wire.line_list_size", "lines.too_long")
            if any(re.fullmatch(r"[1-9][0-9]{0,6}", part) is None for part in parts):
                reject("wire.line_list_syntax")
            lines = [int(part) for part in parts]
            if any(line > 1_000_000 for line in lines):
                reject("wire.line_list_range")
            # Preserve duplicates for the original uniqueness validator; do not
            # deduplicate, sort, infer a range, fill gaps or repair the answer.
            reference.pop("line_numbers")
            reference["lines"] = lines
        validate_plan(block, usage)
        # No line is selected, filled or repaired by this projection. The plan
        # is a checked wire-only declaration, never stored as private reasoning.
        block.pop("evidence_plan")
    return result
