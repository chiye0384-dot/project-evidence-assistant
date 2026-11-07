"""Synthetic protocol response; never a provider or credential source."""
import json
from copy import deepcopy
from project_evidence.deepseek_adapter import MODEL


def line_action(output):
    """Explicit synthetic encoder, never repair raw provider JSON.

    Oversized and duplicate fixtures stay invalid; never truncate or deduplicate.
    """
    output = deepcopy(output)
    for block in output.get("blocks", []):
        # Synthetic default for existing direct-source fixtures only. This does
        # not classify real prose or create/repair a provider evidence plan.
        refs = block.get('source_lines', [])
        kind = 'gap' if block.get('support') == 'gap' else 'unverified' if block.get('support') == 'candidate' and not refs else 'source'
        anchors = [] if not refs or not refs[0]['lines'] else [
            {'role': 'source', 'evidence_id': refs[0]['evidence_id'], 'line_number': refs[0]['lines'][0]}]
        block['evidence_plan'] = {'claim_kind': kind, 'anchors': anchors}
        for reference in block.get("source_lines", []):
            values = reference.pop("lines")
            reference["line_numbers"] = ",".join(str(value) for value in values)
    return {"kind": "final", "final_output": output}


def response_envelope(content=None, **updates):
    content = content if content is not None else json.dumps({"kind": "final", "final_output": {
        "kind": "gap", "message": "Only fixture evidence.", "gaps": ["No verified source read."]}})
    # The declared transport wrapper is literal composition. Invalid JSON stays
    # invalid so rejection fixtures cannot silently normalize or repair it.
    content = '{"action":' + content + '}'
    result = {"object": "response", "status": "completed", "model": MODEL,
              "output": [{"type": "message", "role": "assistant", "status": "completed",
                          "content": [{"type": "output_text", "text": content}]}],
              "usage": {"input_tokens": 10000, "output_tokens": 1000, "total_tokens": 11000}}
    result.update(updates)
    return result
