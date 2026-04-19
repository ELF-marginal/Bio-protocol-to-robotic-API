from __future__ import annotations

import re


VOLUME_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ul|μl|uml|microliter|microliters|ml)\b",
    re.IGNORECASE,
)


def normalize_volume_to_ul(text: str) -> float | None:
    match = VOLUME_PATTERN.search(text)
    if not match:
        return None

    value = float(match.group("value"))
    unit = match.group("unit").lower()
    if unit == "ml":
        return value * 1000.0
    return value
