from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from src.models.contracts import ApiCall, ParsedProtocol, ParsedStep, ProtocolInput
from src.pipeline.llm_grounder import run_grounder_backend
from src.pipeline.llm_parser import run_parser_backend


def run_operation_parser_pass(
    protocol: ProtocolInput,
    operations: list[dict[str, Any]],
    enable_llm_parser: bool,
    parser_config: dict[str, Any],
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    operation_parser_groups: list[dict[str, Any]] = []
    flattened_steps: list[ParsedStep] = []

    llm_invoked_count = 0
    llm_accepted_count = 0
    llm_fallback_count = 0

    total_operations = len(operations)
    for idx, op in enumerate(operations, start=1):
        operation_id = str(op.get("operation_id", f"op_{idx:03d}"))
        if progress_callback is not None:
            progress_callback(f"Parser operation {idx}/{total_operations}: {operation_id}")
        op_protocol = ProtocolInput(
            protocol_id=f"{protocol.protocol_id}_{operation_id}",
            title=f"{protocol.title}:{operation_id}",
            source=protocol.source,
            raw_text=str(op.get("raw_text", "")),
        )
        parser_backend = run_parser_backend(
            protocol=op_protocol,
            enable_llm_parser=enable_llm_parser,
            config=dict(parser_config),
        )
        parsed: ParsedProtocol = parser_backend["parsed"]
        llm_result = parser_backend.get("llm_parser_result", {})

        if llm_result.get("llm_parser_invoked", False):
            llm_invoked_count += 1
        if llm_result.get("llm_parser_accepted", False):
            llm_accepted_count += 1
        if llm_result.get("llm_parser_fallback_used", False):
            llm_fallback_count += 1

        operation_parser_groups.append(
            {
                "operation_id": operation_id,
                "operation_raw_text": op.get("raw_text", ""),
                "line_no": op.get("line_no"),
                "section_hint": op.get("section_hint"),
                "is_section_header": op.get("is_section_header", False),
                "parser_preprocess": parser_backend.get("parser_preprocess", {}),
                "llm_parser_result": llm_result,
                "llm_parser_input": parser_backend.get("llm_parser_input"),
                "llm_parser_raw_output": parser_backend.get("llm_parser_raw_output"),
                "llm_parser_parsed_output": parser_backend.get("llm_parser_parsed_output"),
                "parsed_protocol": parsed.model_dump(),
                "steps": [step.model_dump() for step in parsed.steps],
            }
        )

        for step in parsed.steps:
            flattened_steps.append(
                ParsedStep(
                    step_id=f"{operation_id}_{step.step_id}",
                    raw_text=step.raw_text,
                    action=step.action,
                    entities=step.entities,
                    parameters=step.parameters,
                )
            )

    flattened_parsed = ParsedProtocol(protocol_id=protocol.protocol_id, steps=flattened_steps)
    aggregate_parser_result = {
        "parser_backend_mode": "operation_loop",
        "operation_count": len(operations),
        "llm_parser_invoked_count": llm_invoked_count,
        "llm_parser_accepted_count": llm_accepted_count,
        "llm_parser_fallback_count": llm_fallback_count,
        "llm_parser_failure_reason": None,
    }
    return {
        "operation_parser_groups": operation_parser_groups,
        "flattened_parsed_protocol": flattened_parsed,
        "aggregate_parser_result": aggregate_parser_result,
    }


def run_operation_grounder_pass(
    protocol: ProtocolInput,
    operation_parser_groups: list[dict[str, Any]],
    enable_llm_grounder: bool,
    grounder_config: dict[str, Any],
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    operation_grounder_groups: list[dict[str, Any]] = []
    operation_api_groups: list[dict[str, Any]] = []

    llm_invoked_count = 0
    llm_accepted_count = 0
    llm_fallback_count = 0
    contains_unregistered = False
    unregistered_apis: set[str] = set()

    total_groups = len(operation_parser_groups)
    for idx, group in enumerate(operation_parser_groups, start=1):
        operation_id = str(group.get("operation_id"))
        if progress_callback is not None:
            progress_callback(f"Grounder operation {idx}/{total_groups}: {operation_id}")
        parsed_payload = group.get("parsed_protocol", {})
        parsed = ParsedProtocol.model_validate(parsed_payload)

        grounder_backend = run_grounder_backend(
            parsed_protocol=parsed,
            enable_llm_grounder=enable_llm_grounder,
            config=dict(grounder_config),
            include_lab_state=False,
        )
        workflow = grounder_backend["workflow"]
        grounding_result = grounder_backend.get("grounding_result", {})
        grounding_validation = grounder_backend.get("grounding_validation_result", {})

        if grounding_result.get("llm_grounder_invoked", False):
            llm_invoked_count += 1
        if grounding_result.get("llm_grounder_accepted", False):
            llm_accepted_count += 1
        if grounding_result.get("llm_grounder_fallback_used", False):
            llm_fallback_count += 1
        if grounding_result.get("contains_unregistered_api", False):
            contains_unregistered = True
            for api in grounding_result.get("unregistered_apis", []):
                unregistered_apis.add(str(api))

        operation_group = {
            "operation_id": operation_id,
            "operation_raw_text": group.get("operation_raw_text", ""),
            "line_no": group.get("line_no"),
            "section_hint": group.get("section_hint"),
            "is_section_header": group.get("is_section_header", False),
            "grounding_result": grounding_result,
            "grounding_validation_result": grounding_validation,
            "llm_grounder_input": grounder_backend.get("llm_grounder_input"),
            "llm_grounder_raw_output": grounder_backend.get("llm_grounder_raw_output"),
            "llm_grounder_parsed_output": grounder_backend.get("llm_grounder_parsed_output"),
            "workflow": workflow.model_dump(),
            "api_calls": [call.model_dump() for call in workflow.api_calls],
        }
        operation_grounder_groups.append(operation_group)
        operation_api_groups.append(
            {
                "operation_id": operation_id,
                "operation_raw_text": group.get("operation_raw_text", ""),
                "api_calls": [call.model_dump() for call in workflow.api_calls],
            }
        )

    aggregate_grounding_result = {
        "grounding_backend_mode": "operation_loop",
        "grounding_valid": True,
        "contains_unregistered_api": contains_unregistered,
        "unregistered_apis": sorted(unregistered_apis),
        "grounding_failure_reason": None,
        "llm_grounder_invoked_count": llm_invoked_count,
        "llm_grounder_accepted_count": llm_accepted_count,
        "llm_grounder_fallback_count": llm_fallback_count,
        "operation_count": len(operation_parser_groups),
    }
    aggregate_grounding_validation = {
        "grounding_valid": True,
        "failure_reason": None,
        "contains_unregistered_api": contains_unregistered,
        "unregistered_apis": sorted(unregistered_apis),
        "issues": [],
    }
    return {
        "operation_grounder_groups": operation_grounder_groups,
        "operation_api_groups": operation_api_groups,
        "aggregate_grounding_result": aggregate_grounding_result,
        "aggregate_grounding_validation_result": aggregate_grounding_validation,
    }


def workflow_to_operation_groups(workflow_api_calls: list[ApiCall], operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    op_by_id = {str(op.get("operation_id")): op for op in operations}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for call in workflow_api_calls:
        source_id = _source_operation_id(call.source_step_id)
        if source_id is None:
            source_id = "op_unknown"
        grouped.setdefault(source_id, [])
        grouped[source_id].append(call.model_dump())

    out: list[dict[str, Any]] = []
    for operation_id, api_calls in grouped.items():
        op = op_by_id.get(operation_id, {})
        out.append(
            {
                "operation_id": operation_id,
                "operation_raw_text": op.get("raw_text", ""),
                "api_calls": api_calls,
            }
        )
    return out


def _source_operation_id(source_step_id: str | None) -> str | None:
    if not source_step_id:
        return None
    match = re.match(r"^(op_\d+)_", source_step_id)
    if not match:
        return None
    return match.group(1)
