"""Bounded learning artifacts, not model results or billing authorization."""
import json
import re
from typing import Annotated, Literal
from pydantic import Field, model_serializer, model_validator
from .content_contract import StrictContract, _IDENTIFIER, _safe_tree
from .source_policy import require_allowed_path

LearningTopic = Literal["query", "submission", "consumption", "judging"]


def _references(values):
    if len(set(values)) != len(values) or any(re.fullmatch(_IDENTIFIER, item) is None for item in values):
        raise ValueError("Invalid or duplicate evidence identifiers")


def _bounded(value, limit=12000):
    _safe_tree(value)
    if len(json.dumps(value, ensure_ascii=False)) > limit:
        raise ValueError("Learning content exceeds aggregate limit")


class SectionPlan(StrictContract):
    id: str = Field(pattern=_IDENTIFIER)
    title: str = Field(min_length=1, max_length=80)
    objective: str = Field(min_length=1, max_length=300)
    entry_hints: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def bounded_plan(self):
        if not self.title.strip() or not self.objective.strip():
            raise ValueError("Section plan cannot be blank")
        for path in self.entry_hints:
            require_allowed_path(path)
        _bounded(self.model_dump())
        return self


class ChapterOutline(StrictContract):
    kind: Literal["chapter_outline"] = "chapter_outline"
    topic: LearningTopic = "query"
    title: str = Field(min_length=1, max_length=80)
    objective: str = Field(min_length=1, max_length=800)
    sections: list[SectionPlan] = Field(min_length=1, max_length=8)
    evidence_ids: list[str] = Field(min_length=1, max_length=16)
    gaps: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def bounded_outline(self):
        if not self.title.strip() or not self.objective.strip() or len({s.id for s in self.sections}) != len(self.sections):
            raise ValueError("Blank outline or duplicate section identifiers")
        if any(not item.strip() or len(item) > 300 for item in self.gaps):
            raise ValueError("Outline gaps must be bounded")
        _references(self.evidence_ids)
        _bounded(self.model_dump())
        return self


class ExplanationBlock(StrictContract):
    category: Literal["inputs", "processing", "next_hop", "outputs", "mechanism", "failure", "gap"]
    heading: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1200)
    support: Literal["fact", "documentation", "inference", "candidate", "gap"]
    evidence_ids: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def supported_block(self):
        if not self.heading.strip() or not self.text.strip():
            raise ValueError("Explanation cannot be blank")
        if self.support in {"fact", "documentation", "inference"} and not self.evidence_ids:
            raise ValueError("Supported explanation requires evidence")
        if self.category == "gap" and self.support != "gap":
            raise ValueError("A gap cannot be promoted to a fact")
        _references(self.evidence_ids)
        _bounded(self.model_dump())
        return self


class SectionExplanation(StrictContract):
    kind: Literal["section_explanation"] = "section_explanation"
    topic: LearningTopic = "query"
    section_id: str = Field(pattern=_IDENTIFIER)
    title: str = Field(min_length=1, max_length=80)
    blocks: list[ExplanationBlock] = Field(min_length=1, max_length=12)
    gaps: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def bounded_explanation(self):
        if not self.title.strip() or any(not item.strip() or len(item) > 300 for item in self.gaps):
            raise ValueError("Explanation title or gaps invalid")
        _bounded(self.model_dump())
        return self


class SourceSpan(StrictContract):
    evidence_id: str = Field(pattern=_IDENTIFIER)
    start_line: int = Field(ge=1, le=1_000_000)
    end_line: int = Field(ge=1, le=1_000_000)

    @model_validator(mode="after")
    def short_ordered_span(self):
        if not self.start_line <= self.end_line < self.start_line + 20:
            raise ValueError("A source span must cover at most twenty ordered lines")
        return self


class SourceLineReference(StrictContract):
    evidence_id: str = Field(pattern=_IDENTIFIER)
    lines: list[Annotated[int, Field(ge=1, le=1_000_000)]] = Field(
        min_length=1, max_length=20, json_schema_extra={"uniqueItems": True})

    @model_validator(mode="after")
    def unique_lines(self):
        if len(set(self.lines)) != len(self.lines):
            raise ValueError("Source line numbers must be unique")
        return self


class FollowUpBlock(ExplanationBlock):
    # Empty only for historical payloads or unsupported/gap statements. The
    # live engine requires explicit source lines for new supported blocks.
    source_spans: list[SourceSpan] = Field(default_factory=list, max_length=16)
    source_lines: list[SourceLineReference] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def aligned_span_identifiers(self):
        if self.source_lines and self.source_spans:
            raise ValueError("A block cannot mix old spans and source lines")
        if self.source_lines:
            ids = [reference.evidence_id for reference in self.source_lines]
            if len(set(ids)) != len(ids) or set(ids) != set(self.evidence_ids):
                raise ValueError("Source line references must match the block's evidence identifiers")
        if self.source_spans:
            keys = [(s.evidence_id, s.start_line, s.end_line) for s in self.source_spans]
            if len(set(keys)) != len(keys) or {s.evidence_id for s in self.source_spans} != set(self.evidence_ids):
                raise ValueError("Source spans must match the block's evidence identifiers")
        return self

    @model_serializer(mode="wrap")
    def preserve_historical_shape(self, serialize):
        value = serialize(self)
        if not self.source_spans:
            value.pop("source_spans", None)
        if not self.source_lines:
            value.pop("source_lines", None)
        return value


class FollowUpExplanation(SectionExplanation):
    kind: Literal["follow_up"] = "follow_up"
    blocks: list[FollowUpBlock] = Field(min_length=1, max_length=12)


class CitationBoundsError(ValueError):
    def __init__(self, code, *, block_index=None, location=None, reason=None, missing_lines=()):
        allowed = {"missing_source_lines", "legacy_format", "unread_evidence", "truncated_evidence", "outside_evidence", "unselected_explicit_line"}
        self.code = code if code in allowed else "outside_evidence"
        self.block_index = block_index if type(block_index) is int and 1 <= block_index <= 12 else None
        self.location = location if isinstance(location, str) and location in {"heading", "text", "source_lines", "source_spans"} else None
        reasons = allowed | {"not_selected", "reversed_range", "range_too_wide", "invalid_line_number"}
        self.reason = reason if isinstance(reason, str) and reason in reasons else self.code
        self.missing_lines = tuple(missing_lines) if (
            isinstance(missing_lines, (list, tuple)) and len(missing_lines) <= 320
            and all(type(n) is int and 1 <= n <= 1_000_000 for n in missing_lines)) else ()
        super().__init__("Follow-up citation boundary check failed")


def validate_followup_spans(content, references, *, require_supported=False):
    """Check provenance and visible line bounds, never semantic entailment.

    Historical records with or without spans remain readable/replayable. New
    results require explicit line lists; no evidence identifiers are filled in.
    """
    for block_index, block in enumerate(content.blocks, 1):
        if require_supported and block.source_spans:
            raise CitationBoundsError("legacy_format", block_index=block_index, location="source_spans")
        if require_supported and block.support in {"fact", "documentation", "inference"} and not block.source_lines:
            raise CitationBoundsError("missing_source_lines", block_index=block_index, location="source_lines")
        for line_reference in block.source_lines:
            reference = references.get(line_reference.evidence_id)
            if reference is None:
                raise CitationBoundsError("unread_evidence", block_index=block_index, location="source_lines")
            if reference["truncated"]:
                raise CitationBoundsError("truncated_evidence", block_index=block_index, location="source_lines")
            if any(not reference["start_line"] <= line <= reference["end_line"] for line in line_reference.lines):
                raise CitationBoundsError("outside_evidence", block_index=block_index, location="source_lines")
        for span in block.source_spans:
            reference = references.get(span.evidence_id)
            if (reference is None or reference["truncated"]
                    or not reference["start_line"] <= span.start_line <= span.end_line <= reference["end_line"]):
                raise ValueError("Source span is not inside complete task evidence")


class FollowUpSelection(StrictContract):
    body_id: str = Field(pattern=_IDENTIFIER)
    question: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def safe_question(self):
        if not self.question.strip():
            raise ValueError("Question cannot be blank")
        _bounded(self.model_dump(), 1200)
        return self


class OutlineSelection(StrictContract):
    directory_task_id: str = Field(pattern=_IDENTIFIER)
    chapter_index: int = Field(ge=0, le=11)
    topic: LearningTopic = "query"


class SectionSelection(StrictContract):
    outline_id: str = Field(pattern=_IDENTIFIER)
    section_id: str = Field(pattern=_IDENTIFIER)


class LearningAnchor(StrictContract):
    directory_task_id: str = Field(pattern=_IDENTIFIER)
    chapter_index: int = Field(ge=0, le=11)
    topic: LearningTopic = "query"
    title: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=400)
    entry_hints: list[str] = Field(default_factory=list, max_length=8)
    outline_id: str | None = Field(default=None, pattern=_IDENTIFIER)
    section_id: str | None = Field(default=None, pattern=_IDENTIFIER)
    section_title: str | None = Field(default=None, min_length=1, max_length=80)
    section_objective: str | None = Field(default=None, min_length=1, max_length=300)
    body_id: str | None = Field(default=None, pattern=_IDENTIFIER)
    question: str | None = Field(default=None, min_length=1, max_length=500)
    previous_excerpt: str | None = Field(default=None, max_length=3000)
    previous_excerpt_truncated: bool = False

    @model_validator(mode="after")
    def safe_anchor(self):
        for path in self.entry_hints:
            require_allowed_path(path)
        _bounded(self.model_dump())
        return self
