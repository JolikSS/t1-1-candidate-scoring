"""Recover visually separate skill badges in SuperJob PDF exports."""

from __future__ import annotations

import re
from collections import defaultdict
from math import hypot
from typing import NamedTuple


SKILL_BADGE_PREFIX = "▸ "
SUPERJOB = re.compile(r"\bSuperJob\b|superjob\.ru", re.I)
_SKILL_HEADINGS = {"профессиональные навыки", "skills"}


class _TextSpan(NamedTuple):
    x: float
    y: float
    size: float
    text: str


class _Box(NamedTuple):
    left: float
    bottom: float
    right: float
    top: float

    def starts_badge(self, span: _TextSpan) -> bool:
        return (
            0.2 * span.size <= span.x - self.left <= 1.1 * span.size
            and 0.6 * span.size <= span.y - self.bottom <= 1.3 * span.size
            and 2.1 * span.size <= self.top - self.bottom <= 3 * span.size
            and span.x < self.right
        )


def _page_shapes(page) -> tuple[list[_TextSpan], list[_Box]]:
    spans, boxes = [], []
    points, segments = [], []

    def visit_text(text, matrix, text_matrix, _font, size):
        content = " ".join(text.split())
        if content:
            a, b, c, d, tx, ty = matrix
            x, y = text_matrix[4:6]
            spans.append(_TextSpan(a * x + c * y + tx, b * x + d * y + ty, size * hypot(a, b), content))

    def visit_path(operator, operands, matrix, _text_matrix):
        if operator == b"m":
            points.clear()
            segments.clear()
        if operator in (b"m", b"l", b"c", b"v", b"y"):
            a, b, c, d, tx, ty = matrix
            points.extend((a * x + c * y + tx, b * x + d * y + ty) for x, y in zip(operands[::2], operands[1::2]))
            segments.append(operator)
        if operator in (b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"n"):
            # The export draws rounded badge borders with four straight sides.
            if operator in (b"S", b"s") and segments.count(b"l") == 4 and segments.count(b"c") >= 4:
                xs, ys = zip(*points)
                boxes.append(_Box(min(xs), min(ys), max(xs), max(ys)))
            points.clear()
            segments.clear()

    page.extract_text(visitor_text=visit_text, visitor_operand_before=visit_path)
    return spans, boxes


def restore_superjob_skill_badges(page, text: str) -> str:
    """Mark the first-page skill rows only when each item has its own PDF border."""
    lines = text.splitlines(keepends=True)
    headings = [i for i, line in enumerate(lines) if line.strip().casefold() in _SKILL_HEADINGS]
    if len(headings) != 1:
        return text

    spans, boxes = _page_shapes(page)
    heading_spans = [span for span in spans if span.text.casefold() in _SKILL_HEADINGS]
    if not boxes or len(heading_spans) != 1:
        return text

    rows = defaultdict(list)
    for span in spans:
        if span.y < heading_spans[0].y:
            rows[round(span.y, 1)].append(span)

    for index, y in enumerate(sorted(rows, reverse=True), headings[0] + 1):
        if index >= len(lines):
            break
        row = sorted(rows[y], key=lambda span: span.x)
        labels = [span.text for span in row]
        if " ".join(labels) != " ".join(lines[index].split()):
            break
        borders = [tuple(box for box in boxes if box.starts_badge(span)) for span in row]
        if any(len(matches) != 1 for matches in borders) or len(set(borders)) != len(labels):
            break
        ending = lines[index][len(lines[index].rstrip("\r\n")):]
        lines[index] = " ".join(SKILL_BADGE_PREFIX + label for label in labels) + ending
    return "".join(lines)
