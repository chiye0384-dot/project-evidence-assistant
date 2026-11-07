"""Consistency of explicit 第...行 references, never natural-language entailment."""
import re
from .learning_contract import CitationBoundsError

_PART = r'\d+(?:\s*[-~～—–至到]\s*\d+)?'
_MENTION = re.compile(r'第\s*(' + _PART + r'(?:\s*[、,，]\s*' + _PART + r')*)\s*行')
_RANGE = re.compile(r'\s*[-~～—–至到]\s*')


def validate_explicit_line_mentions(content):
    # New publication path only. Old persisted answers remain readable as saved.
    # An explicit coordinate needs this block's own selected lines, including
    # candidate claims and questions about unknowns. Do not infer intent, attach
    # references, or borrow them from another block to make the prose pass.
    for block_index, block in enumerate(content.blocks, 1):
        selected = {line for ref in block.source_lines for line in ref.lines}
        for location, text in (("heading", block.heading), ("text", block.text)):
            for mention in _MENTION.finditer(text):
                for part in re.split(r'\s*[、,，]\s*', mention.group(1)):
                    bounds = list(map(int, _RANGE.split(part)))
                    start, end = bounds[0], bounds[-1]
                    reason = ("reversed_range" if end < start else "range_too_wide" if end-start >= 320
                              else "invalid_line_number" if start < 1 or end > 1_000_000 else None)
                    missing = () if reason else tuple(line for line in range(start, end+1) if line not in selected)
                    if reason or missing:
                        raise CitationBoundsError('unselected_explicit_line', block_index=block_index,
                                                  location=location, reason=reason or "not_selected", missing_lines=missing)
