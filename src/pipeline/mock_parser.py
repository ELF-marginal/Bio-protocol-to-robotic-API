from __future__ import annotations

import re

from src.models.contracts import ParsedProtocol, ParsedStep, ProtocolInput
from src.pipeline.unit_normalizer import normalize_volume_to_ul


def parse_protocol(protocol: ProtocolInput) -> ParsedProtocol:
    lines = [line.strip() for line in re.split(r"[.;\n]+", protocol.raw_text) if line.strip()]
    steps: list[ParsedStep] = []

    for idx, line in enumerate(lines, start=1):
        lower_line = line.lower()
        action = "unknown"
        entities: dict[str, str] = {}
        parameters: dict[str, float | int] = {}

        if ("take" in lower_line or "pick" in lower_line) and "fridge" in lower_line:
            action = "take"
            entities = {"item": "sample_tube", "source_location": "fridge"}
        elif "add" in lower_line:
            action = "add"
            entities = {
                "source": "lysis_buffer" if "lysis" in lower_line else "buffer",
                "target": "sample_tube" if "tube" in lower_line else "sample_tube",
            }
            normalized_volume = normalize_volume_to_ul(lower_line)
            if normalized_volume is not None:
                parameters["volume_ul"] = int(normalized_volume) if normalized_volume.is_integer() else normalized_volume
        elif "mix" in lower_line:
            action = "mix"
            entities = {"target": "sample_tube" if "tube" in lower_line else "sample_tube"}
            mix_match = re.search(r"(\d+)\s*(times|x)\b", lower_line)
            if mix_match:
                parameters["times"] = int(mix_match.group(1))
        elif "incubate" in lower_line:
            action = "incubate"
            entities = {"target": "sample_tube"}
            temp_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:°|º)?\s*c\b", lower_line)
            if temp_match:
                temperature = float(temp_match.group(1))
                parameters["temperature_c"] = int(temperature) if temperature.is_integer() else temperature
            duration_match = re.search(r"(\d+(?:\.\d+)?)\s*(min|mins|minute|minutes)\b", lower_line)
            if duration_match:
                duration = float(duration_match.group(1))
                parameters["duration_min"] = int(duration) if duration.is_integer() else duration

        steps.append(
            ParsedStep(
                step_id=f"s{idx}",
                raw_text=line,
                action=action,
                entities=entities,
                parameters=parameters,
            )
        )

    return ParsedProtocol(protocol_id=protocol.protocol_id, steps=steps)
