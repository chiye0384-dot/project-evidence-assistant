"""Fixed official endpoint, non-thinking JSON actions and persistent CNY gates."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re
from dataclasses import dataclass, field
from time import monotonic

import httpx
from pydantic import ValidationError

from .agent_protocol import (ModelContext, ModelTurn, ModelError, Usage, FindArguments,
                             SearchArguments, ReadArguments, schema_diagnostics, _safe_text,
                             GOAL, OUTLINE_GOAL, SECTION_GOAL, FOLLOW_UP_GOAL)
from .spending import SpendingLedger, SpendingError
from .followup_request import action_schema, context_view, instruction_contract
from .followup_wire import schema_projection
from . import response_protocol
from . import strict_function_protocol
from .followup_two_phase import PhaseResponse
from .followup_prose_wire import phase_schema, PROSE_NOTICE
from .followup_read_context import planning_context_view
from .followup_claim_wording import CLAIM_WORDING_NOTICE
from .followup_parts_instruction import planning_instruction as parts_planning_instruction
from .followup_prose_kind import FORMAT as PROSE_FORMAT, phase_schema as prose_kind_schema, prose_instruction as kind_prose_instruction
from .followup_prose_kind import FLAT_FORMAT, flat_phase_schema, flat_prose_instruction
from .followup_claim_parts import FORMAT as PARTS_FORMAT, NOTICE as PARTS_NOTICE, phase_schema as parts_phase_schema
from .deepseek_network import checked_proxy_port, client_options, public_route
from . import java_scope_edges
from .followup_scope_request import FORMAT as SCOPE_FORMAT, prepare_request
from .followup_scope_columns import prepare_request as prepare_columns_request
from .followup_scope_columns_codec import FORMAT as COLUMNS_FORMAT
from .followup_scope_columns_binding import verify_inputs
from .followup_index_columns import prepare_request as prepare_index_request
from .followup_index_columns_binding import verify_inputs as verify_index_inputs
from .followup_index_columns_contract import MODE as INDEX_FORMAT
from . import followup_selected_table as selected_table
from . import followup_table_contract as table_contract

ENDPOINT = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"
MAX_RESPONSE_BYTES = 262_144
_TIMEOUT_LABELS = (
    (httpx.ConnectTimeout, "transport.connect_timeout"),
    (httpx.ReadTimeout, "transport.read_timeout"),
    (httpx.WriteTimeout, "transport.write_timeout"),
    (httpx.PoolTimeout, "transport.pool_timeout"),
)
GUIDANCE_PATH = Path(__file__).resolve().parents[2] / "prompts" / "directory.txt"
LEARNING_GUIDANCE_PATHS = {
    OUTLINE_GOAL: GUIDANCE_PATH.parent / "query-outline.txt",
    SECTION_GOAL: GUIDANCE_PATH.parent / "query-section.txt",
}
SUBMISSION_GUIDANCE_PATHS = {
    OUTLINE_GOAL: GUIDANCE_PATH.parent / "submission-outline.txt",
    SECTION_GOAL: GUIDANCE_PATH.parent / "submission-section.txt",
}
CONSUMPTION_GUIDANCE_PATHS = {
    OUTLINE_GOAL: GUIDANCE_PATH.parent / "consumption-outline.txt",
    SECTION_GOAL: GUIDANCE_PATH.parent / "consumption-section.txt",
}
JUDGING_GUIDANCE_PATHS = {
    OUTLINE_GOAL: GUIDANCE_PATH.parent / "judging-outline.txt",
    SECTION_GOAL: GUIDANCE_PATH.parent / "judging-section.txt",
}


def _json_diagnostic(error: json.JSONDecodeError) -> str:
    """Classify syntax without exporting document, position or exception text.

    No repair or partial parsing: these labels only explain a rejected action.
    Unterminated strings are not proof of provider truncation (finish_reason
    is checked separately).
    """
    start = error.doc.lstrip(" \t\r\n")
    if start.startswith("\ufeff"):
        return "json.bom"
    if start.startswith("```"):
        return "json.fence"
    if error.msg.startswith("Invalid \\u") or error.msg.startswith("Invalid \\escape"):
        return "json.invalid_escape"
    if error.msg.startswith("Invalid control character"):
        return "json.control_character"
    if error.msg.startswith("Unterminated string"):
        return "json.unterminated_string"
    return {
        "Extra data": "json.extra_data",
        "Expecting property name enclosed in double quotes": "json.property_name",
        "Expecting ':' delimiter": "json.colon",
        "Expecting ',' delimiter": "json.delimiter",
        "Expecting value": "json.value",
    }.get(error.msg, "json.other")


def _json_property_details(error: json.JSONDecodeError) -> tuple[str, ...]:
    """One fixed lexical detail; never return text, keys, values or positions.

    Classify only a real property-name parser failure. EOF does not establish
    provider truncation. Old failures without their document remain unknown.
    """
    if _json_diagnostic(error) != "json.property_name":
        return ()
    text, pos = error.doc, error.pos
    if not isinstance(text, str) or type(pos) is not int or not 0 <= pos <= len(text):
        return ()
    if pos == len(text):
        return ("json.key_eof",)
    token = text[pos]
    if token == "'":
        return ("json.key_single_quote",)
    if token == "}":
        previous = pos - 1
        while previous >= 0 and text[previous] in " \t\r\n":
            previous -= 1
        if previous >= 0 and text[previous] == ",":
            return ("json.key_trailing_comma",)
    if token in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-":
        return ("json.key_bare_token",)
    return ("json.key_invalid_token",)


def _load_guidance(goal=GOAL, topic="query", *, two_phase=False):
    paths = {"query": LEARNING_GUIDANCE_PATHS, "submission": SUBMISSION_GUIDANCE_PATHS,
             "consumption": CONSUMPTION_GUIDANCE_PATHS, "judging": JUDGING_GUIDANCE_PATHS}.get(topic)
    if paths is None:
        raise ValueError("所选主题尚未实现；未发送模型请求。")
    path = (GUIDANCE_PATH.parent / ("follow-up-two-phase.txt" if two_phase else "follow-up.txt") if goal == FOLLOW_UP_GOAL
            else GUIDANCE_PATH if goal == GOAL else paths[goal])
    try:
        if path.is_symlink() or path.resolve().parent != path.parent:
            raise ValueError
        with path.open("rb") as stream:
            raw = stream.read(12_001)
        if not 1 <= len(raw) <= 12_000:
            raise ValueError
        text = _safe_text(raw.decode("utf-8"))
        return text, hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeError, ValueError):
        raise ValueError("本项目指导文本缺失或未通过边界检查；未发送模型请求。") from None


@dataclass(frozen=True, repr=False)
class _PreparedPhase:
    request_json: str
    context_json: str
    context_chars: int
    max_output_tokens: int
    deadline: float
    identity: tuple = field(repr=False)

    @property
    def context(self):
        return ModelContext.model_validate_json(self.context_json)


class DeepSeekAdapter:
    configured = is_configured = True
    kind = adapter_kind = mode = execution_mode = "real"

    def __init__(self, api_key: str, ledger: SpendingLedger, grant_id: str, *, transport=None,
                 task_id: str | None = None, enable_query: bool = False, enable_submission: bool = False,
                 enable_consumption: bool = False, enable_judging: bool = False, enable_followup: bool = False,
                 goal=GOAL, topic="query", followup_protocol="responses", proxy_port=0, followup_claim_format="literal", followup_prose_format="indexed", followup_scope_view="off"):
        self._proxy_port = checked_proxy_port(proxy_port)
        if not isinstance(api_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,200}", api_key):
            raise ValueError("在本机配置有效API密钥；不要在聊天、URL或日志中填写。")
        self._key, self.ledger, self.grant_id = api_key, ledger, grant_id
        self._transport, self._task_id = transport, task_id
        self.allowed_topics = frozenset(({"query"} if enable_query else set()) | ({"submission"} if enable_submission else set())
                                       | ({"consumption"} if enable_consumption else set())
                                       | ({"judging"} if enable_judging else set()))
        self.allowed_goals = frozenset({GOAL, OUTLINE_GOAL, SECTION_GOAL} if self.allowed_topics else {GOAL})
        self.enable_followup = bool(enable_followup)
        if followup_protocol not in ("responses", "strict_function", "two_phase") or (followup_protocol != "responses" and not (self.enable_followup and self.allowed_topics)):
            raise ValueError("固定追问传输模式需要先启用追问用途；未发送模型请求。")
        if (type(followup_claim_format) is not str or followup_claim_format not in ('literal', PARTS_FORMAT, table_contract.FORMAT)
                or (followup_claim_format in (PARTS_FORMAT, table_contract.FORMAT) and followup_protocol != 'two_phase')):
            raise ValueError('Invalid planning format; explicit two_phase follow-up is required.')
        if (type(followup_prose_format) is not str or followup_prose_format not in ('indexed', PROSE_FORMAT, FLAT_FORMAT)
                or (followup_prose_format in (PROSE_FORMAT, FLAT_FORMAT) and followup_protocol != 'two_phase')):
            raise ValueError('Invalid prose format; explicit two_phase follow-up is required.')
        self.followup_prose_format = followup_prose_format
        if (type(followup_scope_view) is not str or followup_scope_view not in ('off', SCOPE_FORMAT, COLUMNS_FORMAT, INDEX_FORMAT)
                or (followup_scope_view in (SCOPE_FORMAT, COLUMNS_FORMAT, INDEX_FORMAT) and followup_protocol != 'two_phase')):
            raise ValueError('Invalid scope view; explicit two_phase follow-up is required.')
        self.followup_scope_view = followup_scope_view
        self._prepared_phase = None
        self.followup_claim_format = followup_claim_format
        self.followup_protocol = followup_protocol
        if self.enable_followup and self.allowed_topics:
            self.allowed_goals = self.allowed_goals | {FOLLOW_UP_GOAL}
        if goal not in self.allowed_goals or (goal != GOAL and topic not in self.allowed_topics):
            raise ValueError("所选主题用途尚未启用；未发送模型请求。")
        self._goal, self._topic = goal, topic
        # Each task freezes its own checked prompt; later tasks may read an update.
        self._guidance, self.guidance_sha256 = (_load_guidance(goal, topic, two_phase=True)
            if goal == FOLLOW_UP_GOAL and followup_protocol == "two_phase" else _load_guidance(goal, topic))

    def for_task(self, task_id, *, goal=GOAL, topic="query"):
        return DeepSeekAdapter(self._key, self.ledger, self.grant_id,
                               transport=self._transport, task_id=task_id,
                               enable_query="query" in self.allowed_topics,
                               enable_submission="submission" in self.allowed_topics,
                               enable_consumption="consumption" in self.allowed_topics,
                               enable_judging="judging" in self.allowed_topics, enable_followup=self.enable_followup,
                               goal=goal, topic=topic, followup_protocol=self.followup_protocol, proxy_port=self._proxy_port,
                               followup_claim_format=self.followup_claim_format, followup_prose_format=self.followup_prose_format,
                               followup_scope_view=self.followup_scope_view)

    @property
    def network_route(self):
        return public_route(self._proxy_port)

    def public_status(self):
        return self.ledger.summary(self.grant_id)

    def _contract(self, context):
        schema = ModelTurn.model_json_schema()
        schema["properties"].pop("usage", None)
        schema.get("$defs", {}).pop("Usage", None)
        allowed = {"gap", "clarify", {GOAL: "directory", OUTLINE_GOAL: "chapter_outline", SECTION_GOAL: "section_explanation", FOLLOW_UP_GOAL: "follow_up"}[context.goal]}
        output_schema = schema["properties"]["final_output"]["anyOf"][0]
        mapping = output_schema["discriminator"]["mapping"]
        output_schema["discriminator"]["mapping"] = {key: value for key, value in mapping.items() if key in allowed}
        refs = set(output_schema["discriminator"]["mapping"].values())
        output_schema["oneOf"] = [value for value in output_schema["oneOf"] if value["$ref"] in refs]
        # Include only definitions reachable by the selected goal contract.
        definitions = schema.get("$defs", {})
        def reachable(node, found):
            if isinstance(node, dict):
                if "$ref" in node:
                    name = node["$ref"].rsplit("/", 1)[-1]
                    if name in definitions and name not in found:
                        found.add(name); reachable(definitions[name], found)
                for value in node.values(): reachable(value, found)
            elif isinstance(node, list):
                for value in node: reachable(value, found)
        found = set(); reachable(schema["properties"], found)
        schema["$defs"] = {key: value for key, value in definitions.items() if key in found}
        if context.anchor is not None:
            for name in ("ChapterOutline", "SectionExplanation", "FollowUpExplanation"):
                if name in schema["$defs"]:
                    schema["$defs"][name]["properties"]["topic"] = {"const": context.anchor.topic, "type": "string"}
        if context.goal == FOLLOW_UP_GOAL and "FollowUpBlock" in schema["$defs"]:
            # Live generation must return explicit anchors. Historical parsing
            # remains compatible with pre-anchor stored follow-ups.
            block_schema = schema["$defs"]["FollowUpBlock"]
            block_schema["properties"].pop("source_spans", None)
            schema["$defs"].pop("SourceSpan", None)
            block_schema["required"] = list(dict.fromkeys(block_schema.get("required", []) + ["source_lines"]))
            schema = schema_projection(action_schema(schema))
        contract = {"turn": schema, "tool_arguments": {
            "find_files": FindArguments.model_json_schema(),
            "search_text": SearchArguments.model_json_schema(),
            "read_excerpt": ReadArguments.model_json_schema()}}
        return contract

    def _messages(self, context):
        contract = self._contract(context)
        if context.goal == FOLLOW_UP_GOAL and self.followup_protocol == "strict_function":
            return strict_function_protocol.messages(context, contract, self._guidance)
        wire_instruction = response_protocol.WIRE_INSTRUCTION if context.goal == FOLLOW_UP_GOAL else ""
        if context.goal == FOLLOW_UP_GOAL:
            contract = instruction_contract(contract)
        instruction = context.system_instruction + self._guidance + wire_instruction + json.dumps(contract, ensure_ascii=False)
        view = context.model_dump()
        if context.goal == FOLLOW_UP_GOAL:
            view = context_view(context)
        return [{"role": "system", "content": instruction},
                {"role": "user", "content": json.dumps(view, ensure_ascii=False)}]

    def _checked_context(self, context, timeout_seconds, max_output_tokens):
        # Revalidate even mutated model instances before anything is transmitted.
        try:
            context = ModelContext.model_validate(context)
        except ValidationError:
            raise ModelError("invalid_response", request_sent=False) from None
        if context.goal != self._goal or context.goal not in self.allowed_goals:
            raise ModelError("not_authorized", request_sent=False)
        if context.anchor is not None and (context.anchor.topic != self._topic or self._topic not in self.allowed_topics):
            raise ModelError("not_authorized", request_sent=False)
        if self._task_id is None or timeout_seconds <= 0 or max_output_tokens <= 0:
            raise ModelError("adapter_error", request_sent=False)
        return context

    def generate(self, context, *, timeout_seconds, max_output_tokens):
        context = self._checked_context(context, timeout_seconds, max_output_tokens)
        if context.goal == FOLLOW_UP_GOAL and self.followup_protocol == "two_phase":
            raise ModelError("not_authorized", request_sent=False)
        function_carrier = context.goal == FOLLOW_UP_GOAL and self.followup_protocol == "strict_function"
        responses = context.goal == FOLLOW_UP_GOAL and not function_carrier
        messages = self._messages(context)
        if function_carrier:
            request = strict_function_protocol.request_body(MODEL, messages, self._contract(context)["turn"], max_output_tokens)
        elif responses:
            request = response_protocol.request_body(MODEL, messages, self._contract(context)["turn"], max_output_tokens)
        else:
            request = {"model": MODEL, "messages": messages, "stream": False,
                       "thinking": {"type": "disabled"}, "temperature": 0,
                       "max_tokens": max_output_tokens, "response_format": {"type": "json_object"}}
        return self._dispatch(request, timeout_seconds, max_output_tokens,
                              function_carrier=function_carrier, responses=responses)

    def generate_phase(self, context, *, phase, payload=None, timeout_seconds, max_output_tokens):
        if self.followup_scope_view != 'off':
            # Enabled requests need the engine's accepted references and counter.
            raise ModelError('not_authorized', request_sent=False)
        context = self._checked_context(context, timeout_seconds, max_output_tokens)
        request = self._phase_request(context, phase, payload, max_output_tokens)
        return self._dispatch(request, timeout_seconds, max_output_tokens,
                              function_carrier=True, responses=False, phase_response=True)

    def _scope_identity(self):
        identity = (self._task_id, self._goal, self._topic, self.followup_protocol,
                self.followup_scope_view, self.followup_claim_format,
                self.followup_prose_format, self.guidance_sha256)
        if self.followup_scope_view == INDEX_FORMAT: return identity + (verify_index_inputs(),)
        return identity + (verify_inputs(),) if self.followup_scope_view == COLUMNS_FORMAT else identity

    def prepare_phase(self, context, *, phase, payload=None, accepted, context_limit,
                      previous_context_chars, timeout_seconds, max_output_tokens):
        """Pure preparation; the returned single-use snapshot owns the request."""
        deadline = monotonic()+timeout_seconds
        self._prepared_phase = None
        if self.followup_scope_view not in (SCOPE_FORMAT, COLUMNS_FORMAT, INDEX_FORMAT):
            raise ModelError('not_authorized', request_sent=False)
        context = self._checked_context(context, timeout_seconds, max_output_tokens)
        prepare = prepare_index_request if self.followup_scope_view == INDEX_FORMAT else prepare_columns_request if self.followup_scope_view == COLUMNS_FORMAT else prepare_request
        if self.followup_scope_view == INDEX_FORMAT: verify_index_inputs()
        if self.followup_scope_view == COLUMNS_FORMAT: verify_inputs()
        request, context, chars = prepare(context, phase=phase, payload=payload,
            accepted=accepted, lexer=java_scope_edges, context_limit=context_limit,
            previous_context_chars=previous_context_chars,
            build_request=lambda checked: self._phase_request(checked, phase, payload, max_output_tokens))
        prepared = _PreparedPhase(json.dumps(request, ensure_ascii=False, allow_nan=False),
            context.model_dump_json(), chars, max_output_tokens, deadline,
            self._scope_identity())
        self._prepared_phase = prepared
        return prepared

    def generate_prepared_phase(self, prepared, *, timeout_seconds):
        # Switching an owned columns snapshot off is still a failed consumption.
        # Keep foreign/replaced snapshot behavior and all original formats intact.
        if (isinstance(prepared, _PreparedPhase) and prepared is self._prepared_phase
                and prepared.identity[4:5] in ((COLUMNS_FORMAT,), (INDEX_FORMAT,))
                and (self.followup_scope_view,) != prepared.identity[4:5]):
            self._prepared_phase = None
            raise ModelError('not_authorized', request_sent=False)
        if (not isinstance(prepared, _PreparedPhase) or prepared is not self._prepared_phase
                or self.followup_scope_view not in (SCOPE_FORMAT, COLUMNS_FORMAT, INDEX_FORMAT)):
            raise ModelError('not_authorized', request_sent=False)
        self._prepared_phase = None  # Consume on every outcome, including transport failure.
        if prepared.identity != self._scope_identity():
            raise ModelError('not_authorized', request_sent=False)
        timeout_seconds = min(timeout_seconds, prepared.deadline-monotonic())
        if timeout_seconds <= 0:
            raise ModelError('timeout', request_sent=False)
        self._checked_context(prepared.context, timeout_seconds, prepared.max_output_tokens)
        # Use the exact measured snapshot, never rebuild after the checkpoint.
        return self._dispatch(json.loads(prepared.request_json), timeout_seconds, prepared.max_output_tokens,
                              function_carrier=True, responses=False, phase_response=True)

    def _phase_request(self, context, phase, payload, max_output_tokens):
        if self.followup_protocol != "two_phase" or context.goal != FOLLOW_UP_GOAL or not self.enable_followup:
            raise ModelError("not_authorized", request_sent=False)
        if phase not in {"plan_or_tools", "prose"} or (phase == "plan_or_tools" and payload is not None):
            raise ModelError("invalid_response", request_sent=False)
        try:
            count = len(payload['plan']['blocks']) if phase == 'prose' else None
            schema = (parts_phase_schema(context, phase, count) if self.followup_claim_format in (PARTS_FORMAT, table_contract.FORMAT)
                      else phase_schema(context, phase, count))
            if self.followup_prose_format in (PROSE_FORMAT, FLAT_FORMAT):
                prose_schema = flat_phase_schema if self.followup_prose_format == FLAT_FORMAT else prose_kind_schema
                schema = prose_schema(context, phase, count, planning_format=PARTS_FORMAT if self.followup_claim_format == table_contract.FORMAT else self.followup_claim_format)
            view = planning_context_view(context) if phase == 'plan_or_tools' else context_view(context)
            instruction = context.system_instruction + self._guidance + '\n' + (PROSE_NOTICE if phase == 'prose' else CLAIM_WORDING_NOTICE)
            if phase == 'plan_or_tools' and self.followup_claim_format in (PARTS_FORMAT, table_contract.FORMAT):
                instruction = parts_planning_instruction(instruction) + '\n' + PARTS_NOTICE
            if phase == 'prose' and self.followup_prose_format in (PROSE_FORMAT, FLAT_FORMAT):
                prose_instruction = flat_prose_instruction if self.followup_prose_format == FLAT_FORMAT else kind_prose_instruction
                instruction = prose_instruction(instruction)
            if phase == 'plan_or_tools' and self.followup_claim_format == table_contract.FORMAT:
                instruction = table_contract.rewrite_system(instruction, prose_format=self.followup_prose_format)
                schema = table_contract.schema(context)
            messages = [dict(role='system', content=instruction),
                dict(role='user', content=json.dumps(dict(phase=phase, context=view, payload=payload),
                                                    ensure_ascii=False, allow_nan=False))]
            request = strict_function_protocol.request_body(MODEL, messages, schema, max_output_tokens)
        except ModelError:
            raise
        except (TypeError, ValueError, KeyError):
            raise ModelError('invalid_response', request_sent=False) from None
        return request

    def _dispatch(self, request, timeout_seconds, max_output_tokens, *, function_carrier, responses, phase_response=False):
        """Exactly one HTTP dispatch, sharing the original receipt/cost guards."""
        if len(json.dumps(request, ensure_ascii=False).encode("utf-8")) > 128_000:
            raise ModelError("output_limit", request_sent=False, diagnostics=("request.byte_limit",))
        try:
            reservation = self.ledger.reserve(self.grant_id, self._task_id, max_output_tokens)
        except SpendingError as error:
            raise ModelError(error.code, request_sent=False) from None
        usage = None
        deadline = monotonic() + timeout_seconds
        phase = "transport"
        transport_stage = "transport.awaiting_headers"
        try:
            # Only an explicit loopback proxy; no environment inheritance,
            # redirects, vendor SDK retries, Shell or arbitrary destination.
            with httpx.Client(transport=self._transport, trust_env=False, follow_redirects=False,
                              timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0)),
                              **client_options(self._proxy_port)) as client:
                endpoint = strict_function_protocol.ENDPOINT if function_carrier else response_protocol.ENDPOINT if responses else ENDPOINT
                with client.stream("POST", endpoint, json=request,
                                   headers={"Authorization": "Bearer " + self._key}) as response:
                    if response.status_code != 200:
                        # Keep only a bounded protocol status. Never read/persist an
                        # error body, headers, credential echo or redirect target.
                        status = response.status_code
                        label = f"response.http_{status}" if type(status) is int and 100 <= status <= 599 else "response.http_other"
                        raise ModelError("cost_uncertain", diagnostics=("response.non_200", label))
                    transport_stage = "transport.reading_body"
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if monotonic() >= deadline or len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise ModelError("cost_uncertain", diagnostics=("response.size_or_deadline",))
                        body.extend(chunk)
                    transport_stage = "transport.response_complete"
            phase = "envelope"
            envelope = json.loads(body)
            if envelope.get("model") != MODEL:
                raise ModelError("cost_uncertain", diagnostics=("response.unexpected_model",))
            if responses and (envelope.get("object") != "response"
                              or envelope.get("status") not in {"completed", "incomplete", "failed"}):
                raise ModelError("cost_uncertain", diagnostics=("envelope.invalid",))
            if function_carrier and envelope.get("object") != "chat.completion":
                raise ModelError("cost_uncertain", diagnostics=("envelope.invalid",))
            phase = "receipt"
            # Provider envelope only. Model text cannot invent receipt statistics.
            if function_carrier:
                input_count, output_count = strict_function_protocol.receipt_values(envelope)
            elif responses:
                input_count, output_count = response_protocol.receipt_values(envelope)
            else:
                receipt = envelope["usage"]
                input_count, output_count = receipt["prompt_tokens"], receipt["completion_tokens"]
            usage = Usage(input_tokens=input_count, output_tokens=output_count, mode="real")
            self.ledger.settle(reservation, usage.input_tokens, usage.output_tokens)
            phase = "action"
            if monotonic() >= deadline:
                raise ModelError("timeout", usage=usage)
            if function_carrier:
                content = strict_function_protocol.action_text(envelope, usage)
            elif responses:
                content = response_protocol.action_text(envelope, usage)
            else:
                choices = envelope["choices"]
                if not isinstance(choices, list) or len(choices) != 1:
                    raise ModelError("invalid_response", usage=usage, diagnostics=("response.choices_invalid",))
                choice = choices[0]
                if choice.get("finish_reason") != "stop":
                    raise ModelError("output_limit" if choice.get("finish_reason") == "length" else "invalid_response",
                                     usage=usage, diagnostics=("response.finish_reason_invalid",))
                message = choice["message"]
                if message.get("reasoning_content"):
                    raise ModelError("invalid_response", usage=usage, diagnostics=("response.unexpected_reasoning",))
                if message.get("tool_calls"):
                    raise ModelError("invalid_response", usage=usage, diagnostics=("response.native_tool_calls",))
                content = message["content"]
            if not isinstance(content, str) or not content.strip():
                raise ModelError("invalid_response", usage=usage, diagnostics=("response.empty_content",))
            if phase_response and self.followup_claim_format == table_contract.FORMAT:
                owned = json.loads(request['messages'][1]['content'])
                if owned['phase'] == 'plan_or_tools':
                    try:
                        action = selected_table.parse(content)
                        if action.get('kind') == 'citation_plan':
                            anchor = owned['context']['active_request']
                            action = selected_table.decode(action, topic=anchor['topic'], section_id=anchor['section_id'])
                    except selected_table.Rejected as error:
                        safe = selected_table.safe_failure(error)
                        raise ModelError('invalid_response', usage=usage, diagnostics=tuple(safe['diagnostics'])) from None
                else:
                    action = response_protocol.parse_action(content, usage)
            else:
                action = response_protocol.parse_action(content, usage) if responses or function_carrier else json.loads(content)
            if not isinstance(action, dict):
                raise ModelError("invalid_response", usage=usage, diagnostics=("action.not_object",))
            if "usage" in action:
                raise ModelError("invalid_response", usage=usage, diagnostics=("action.model_usage",))
            if phase_response:
                return PhaseResponse(action, usage)
            action["usage"] = usage.model_dump()
            try:
                return ModelTurn.model_validate(action)
            except ValidationError as error:
                raise ModelError("invalid_response", usage=usage, diagnostics=schema_diagnostics(error)) from None
        except ModelError as error:
            if usage is None:
                self.ledger.uncertain(reservation)
            raise error from None
        except SpendingError:
            self.ledger.uncertain(reservation)
            raise ModelError("cost_uncertain", usage=usage, diagnostics=("receipt.invalid",)) from None
        except httpx.TimeoutException as error:
            if usage is None:
                self.ledger.uncertain(reservation)
            # Only local class identity and progress markers; never inspect the
            # exception message, request, response, headers, URL or __cause__.
            subtype = next((label for kind, label in _TIMEOUT_LABELS if isinstance(error, kind)),
                           "transport.timeout_other")
            diagnostics = ("transport.timeout", subtype, transport_stage)
            if transport_stage != "transport.awaiting_headers":
                diagnostics += ("response.http_200",)
            raise ModelError("cost_uncertain" if usage is None else "invalid_response",
                             usage=usage, diagnostics=diagnostics) from None
        except httpx.HTTPError:
            if usage is None:
                self.ledger.uncertain(reservation)
            raise ModelError("cost_uncertain" if usage is None else "invalid_response",
                             usage=usage, diagnostics=("transport.http_error",)) from None
        except json.JSONDecodeError as error:
            if usage is None:
                self.ledger.uncertain(reservation)
            raise ModelError("cost_uncertain" if usage is None else "invalid_response", usage=usage,
                             diagnostics=(("envelope.invalid",) if usage is None else
                                          ("action.json_invalid", _json_diagnostic(error),
                                           *_json_property_details(error)))) from None
        except Exception:
            if usage is None:
                self.ledger.uncertain(reservation)
                label = "receipt.invalid" if phase == "receipt" else "envelope.invalid"
                raise ModelError("cost_uncertain", diagnostics=(label,)) from None
            raise ModelError("invalid_response", usage=usage) from None
