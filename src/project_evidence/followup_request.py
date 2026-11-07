"""Model-only follow-up presentation; no new tool, permission or stored shape."""
from copy import deepcopy
import json

from .agent_protocol import FindArguments, SearchArguments, ReadArguments, ModelError
from .java_scope_index import scope_index


def instruction_contract(contract):
    """Omit only a verified duplicate of the three typed tool schemas.

    The complete turn contract, including every local limit/definition, stays
    in the instruction. Native output schema generation is unchanged. An
    unfamiliar or different contract keeps its original presentation.
    """
    if not isinstance(contract, dict) or set(contract) != {"turn", "tool_arguments"}:
        return contract
    try:
        tool_branch = contract["turn"]["oneOf"][0]
        if tool_branch["properties"]["kind"]["const"] != "tools":
            return contract
        alternatives = tool_branch["properties"]["tool_calls"]["items"]["oneOf"]
        embedded = {}
        for call in alternatives:
            name = call["properties"]["name"]["const"]
            if not isinstance(name, str) or name in embedded:
                return contract
            embedded[name] = call["properties"]["arguments"]
        if set(embedded) != {"find_files", "search_text", "read_excerpt"}:
            return contract
        # Compare JSON types exactly, including False versus numeric zero.
        if json.dumps(embedded, sort_keys=True, ensure_ascii=False) != json.dumps(
                contract["tool_arguments"], sort_keys=True, ensure_ascii=False):
            return contract
    except (KeyError, TypeError, ValueError):
        return contract
    return {"turn": contract["turn"]}


def _share_observation_fields(view):
    """Factor identical source metadata out of the model-only feedback copy.

    Never factor per-read identity, ranges, content, status or truncation. A
    missing or different value keeps that entire field inline for every result.
    """
    results = [item["result"] for item in view["observations"]]
    if len(results) < 2:
        return
    shared = {}
    for field in ("commit", "public_url", "source_state", "data_notice"):
        if field in results[0] and all(field in value and
                json.dumps(value[field], ensure_ascii=False, sort_keys=True) ==
                json.dumps(results[0][field], ensure_ascii=False, sort_keys=True)
                for value in results[1:]):
            shared[field] = results[0][field]
    if not shared:
        return
    defaults = {
        "applies_to": "every_observation_result",
        "notice": "每条result均继承以下完全相同的公共字段；其余字段保留原处。",
        "values": shared,
    }
    # Tiny common fields need not make an already short context longer.
    candidate = {**view, "observations": [
        {**item, "result": {key: value for key, value in item["result"].items() if key not in shared}}
        for item in view["observations"]], "observation_defaults": defaults}
    if len(json.dumps(candidate, ensure_ascii=False)) >= len(json.dumps(view, ensure_ascii=False)):
        return
    view["observations"] = candidate["observations"]
    view["observation_defaults"] = defaults


def action_schema(schema):
    """Express the existing exclusive runtime choices and typed arguments."""
    definitions = deepcopy(schema["$defs"])
    call_id = deepcopy(definitions["ToolCall"]["properties"]["id"])
    calls = []
    for name, arguments in (("find_files", FindArguments), ("search_text", SearchArguments),
                            ("read_excerpt", ReadArguments)):
        calls.append({"type": "object", "additionalProperties": False,
                      "required": ["id", "name", "arguments"],
                      "properties": {"id": deepcopy(call_id), "name": {"const": name, "type": "string"},
                                     "arguments": arguments.model_json_schema()}})
    final_output = deepcopy(schema["properties"]["final_output"]["anyOf"][0])
    result = {"oneOf": [
        {"type": "object", "additionalProperties": False, "required": ["kind", "tool_calls"],
         "properties": {"kind": {"const": "tools", "type": "string"},
                        "tool_calls": {"type": "array", "minItems": 1, "maxItems": 3,
                                       "items": {"oneOf": calls}}}},
        {"type": "object", "additionalProperties": False, "required": ["kind", "final_output"],
         "properties": {"kind": {"const": "final", "type": "string"}, "final_output": final_output}},
    ]}
    found = set()
    def collect(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                if name not in found:
                    found.add(name); collect(definitions[name])
            for value in node.values(): collect(value)
        elif isinstance(node, list):
            for value in node: collect(value)
    collect(result)
    result["$defs"] = {name: value for name, value in definitions.items() if name in found}
    return result


def context_view(context):
    """Keep the active question distinct from historical model prose.

    No inferred method graph, answer citations or semantic claim is generated.
    The original context and historical artifacts remain unchanged.
    """
    original = context.model_dump()
    anchor = original.pop("anchor")
    request = {"question": anchor.pop("question"), "topic": anchor["topic"],
               "section_id": anchor["section_id"], "body_id": anchor["body_id"]}
    history = {"source_of_text": "previous_model_explanation", "is_source_evidence": False,
               "text": anchor.pop("previous_excerpt"),
               "truncated": anchor.pop("previous_excerpt_truncated")}
    original.pop("system_instruction")
    manifest = []
    for observation in original["observations"]:
        value = observation["result"]
        if observation["name"] != "read_excerpt" or value.get("status") != "ok":
            continue
        text, start, end = value.get("excerpt"), value.get("start_line"), value.get("end_line")
        if not isinstance(text, str) or type(start) is not int or type(end) is not int:
            raise ModelError("invalid_response", request_sent=False)
        lines = text.split("\n")
        if len(lines) != end - start + 1:
            raise ModelError("invalid_response", request_sent=False)
        manifest.append({key: value[key] for key in
                         ("evidence_id", "path", "start_line", "end_line", "truncated")})
        value["excerpt"] = "\n".join(f"{start + index} | {line}" for index, line in enumerate(lines))
    view = {"active_request": request, "anchor": anchor, "read_manifest": manifest,
            **original, "historical_explanation": history}
    _share_observation_fields(view)
    # Only index validated raw observations; the numbered projection is a copy.
    try:
        lexical = scope_index(context.model_dump()['observations'])
    except (KeyError, ValueError, TypeError):
        raise ModelError('invalid_response', request_sent=False) from None
    if lexical is not None:
        available = 24_000 - len(json.dumps(view, ensure_ascii=False)) - len(', "java_scope_index": ')
        if len(json.dumps(lexical, ensure_ascii=False)) <= available:
            view['java_scope_index'] = lexical
    if len(json.dumps(view, ensure_ascii=False)) > 24_000:
        error=ModelError("output_limit", request_sent=False, diagnostics=("context.projected_limit",))
        error._preparation_base_view_chars=len(json.dumps(view,ensure_ascii=False))
        raise error
    return view
