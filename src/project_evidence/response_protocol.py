"""Fixed official Responses transport shapes, never tool execution or retries."""
import json
from .agent_protocol import ModelError
from .followup_wire import decode_action

ENDPOINT = "https://api.deepseek.com/responses"
WIRE_INSTRUCTION = (
    "\nResponses传输合同：最外层必须且只能是action一个字段，值为后附turn合同的一个完整动作。"
    "标注为完整响应的样例已经包含action外层，直接采用该形状，不要再次包装。"
    "标注为单块的样例只说明blocks中的一块，不是完整响应。"
    '例如最外层形状为{"action":{"kind":"final","final_output":{"kind":"gap",'
    '"message":"证据不足。","gaps":["缺少必要证据"]}}}。'
    "所有对象字段按Schema必填，数组无内容时明确填[]，允许空值的字段明确填null。"
    "不得在action之外加说明、usage或其他字段。\n"
    '追问引用只用line_numbers字符串，如"4,9,15"只代表这三个逐项选择的行号；每ID每块1至20个。'
    "只用十进制正整数与英文逗号，不加空格、范围或null；不发送lines数组或旧line_slots对象。"
    "每块先生成support与evidence_plan（claim_kind及最多8个role/evidence_id/line_number坐标），"
    "再明确选source_lines，最后写单一结论；计划坐标必须也在本块引用中，程序不会补行。"
    "它们经本机精确解码为原行号列表，仍须通过全部原证据与源码标准。\n"
)


def strict_schema(action_schema):
    """Transport projection only; original runtime validation remains mandatory.

    Action alternatives have disjoint kind/name tags, so anyOf preserves their
    exclusivity. DeepSeek documents string/array length keywords as unsupported
    in its strict schema subset. Those limits and uniqueness remain local.
    Inline this fixed acyclic schema rather than relying on provider ref syntax.
    No accepted source range, tool or runtime budget is expanded here.
    """
    definitions = action_schema.get("$defs", {})
    def convert(node, trail=()):
        if isinstance(node, list):
            return [convert(item, trail) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            reference = node["$ref"]
            name = reference.removeprefix("#/$defs/")
            if reference != "#/$defs/" + name or name not in definitions or name in trail:
                raise ModelError("invalid_response", request_sent=False)
            return convert({**definitions[name], **{k:v for k,v in node.items() if k != "$ref"}}, (*trail, name))
        result = {}
        for key, value in node.items():
            if key in {"title", "default", "discriminator", "uniqueItems", "$defs",
                       "minLength", "maxLength", "minItems", "maxItems"}:
                continue
            if key == "properties":
                result[key] = {name: convert(item, trail) for name, item in value.items()}
            elif key == "const":
                result["enum"] = [value]
            else:
                result["anyOf" if key == "oneOf" else key] = convert(value, trail)
        if result.get("type") == "object":
            result["required"] = list(result["properties"])
            result["additionalProperties"] = False
        return result
    converted = convert(action_schema)
    return {"type": "object", "properties": {"action": converted}, "required": ["action"],
            "additionalProperties": False}


def unwrap_action(value, usage):
    """Decode the exact declared wire wrapper, never salvage another shape."""
    if not isinstance(value, dict) or set(value) != {"action"} or not isinstance(value["action"], dict):
        raise ModelError("invalid_response", usage=usage, diagnostics=("action.value_error",))
    return decode_action(value["action"], usage)


def parse_action(text, usage):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ModelError("invalid_response", usage=usage, diagnostics=("action.value_error",))
            result[key] = value
        return result
    return unwrap_action(json.loads(text, object_pairs_hook=unique_object), usage)


def request_body(model, messages, schema, max_output_tokens):
    return {"model": model, "input": messages, "stream": False, "temperature": 0,
            "reasoning": {"effort": "none"}, "max_output_tokens": max_output_tokens,
            "text": {"format": {"type": "json_schema", "name": "followup_action", "strict": True,
                                "schema": strict_schema(schema)}}}


def receipt_values(envelope):
    receipt = envelope["usage"]
    values = [receipt[key] for key in ("input_tokens", "output_tokens", "total_tokens")]
    if any(type(value) is not int or value < 0 for value in values) or values[0] + values[1] != values[2]:
        raise ValueError("Invalid fixed provider receipt")
    return values[0], values[1]


def action_text(envelope, usage):
    def stop(code="invalid_response", diagnostic="response.choices_invalid"):
        raise ModelError(code, usage=usage, diagnostics=(diagnostic,))
    if (envelope["status"] != "completed" or envelope.get("error") is not None
            or envelope.get("incomplete_details") is not None):
        details = envelope.get("incomplete_details")
        limited = isinstance(details, dict) and details.get("reason") == "max_output_tokens"
        stop("output_limit" if limited else "invalid_response", "response.finish_reason_invalid")
    items = envelope.get("output")
    if not isinstance(items, list): stop()
    if any(isinstance(item, dict) and item.get("type") == "reasoning" for item in items):
        stop(diagnostic="response.unexpected_reasoning")
    if any(isinstance(item, dict) and item.get("type") in {"function_call", "custom_tool_call"} for item in items):
        stop(diagnostic="response.native_tool_calls")
    if len(items) != 1 or not isinstance(items[0], dict): stop()
    item = items[0]
    if (item.get("type") != "message" or item.get("role") != "assistant"
            or item.get("status", "completed") != "completed"):
        stop()
    content = item.get("content")
    if (not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict)
            or content[0].get("type") != "output_text"):
        stop()
    value = content[0].get("text")
    if not isinstance(value, str) or not value.strip(): stop(diagnostic="response.empty_content")
    return value
