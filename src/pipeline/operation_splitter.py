from __future__ import annotations

import re
from typing import Any


_SECTION_HEADER_PATTERN = re.compile(r"^(?:[A-Z]\d*\.|[A-Z]\.)\s+")


def split_operations(raw_text: str) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    section_hint: str | None = None

    lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        lower = stripped.lower()
        is_section_header = bool(_SECTION_HEADER_PATTERN.match(stripped)) or lower in {
            "procedure",
            "materials",
            "methods",
            "results",
            "discussion",
        }
        if is_section_header:
            section_hint = stripped

        operation_id = f"op_{len(operations) + 1:03d}"
        operations.append(
            {
                "operation_id": operation_id,
                "raw_text": stripped,
                "line_no": idx,
                "section_hint": section_hint,
                "is_section_header": is_section_header,
            }
        )

    return operations
