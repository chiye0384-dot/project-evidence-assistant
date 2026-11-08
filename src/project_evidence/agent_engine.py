"""One bounded synchronous tool loop, with no provider or network implementation.

Adapters and tool executors must enforce their own in-flight I/O deadlines. The
engine checks elapsed time before and after each operation; it cannot interrupt
an arbitrary non-cooperating synchronous callable. Scripted usage is never a
claim of real model quality, token billing or semantic citation correctness.
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import math
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .preparation_size_diagnostics import error_size
from .agent_protocol import (
    AgentBudget, ClarificationOutput, DirectoryOutput, GapOutput, ModelAdapter,
    ModelContext, ModelError, ModelTurn, RemainingBudget, ToolCall, ToolObservation,
    Usage,
    GOAL, OUTLINE_GOAL, SECTION_GOAL, FOLLOW_UP_GOAL, ChapterOutline, SectionExplanation, FollowUpExplanation,
)
from .source_policy import SourceError, normalize_github_repository, require_allowed_path
from .learning_contract import validate_followup_spans, CitationBoundsError
from .followup_line_mentions import validate_explicit_line_mentions
from .task_diagnostics import citation_details
from .citation_evidence_slots import evidence_slots
from .followup_two_phase import PhaseResponse, freeze_plan, rendering_payload
from .followup_prose_kind import FORMAT as PROSE_FORMAT, assemble as assemble_prose_kind
from .followup_prose_kind import FLAT_FORMAT, assemble_flat
from .followup_claim_parts import FORMAT as PARTS_FORMAT, freeze_plan as freeze_parts_plan
from .followup_prose_wire import assemble
from .phase_diagnostics import PhaseContractError, PHASE_REASONS, safe_wire_diagnostics
from .prose_shape_diagnostics import safe_shape_diagnostics


RunStatus = Literal[
    "completed", "gap", "clarify", "not_configured", "cancelled", "source_changed",
    "unverified", "denied", "no_progress", "budget_exhausted", "timeout", "failed",
]
FinalOutput = DirectoryOutput | GapOutput | ClarificationOutput | ChapterOutline | SectionExplanation | FollowUpExplanation
ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]
EventRecorder = Callable[[dict[str, Any]], object]

_MESSAGES = {
    "completed": "目录响应与本任务引用标识通过结构核对；语义正确性尚未验证。",
    "gap": "本次保留证据缺口，没有将缺口写成已确认事实。",
    "clarify": "需要用户补充范围或入口信息后继续。",
    "not_configured": "模型未配置；没有进行模型调用或生成目录。",
    "cancelled": "本次任务已取消。",
    "source_changed": "来源、版本或核对会话已变化；本次停止。",
    "unverified": "没有有效的公开来源核对；本次停止。",
    "denied": "工具或来源拒绝了访问；不会改参数绕过。",
    "no_progress": "重复请求没有产生新结果；本次停止。",
    "budget_exhausted": "本次调用、上下文或输出预算已用尽。",
    "timeout": "本次达到时间限制；没有将超时结果当成完成。",
    "failed": "响应、工具反馈或执行不符合合同；未回显异常原文。",
}
_TOOL_STATUSES = frozenset({
    "ok", "no_results", "not_found", "denied", "file_too_large", "invalid_encoding",
    "binary", "invalid_argument", "invalid_query", "budget_exhausted", "source_changed",
    "source_error", "timeout", "unverified", "temporary_error",
})
_RESULT_KEYS = frozenset({
    "status", "message", "commit", "public_url", "truncated", "data_notice", "items",
    "scanned_files", "scanned_bytes", "skipped_files", "source_state", "path", "blob_oid",
    "sha256", "total_lines", "start_line", "end_line", "excerpt", "text",
    "last_line_truncated", "evidence_id",
})
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


class RunCounters(BaseModel):
    # Attempts include a persisted pending dispatch that cancellation can stop
    # before the adapter/executor is entered. They are not billing receipts.
    model_calls: int = 0
    tool_calls: int = 0
    retries: int = 0
    context_chars: int = 0
    output_chars: int = 0
    input_tokens: int = 0
    input_tokens_estimated: bool = False
    reported_input_tokens: int = 0
    reported_output_tokens: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    output_tokens: int = 0
    output_tokens_estimated: bool = False
    elapsed_seconds: float = 0.0


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    path: str
    public_url: str
    commit: str
    blob_oid: str
    sha256: str
    start_line: int
    end_line: int
    truncated: bool = False


class RunResult(BaseModel):
    status: RunStatus
    message: str
    final_output: FinalOutput | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list)
    counters: RunCounters = Field(default_factory=RunCounters)
    adapter_kind: Literal["unconfigured", "scripted", "real"] = "unconfigured"
    mode: Literal["none", "scripted", "real"] = "none"
    semantic_verified: Literal[False] = False
    citations_validated: bool = False
    events: list[dict[str, Any]] = Field(default_factory=list)


class _Stop(Exception):
    def __init__(self, status: RunStatus) -> None:
        self.status = status


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class _Runner:
    def __init__(self, adapter: ModelAdapter, tools: ToolExecutor, guard: Callable[[], None],
                 cancelled: Callable[[], bool], limits: AgentBudget, recorder: EventRecorder | None) -> None:
        self.adapter, self.tools, self.guard, self.cancelled = adapter, tools, guard, cancelled
        self.limits, self.recorder = limits, recorder
        self.started = time.monotonic()
        self.counters = RunCounters()
        self.events: list[dict[str, Any]] = []
        self.observations: list[ToolObservation] = []
        self.history: list[dict[str, Any]] = []
        self.evidence: dict[str, EvidenceReference] = {}
        self.seen_results: dict[str, str] = {}
        self.recorder_failed = False
        self.final_output: FinalOutput | None = None
        self.citations_validated = False
        kind = getattr(adapter, "adapter_kind", "unconfigured")
        mode = getattr(adapter, "mode", "none")
        self.adapter_contract_valid = kind in {"unconfigured", "scripted", "real"} and mode in {"none", "scripted", "real"}
        self.kind = kind if kind in {"unconfigured", "scripted", "real"} else "unconfigured"
        self.mode = mode if mode in {"none", "scripted", "real"} else "none"
        self.no_progress_turns = 0
        self.goal = GOAL
        self.section_id = None
        self.topic = "query"

    def _snapshot(self) -> RunCounters:
        self.counters.elapsed_seconds = round(max(0.0, time.monotonic() - self.started), 6)
        return self.counters.model_copy(deep=True)

    def _check_cancel_time(self) -> None:
        try:
            is_cancelled = self.cancelled()
        except Exception:
            raise _Stop("failed") from None
        if is_cancelled:
            raise _Stop("cancelled")
        if time.monotonic() - self.started >= self.limits.max_seconds:
            raise _Stop("timeout")

    def _checkpoint(self) -> None:
        self._check_cancel_time()
        try:
            self.guard()
        except SourceError as error:
            self._check_cancel_time()
            if error.code in {"source_changed", "version_changed", "head_changed"}:
                raise _Stop("source_changed") from None
            if error.code in {"denied", "protected_path", "path_denied"}:
                raise _Stop("denied") from None
            raise _Stop("unverified") from None
        except Exception:
            self._check_cancel_time()
            raise _Stop("failed") from None
        self._check_cancel_time()

    def _event(self, event_type: str, summary: str, **details: Any) -> None:
        event = {
            "type": event_type, "sequence": len(self.events) + 1, "summary": summary,
            "details": details, "counters": self._snapshot().model_dump(),
        }
        self.events.append(event)
        if self.recorder is not None and not self.recorder_failed:
            try:
                if self.recorder(json.loads(_json(event))) is False:
                    raise RuntimeError("record rejected")
            except SourceError as error:
                self.recorder_failed = True
                self._check_cancel_time()
                raise _Stop("source_changed" if error.code == "source_changed" else "failed") from None
            except Exception:
                self.recorder_failed = True
                self._check_cancel_time()
                raise _Stop("failed") from None

    def _remaining(self) -> RemainingBudget:
        return RemainingBudget(
            turns=max(0, self.limits.max_turns - self.counters.model_calls),
            tool_calls=max(0, self.limits.max_tool_calls - self.counters.tool_calls),
            seconds=max(0.0, self.limits.max_seconds - (time.monotonic() - self.started)),
            context_chars=max(0, self.limits.max_context_chars - self.counters.context_chars),
            output_chars=max(0, self.limits.max_output_chars - self.counters.output_chars),
            output_tokens=max(0, self.limits.max_output_tokens - self.counters.output_tokens),
            retries=max(0, self.limits.max_retries - self.counters.retries),
        )

    def _context(self, initial: ModelContext) -> ModelContext:
        try:
            context = ModelContext(
                commit=initial.commit, public_url=initial.public_url, goal=initial.goal,
                observations=self.observations, history=self.history, remaining=self._remaining(),
                anchor=initial.anchor,
            )
        except ValidationError:
            # Observations and history have already passed their individual
            # contracts; exceeding the aggregate cap is a budget failure.
            raise _Stop("budget_exhausted") from None
        length = len(_json(context.model_dump()))
        self.counters.context_chars = max(self.counters.context_chars, length)
        if length > self.limits.max_context_chars:
            raise _Stop("budget_exhausted")
        return context

    def _retry(self, error: ModelError, *, operation: str) -> bool:
        self._checkpoint()
        if error.code == "not_configured":
            raise _Stop("not_configured")
        if error.code == "cancelled":
            raise _Stop("cancelled")
        if error.code in {"output_limit", "spending_limit"}:
            raise _Stop("budget_exhausted")
        if error.retryable and self.counters.retries < self.limits.max_retries:
            self.counters.retries += 1
            self._event("retry", "临时错误有限重试；调用额度不重置。", operation=operation, code=error.code)
            return True
        raise _Stop("timeout" if error.code == "timeout" else "failed")

    def _account_usage(self, usage: Usage, context: ModelContext, output_chars: int,
                       output_bytes: int) -> None:
        """Keep receipts even when cancellation or a source race ends the task."""
        self.counters.output_chars += output_chars
        if self.mode == "real" and usage.mode == "real":
            self.counters.reported_input_tokens += usage.input_tokens
            self.counters.reported_output_tokens += usage.output_tokens
        else:
            input_estimate = usage.input_tokens if usage.mode == "scripted" and self.mode == "scripted" else 0
            output_estimate = usage.output_tokens if usage.mode == "scripted" and self.mode == "scripted" else 0
            input_estimate = input_estimate or max(1, math.ceil(len(_json(context.model_dump()).encode("utf-8")) / 4))
            output_estimate = output_estimate or (max(1, math.ceil(output_bytes / 4)) if output_bytes else 0)
            self.counters.estimated_input_tokens += input_estimate
            self.counters.estimated_output_tokens += output_estimate
            self.counters.input_tokens_estimated = True
            self.counters.output_tokens_estimated = True
        self.counters.input_tokens = self.counters.reported_input_tokens + self.counters.estimated_input_tokens
        self.counters.output_tokens = self.counters.reported_output_tokens + self.counters.estimated_output_tokens

    def _account_bad_response(self, raw: Any, context: ModelContext) -> None:
        try:
            data = raw.usage if isinstance(raw, (ModelTurn, PhaseResponse)) else raw.get("usage", {}) if isinstance(raw, dict) else {}
            usage = Usage.model_validate(data)
        except (ValidationError, TypeError, ValueError, AttributeError):
            usage = Usage()
        self._account_usage(usage, context, 0, 0)

    def _model_request(self, initial: ModelContext, *, phase=None, payload=None):
        """One counted adapter request; phases share this clock and receipt path."""
        while True:
            self._checkpoint()
            if self.counters.model_calls >= self.limits.max_turns:
                raise _Stop("budget_exhausted")
            context = self._context(initial)
            if phase is not None:
                length = len(_json(dict(context=context.model_dump(), phase=phase, payload=payload)))
                self.counters.context_chars = max(self.counters.context_chars, length)
                if length > self.limits.max_context_chars:
                    raise _Stop('budget_exhausted')
            remaining = self._remaining()
            if remaining.output_chars <= 0 or remaining.output_tokens <= 0:
                raise _Stop("budget_exhausted")
            prepared = None
            if phase is not None and getattr(self.adapter, 'followup_scope_view', 'off') in ('java_scope_edge_view_v1', 'java_scope_edge_unit_columns_v1', 'java_scope_edge_unit_index_columns_v1'):
                try:
                    prepared = self.adapter.prepare_phase(context, phase=phase, payload=payload,
                        accepted={eid: ref.model_dump() for eid, ref in self.evidence.items()},
                        context_limit=self.limits.max_context_chars,
                        previous_context_chars=self.counters.context_chars,
                        timeout_seconds=self._remaining().seconds, max_output_tokens=remaining.output_tokens)
                    context = prepared.context
                    self.counters.context_chars = prepared.context_chars
                except ModelError as error:
                    error.retryable = False
                    labels = [label for label in ('context.projected_limit', 'request.byte_limit')
                              if label in error.diagnostics]
                    size=error_size(error)
                    self._event('model_error', '模型请求准备停止：' + error.code +
                                ('；准备类别：' + ', '.join(labels) if labels else ''),
                                **({'preparation_size':size} if size is not None else {}))
                    self._checkpoint()  # Cancellation/source change retains priority.
                    self._retry(error, operation='model')
            self.counters.model_calls += 1
            self._event("model_requested", "记录调用尝试，再请求适配器选择下一步。", adapter_kind=self.kind)
            self._checkpoint()  # Callback/cancellation can change the session.
            try:
                if prepared is not None:
                    raw = self.adapter.generate_prepared_phase(prepared, timeout_seconds=self._remaining().seconds)
                elif phase is None:
                    raw = self.adapter.generate(context, timeout_seconds=self._remaining().seconds,
                                                max_output_tokens=remaining.output_tokens)
                else:
                    raw = self.adapter.generate_phase(context, phase=phase, payload=payload,
                        timeout_seconds=self._remaining().seconds, max_output_tokens=remaining.output_tokens)
            except ModelError as error:
                if error.request_sent is not False:
                    self._account_usage(error.usage or Usage(), context, 0, 0)
                # A completed dispatch can return its failure at the deadline.
                # Preserve only fixed diagnostics before a timeout/cancel/source
                # checkpoint takes precedence. This never authorizes a retry or
                # publishes model output, and the recorder still uses task CAS.
                self._event("model_error", "模型响应停止：" + error.code +
                            ("；合同类别：" + ", ".join(error.diagnostics) if error.diagnostics else ""))
                self._checkpoint()
                if phase is not None:
                    error.retryable = False  # No automatic re-dispatch of either phase.
                if self._retry(error, operation="model"):
                    continue
            except TimeoutError:
                self._account_usage(Usage(), context, 0, 0)
                if self._retry(ModelError("timeout", retryable=phase is None), operation="model"):
                    continue
            except Exception:
                self._account_usage(Usage(), context, 0, 0)
                self._check_cancel_time()
                raise _Stop("failed") from None
            try:
                if phase is None:
                    turn = ModelTurn.model_validate(raw)
                    serialized = _json(turn.model_dump())
                else:
                    if not isinstance(raw, PhaseResponse) or not isinstance(raw.action, dict):
                        raise ValueError('phase_response')
                    turn = raw
                    serialized = _json(dict(action=raw.action, usage=raw.usage.model_dump()))
            except (ValidationError, TypeError, ValueError):
                self._account_bad_response(raw, context)
                self._checkpoint()  # Cancellation wins over invalid output.
                raise _Stop("failed") from None
            self._account_usage(turn.usage, context, len(serialized), len(serialized.encode("utf-8")))
            self._checkpoint()
            if turn.usage.mode not in {"none", self.mode}:
                raise _Stop("failed")
            if self.counters.output_chars > self.limits.max_output_chars or self.counters.output_tokens > self.limits.max_output_tokens:
                raise _Stop("budget_exhausted")
            self._event("model_returned", "收到结构化行动；未保存模型原文或内部思考。",
                        **({'kind': turn.kind} if phase is None else {'phase': phase}))
            return turn

    def _model_turn(self, initial: ModelContext) -> ModelTurn:
        if self.goal != FOLLOW_UP_GOAL or getattr(self.adapter, 'followup_protocol', None) != 'two_phase':
            return self._model_request(initial)
        self._event('generation_phase', '先查证并固定本块引用；尚未发布正文。', phase='plan_or_tools')
        raw = self._model_request(initial, phase='plan_or_tools').action
        terminal = self._phase_checked('plan_or_tools', 'terminal', lambda: self._phase_terminal(raw))
        if terminal is not None:
            return terminal
        if raw.get('kind') == 'tools':
            return self._phase_checked('plan_or_tools', 'tools', lambda: ModelTurn.model_validate(raw))
        if raw.get('kind') != 'citation_plan':
            self._phase_rejected('plan_or_tools', 'action', 'action_kind')
        if raw.get('topic') != self.topic or raw.get('section_id') != self.section_id:
            self._phase_rejected('plan_or_tools', 'identity', 'plan_identity')
        references = {key: value.model_dump() for key, value in self.evidence.items()}
        freeze = freeze_parts_plan if getattr(self.adapter, 'followup_claim_format', 'literal') in (PARTS_FORMAT, 'selected_table_v1') else freeze_plan
        frozen = self._phase_checked('plan_or_tools', 'freeze_plan', lambda: freeze(
            raw, references, lambda content: self._validate_followup(content, phase='plan_or_tools')))
        self._event('plan_frozen', '本任务引用计划已固定；未保存计划原文或发布正文。',
                    phase='prose', plan_sha256=frozen.sha256)
        self._checkpoint()
        payload = self._phase_checked('prose', 'rendering_payload', lambda: rendering_payload(frozen))
        prose = self._model_request(initial, phase='prose', payload=payload).action
        terminal = self._phase_checked('prose', 'terminal', lambda: self._phase_terminal(prose))
        if terminal is not None:
            return terminal
        self._checkpoint()
        prose_format = getattr(self.adapter, 'followup_prose_format', 'indexed')
        render = assemble_flat if prose_format == FLAT_FORMAT else assemble_prose_kind if prose_format == PROSE_FORMAT else assemble
        content = self._phase_checked('prose', 'assemble', lambda: render(frozen, prose))
        return ModelTurn(kind='final', final_output=content)

    def _phase_rejected(self, phase, step, reason, *, wire_diagnostics=(), shape_diagnostics=()):
        # Preserve cancellation/source/deadline precedence, and never inspect
        # an exception's message, rejected value, traceback or raw action.
        self._checkpoint()
        if not isinstance(reason, str) or reason not in PHASE_REASONS:
            reason = 'implementation_error'
        check = dict(step=step, reason=reason)
        labels = safe_wire_diagnostics(wire_diagnostics) if reason == 'wire_rejected' else []
        if labels:
            check['wire_diagnostics'] = labels
        shapes = safe_shape_diagnostics(shape_diagnostics) if (phase, step, reason) == ('prose', 'assemble', 'prose_shape') else []
        if shapes:
            check['shape_diagnostics'] = shapes
        self._event('phase_rejected', '两阶段本地处理停止；未发布回答。', phase=phase,
                    phase_check=check)
        self._checkpoint()
        raise _Stop('failed')

    def _phase_checked(self, phase, step, check):
        try:
            return check()
        except _Stop:
            raise  # Original source-line diagnosis / cancel / source / time.
        except PhaseContractError as error:
            self._phase_rejected(phase, step, error.reason, shape_diagnostics=getattr(error, "shape_diagnostics", ()))
        except ModelError as error:
            self._phase_rejected(phase, step, 'wire_rejected', wire_diagnostics=error.diagnostics)
        except ValidationError:
            self._phase_rejected(phase, step, 'schema_rejected')
        except (ValueError, TypeError, KeyError):
            self._phase_rejected(phase, step, 'value_rejected')
        except Exception:
            self._phase_rejected(phase, step, 'implementation_error')

    @staticmethod
    def _phase_terminal(raw):
        if raw.get('kind') != 'final':
            return None
        turn = ModelTurn.model_validate(raw)
        if not isinstance(turn.final_output, (GapOutput, ClarificationOutput)):
            raise PhaseContractError('terminal_kind')
        return turn

    def _tool_result(self, call: ToolCall, initial: ModelContext) -> dict[str, Any]:
        while True:
            self._checkpoint()
            if self.counters.tool_calls >= self.limits.max_tool_calls:
                raise _Stop("budget_exhausted")
            signature = _json({"name": call.name, "arguments": call.arguments})
            self.counters.tool_calls += 1
            self._event("tool_requested", "记录调用尝试，再调用固定只读工具。", tool=call.name,
                        arguments=dict(call.arguments), arguments_sha256=hashlib.sha256(signature.encode()).hexdigest())
            self._checkpoint()
            try:
                raw = self.tools(call.name, dict(call.arguments))
            except ModelError as error:
                if self._retry(error, operation="tool"):
                    continue
            except SourceError as error:
                self._checkpoint()
                if error.code == "source_changed":
                    raise _Stop("source_changed") from None
                if error.code == "unverified":
                    raise _Stop("unverified") from None
                if error.code in {"denied", "invalid_path", "protected_path", "secret_detected"}:
                    raise _Stop("denied") from None
                raise _Stop("timeout" if error.code == "timeout" else "failed") from None
            except Exception:
                self._check_cancel_time()
                raise _Stop("failed") from None
            evidence_error: _Stop | None = None
            if call.name == "read_excerpt" and isinstance(raw, dict) and raw.get("status") == "ok" and "evidence_id" in raw:
                try:
                    self._remember_evidence(raw, initial)
                except _Stop as error:
                    evidence_error = error
            self._checkpoint()
            if evidence_error is not None:
                raise evidence_error
            if not isinstance(raw, dict) or raw.get("status") not in _TOOL_STATUSES:
                raise _Stop("failed")
            status = raw["status"]
            self._event("tool_returned", "工具已返回；错误原文不进入行动记录。", tool=call.name, status=status)
            terminal = {
                "denied": "denied", "source_changed": "source_changed", "unverified": "unverified",
                "budget_exhausted": "budget_exhausted", "timeout": "timeout", "source_error": "failed",
                "invalid_argument": "failed", "invalid_query": "failed",
            }.get(status)
            if terminal:
                raise _Stop(terminal)
            if status == "temporary_error":
                if self._retry(ModelError("temporary_error", retryable=True), operation="tool"):
                    continue
            try:
                if set(raw) - _RESULT_KEYS:
                    raise ValueError("unknown tool fields")
                result = json.loads(_json(raw))
                if result.get("commit", initial.commit) != initial.commit:
                    raise _Stop("source_changed")
                if "public_url" in result and normalize_github_repository(result["public_url"]) != initial.public_url:
                    raise _Stop("source_changed")
                if "path" in result:
                    require_allowed_path(result["path"])
                for item in result.get("items", []):
                    require_allowed_path(item["path"])
                    if call.name in {"find_files", "search_text"}:
                        allowed = {"path", "size", "blob_oid"} if call.name == "find_files" else {
                            "path", "line", "text", "excerpt", "sha256", "blob_oid",
                            "start_column", "end_column", "line_truncated",
                        }
                        if set(item) - allowed:
                            raise ValueError("unknown navigation fields")
                        positions = {"start_column", "end_column", "line_truncated"}
                        if call.name == "search_text" and positions.intersection(item):
                            if not positions.issubset(item):
                                raise ValueError("incomplete search positions")
                            text = item.get("excerpt", item.get("text"))
                            if (type(item.get("line")) is not int or item["line"] < 1
                                    or type(item["start_column"]) is not int
                                    or type(item["end_column"]) is not int
                                    or item["start_column"] < 1
                                    or item["end_column"] < item["start_column"]
                                    or type(item["line_truncated"]) is not bool
                                    or not isinstance(text, str)
                                    or item["end_column"] - item["start_column"] + 1 != len(text)):
                                raise ValueError("invalid search positions")
                # Navigation is not a citation. Keep source metadata in the
                # read tool, rather than repeating hashes for every filename.
                if call.name in {"find_files", "search_text"}:
                    items = result.get("items", [])
                    count = len(items)
                    cap = 20 if call.name == "find_files" else 10
                    fields = ("path",) if call.name == "find_files" else (
                        "path", "line", "text", "start_column", "end_column", "line_truncated",
                    )
                    result["items"] = [{key: item[key] for key in fields if key in item} for item in items[:cap]]
                    result["truncated"] = (bool(result.get("truncated")) or count > cap
                                           or any(item.get("line_truncated") is True for item in items))
                    result["projection_notice"] = "导航结果有限展示且省略对象摘要；引用须读取片段取得证据ID，不代表全工程覆盖。"
                # M0 has compatibility aliases for text. Send one copy only.
                if "excerpt" in result:
                    result.pop("text", None)
                for item in result.get("items", []):
                    if "excerpt" in item:
                        item.pop("text", None)
                observation = ToolObservation(call_id=call.id, name=call.name, result=result)
            except _Stop:
                raise
            except SourceError:
                raise _Stop("denied") from None
            except (ValidationError, TypeError, ValueError, KeyError):
                raise _Stop("failed") from None
            semantic = {
                key: value for key, value in result.items()
                if key not in {"evidence_id", "message", "data_notice", "source_state",
                               "scanned_files", "scanned_bytes", "skipped_files"}
            }
            fingerprint = hashlib.sha256(_json(semantic).encode()).hexdigest()
            if self.seen_results.get(signature) == fingerprint:
                raise _Stop("no_progress")
            self.seen_results[signature] = fingerprint
            self.observations.append(observation)
            self._event("tool_feedback_accepted", "接受有限工具反馈；未把代码原文写入行动日志。",
                        tool=call.name, status=status, evidence_id=result.get("evidence_id"),
                        truncated=result.get("truncated", False), items_count=len(result.get("items", [])))
            return result

    def _remember_evidence(self, result: dict[str, Any], initial: ModelContext) -> None:
        try:
            identifier, path = result["evidence_id"], result["path"]
            if result["commit"] != initial.commit or normalize_github_repository(result["public_url"]) != initial.public_url:
                raise _Stop("source_changed")
            if not isinstance(identifier, str) or _EVIDENCE_ID.fullmatch(identifier) is None:
                raise ValueError("invalid identifier")
            require_allowed_path(path)
            if _SHA1.fullmatch(result["blob_oid"]) is None or _SHA256.fullmatch(result["sha256"]) is None:
                raise ValueError("invalid hash")
            start, end = result["start_line"], result["end_line"]
            if type(start) is not int or type(end) is not int or not 1 <= start <= end:
                raise ValueError("invalid line range")
            reference = EvidenceReference(
                evidence_id=identifier, path=path, public_url=initial.public_url, commit=initial.commit,
                blob_oid=result["blob_oid"], sha256=result["sha256"], start_line=start, end_line=end,
                truncated=result.get("truncated", False),
            )
        except _Stop:
            raise
        except (SourceError, ValidationError, TypeError, ValueError, KeyError):
            raise _Stop("failed") from None
        previous = self.evidence.get(identifier)
        if previous is not None and previous != reference:
            raise _Stop("failed")
        self.evidence[identifier] = reference

    def _validate_followup(self, output, *, phase=None):
        references = {key: value.model_dump() for key, value in self.evidence.items()}
        try:
            validate_followup_spans(output, references, require_supported=True)
            validate_explicit_line_mentions(output)
        except CitationBoundsError as error:
            self._event("source_lines_rejected", "本任务行引用检查停止：" + error.code + "；未发布回答。",
                        citation_check=citation_details(error, output, references),
                        **({"evidence_slots": evidence_slots(error, output, references)}
                           if error.code == "unselected_explicit_line" and error.reason == "not_selected" else {}),
                        **({"phase": phase} if phase else {}))
            raise _Stop("failed") from None
        except (ValueError, KeyError, TypeError):
            self._event("source_lines_rejected", "本任务引用格式不符合合同；未发布回答。")
            raise _Stop("failed") from None
        self._event("plan_lines_validated" if phase == "plan_or_tools" else "source_lines_validated", "本任务行引用位于对应完整片段内；尚未证明语义支持。")

    def _final(self, output: FinalOutput) -> RunStatus:
        self._checkpoint()
        ids: list[str] = []
        if isinstance(output, DirectoryOutput):
            if self.goal != GOAL:
                raise _Stop("failed")
            ids = [identifier for chapter in output.chapters for identifier in chapter.evidence_ids]
            if not ids:
                raise _Stop("failed")  # Without task evidence, return a gap instead.
            status: RunStatus = "completed"
        elif isinstance(output, (ChapterOutline, SectionExplanation)):
            expected_goal = (FOLLOW_UP_GOAL if isinstance(output, FollowUpExplanation)
                             else OUTLINE_GOAL if isinstance(output, ChapterOutline) else SECTION_GOAL)
            if self.goal != expected_goal:
                raise _Stop("failed")
            if output.topic != self.topic:
                raise _Stop("failed")
            if isinstance(output, SectionExplanation) and output.section_id != self.section_id:
                raise _Stop("failed")
            ids = output.evidence_ids if isinstance(output, ChapterOutline) else [identifier for block in output.blocks for identifier in block.evidence_ids]
            status = "completed"
        elif isinstance(output, ClarificationOutput):
            ids = output.evidence_ids
            status = "clarify"
        elif isinstance(output, GapOutput):
            status = "gap"
        else:
            raise _Stop("failed")
        if any(identifier not in self.evidence for identifier in ids):
            raise _Stop("failed")
        if isinstance(output, FollowUpExplanation):
            self._validate_followup(output, phase="prose" if getattr(self.adapter, "followup_protocol", None) == "two_phase" else None)
        self._event("final_validated", "本任务引用标识已核对；不证明引用支持语义结论。", kind=output.kind, citations=len(ids))
        self._checkpoint()
        self.final_output = output
        self.citations_validated = True
        return status

    def execute(self, initial_context: ModelContext | dict[str, Any] | None) -> RunResult:
        status: RunStatus = "failed"
        try:
            self._check_cancel_time()
            if not self.adapter_contract_valid:
                raise _Stop("failed")
            if self.kind == "unconfigured" or not getattr(self.adapter, "configured", False):
                raise _Stop("not_configured")
            if self.mode != self.kind:
                raise _Stop("failed")
            if isinstance(initial_context, ModelContext):
                values = {"goal": initial_context.goal, "commit": initial_context.commit, "public_url": initial_context.public_url, "anchor": initial_context.anchor}
            elif isinstance(initial_context, dict) and not set(initial_context) - {"goal", "commit", "public_url", "anchor"}:
                values = initial_context
            else:
                raise _Stop("failed")
            try:
                initial = ModelContext.model_validate(values)
                self.goal = initial.goal
                self.section_id = initial.anchor.section_id if initial.anchor is not None else None
                self.topic = initial.anchor.topic if initial.anchor is not None else "query"
            except ValidationError:
                raise _Stop("failed") from None
            identity = getattr(self.adapter, "guidance_sha256", None)
            prompt_identity = {"guidance_sha256": identity} if isinstance(identity, str) and _SHA256.fullmatch(identity) else {}
            self._event("started", "启动有界目录任务；没有读取整个工作区。" if self.goal == GOAL else "启动选定后端章节的有界任务；只按需查阅公开片段。", adapter_kind=self.kind, mode=self.mode, **prompt_identity)
            while True:
                turn = self._model_turn(initial)
                if turn.kind == "final":
                    status = self._final(turn.final_output)
                    break
                if self.no_progress_turns >= self.limits.max_no_progress_turns:
                    raise _Stop("no_progress")
                self.history.append({"kind": "tools", "calls": [call.model_dump() for call in turn.tool_calls]})
                produced_data = False
                for call in turn.tool_calls:
                    result = self._tool_result(call, initial)
                    produced_data = produced_data or (
                        result["status"] == "ok" and bool(result.get("items") or result.get("evidence_id"))
                    )
                self.no_progress_turns = 0 if produced_data else self.no_progress_turns + 1
        except _Stop as stop:
            status = stop.status
        except Exception:
            # Do not leak third-party error strings, Pydantic rejected values,
            # source excerpts or credentials into a result or action log.
            status = "failed"
        if status not in {"completed", "gap", "clarify"}:
            self.final_output = None
            self.citations_validated = False
        try:
            self._event("finished", self._message(status), status=status)
        except _Stop as stop:
            status = stop.status
            self.final_output = None
            self.citations_validated = False
        return RunResult(
            status=status, message=self._message(status), final_output=self.final_output,
            evidence=list(self.evidence.values()), counters=self._snapshot(), adapter_kind=self.kind,
            mode=self.mode, semantic_verified=False, citations_validated=self.citations_validated,
            events=self.events,
        )


    def _message(self, status):
        if status == "completed" and self.goal != GOAL:
            return "学习响应与本任务引用标识通过结构核对；语义正确性尚未验证。"
        return _MESSAGES[status]


def run_agent(
    adapter: ModelAdapter, tools: ToolExecutor, guard: Callable[[], None],
    cancelled: Callable[[], bool], limits: AgentBudget | None = None,
    record_event: EventRecorder | None = None,
    initial_context: ModelContext | dict[str, Any] | None = None,
) -> RunResult:
    """Run one task; callbacks receive safe events with current counter snapshots."""
    budget = AgentBudget.model_validate(limits) if limits is not None else AgentBudget()
    if getattr(adapter, 'followup_protocol', None) == 'two_phase' and (
            budget.max_turns > 6 or budget.max_tool_calls > 12 or budget.max_seconds > 30 or budget.max_output_tokens > 4000):
        raise ValueError('Cannot expand original two-phase limits')
    return _Runner(adapter, tools, guard, cancelled, budget, record_event).execute(initial_context)
