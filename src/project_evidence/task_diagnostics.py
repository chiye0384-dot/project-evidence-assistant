"""Bounded fixed metadata only; never rejected prose, source text or secrets."""
import math
import re
from .prose_shape_diagnostics import safe_shape_diagnostics
from .citation_evidence_slots import safe_slots
from .preparation_size_diagnostics import safe_size
from .phase_diagnostics import PHASES, PHASE_STEPS, PHASE_REASONS, safe_wire_diagnostics

REASONS = frozenset({"missing_source_lines", "legacy_format", "unread_evidence", "truncated_evidence",
                     "outside_evidence", "unselected_explicit_line", "not_selected", "reversed_range",
                     "range_too_wide", "invalid_line_number"})
LOCATIONS = frozenset({"heading", "text", "source_lines", "source_spans"})


def safe_metadata(value):
    """Validate again at storage; discard unknown keys and invalid metadata."""
    result = {}
    size=safe_size(value.get('preparation_size'))
    if size is not None:result['preparation_size']=size
    phase = value.get('phase')
    if isinstance(phase, str) and phase in PHASES:
        result['phase'] = phase
        check = value.get('phase_check')
        if isinstance(check, dict):
            step, reason = check.get('step'), check.get('reason')
            if isinstance(step, str) and step in PHASE_STEPS and isinstance(reason, str) and reason in PHASE_REASONS:
                result['phase_check'] = dict(step=step, reason=reason)
                labels = safe_wire_diagnostics(check.get('wire_diagnostics')) if reason == 'wire_rejected' else []
                if labels:
                    result['phase_check']['wire_diagnostics'] = labels
                shapes = safe_shape_diagnostics(check.get('shape_diagnostics')) if (phase, step, reason) == ('prose', 'assemble', 'prose_shape') else []
                if shapes:
                    result['phase_check']['shape_diagnostics'] = shapes
    identity = value.get('plan_sha256')
    if phase == 'prose' and isinstance(identity, str) and re.fullmatch(r'[0-9a-f]{64}', identity):
        result['plan_sha256'] = identity
    elapsed = value.get("elapsed_ms")
    if type(elapsed) is int and 0 <= elapsed <= 86_400_000:
        result["elapsed_ms"] = elapsed
    diagnostic = value.get("citation_check")
    if not isinstance(diagnostic, dict):
        return result
    block, location, reason = (diagnostic.get(key) for key in ("block_index", "location", "reason"))
    if (type(block) is not int or not 1 <= block <= 12 or not isinstance(location, str)
            or location not in LOCATIONS or not isinstance(reason, str) or reason not in REASONS):
        return result
    clean = dict(block_index=block, location=location, reason=reason)
    for key in ("selected_line_count", "missing_line_count", "missing_unobserved_count", "reference_count"):
        number = diagnostic.get(key)
        cap = 16 if key == "reference_count" else 320
        if type(number) is int and 0 <= number <= cap:
            clean[key] = number
    for key in ("selected_observed_line_sample", "missing_observed_line_sample"):
        numbers = diagnostic.get(key)
        if (isinstance(numbers, list) and len(numbers) <= 20
                and all(type(n) is int and 1 <= n <= 1_000_000 for n in numbers)
                and len(set(numbers)) == len(numbers)):
            clean[key] = list(numbers)
    result["citation_check"] = clean
    slots = safe_slots(value.get("evidence_slots"))
    if (clean["reason"] == "not_selected" and clean["location"] in {"heading", "text"}
            and slots and "reference_count" in clean and "missing_observed_line_sample" in clean
            and all(row["block_reference_index"] <= clean["reference_count"]
                    and set(row["missing_observed_line_sample"]) <= set(clean["missing_observed_line_sample"])
                    for row in slots)):
        result["evidence_slots"] = slots
    return result


def engine_metadata(event):
    """Copy elapsed snapshots already taken by the engine, without a new clock."""
    value = {}
    counters = event.get("counters", {})
    seconds = counters.get("elapsed_seconds") if isinstance(counters, dict) else None
    if type(seconds) in (int, float) and 0 <= seconds <= 86_400 and math.isfinite(seconds):
        value["elapsed_ms"] = round(seconds * 1000)
    if event.get("type") == "source_lines_rejected":
        details = event.get("details", {})
        value["citation_check"] = details.get("citation_check") if isinstance(details, dict) else None
        value["evidence_slots"] = details.get("evidence_slots") if isinstance(details, dict) else None
    if event.get('type') in {'generation_phase', 'plan_frozen', 'source_lines_rejected', 'phase_rejected'}:
        details = event.get('details', {})
        if isinstance(details, dict):
            value['phase'] = details.get('phase')
            if event.get('type') == 'plan_frozen':
                value['plan_sha256'] = details.get('plan_sha256')
            if event.get('type') == 'phase_rejected':
                value['phase_check'] = details.get('phase_check')
    if event.get('type')=='model_error':
        details=event.get('details',{})
        if isinstance(details,dict):value['preparation_size']=details.get('preparation_size')
    return safe_metadata(value)


def citation_details(error, content, references):
    """Coordinates only from task-observed ranges; do not retain unobserved values."""
    block_index = getattr(error, "block_index", None)
    if type(block_index) is not int or not 1 <= block_index <= len(content.blocks):
        return {}
    block = content.blocks[block_index - 1]
    selected = {n for ref in block.source_lines for n in ref.lines}
    ranges = [references[ref.evidence_id] for ref in block.source_lines
              if ref.evidence_id in references and not references[ref.evidence_id]["truncated"]]
    def observed(number):
        return any(ref["start_line"] <= number <= ref["end_line"] for ref in ranges)
    missing = getattr(error, "missing_lines", ())
    if not isinstance(missing, (list, tuple)) or len(missing) > 320 or any(type(n) is not int or not 1 <= n <= 1_000_000 for n in missing):
        missing = ()
    observed_missing = [n for n in missing if observed(n)]
    value = dict(block_index=block_index, location=getattr(error, "location", None), reason=getattr(error, "reason", None),
                 selected_line_count=len(selected), reference_count=len(block.source_lines), missing_line_count=len(missing),
                 missing_unobserved_count=len(missing)-len(observed_missing),
                 selected_observed_line_sample=sorted(n for n in selected if observed(n))[:20],
                 missing_observed_line_sample=observed_missing[:20])
    return safe_metadata({"citation_check": value}).get("citation_check", {})
