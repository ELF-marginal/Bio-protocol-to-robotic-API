from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from src.models.contracts import ApiCall, Workflow


class APIConstraint(BaseModel):
    name: str
    description: str = ""
    required_args: list[str] = Field(default_factory=list)
    arg_types: dict[str, str] = Field(default_factory=dict)


class LLMRepairInput(BaseModel):
    protocol_text: str
    parsed_steps: list[dict[str, Any]]
    workflow_before_llm_repair: list[dict[str, Any]]
    validation_issues_before_llm: list[dict[str, Any]]
    applied_rule_repairs: list[dict[str, Any]]
    remaining_issues_after_rule_repair: list[dict[str, Any]]
    available_apis: list[APIConstraint]
    lab_state_summary: dict[str, Any]
    task: str
    lab_state_initial: dict[str, Any] = Field(default_factory=dict)
    lab_state_expected: dict[str, Any] = Field(default_factory=dict)
    notice: str = ""


class RepairNewCall(BaseModel):
    api: str
    args: dict[str, Any] = Field(default_factory=dict)


class RepairOperation(BaseModel):
    op: str
    target_call_id: str
    new_call: RepairNewCall | None = None


class LLMRepairOutput(BaseModel):
    decision: str
    reasoning_summary: str
    operations: list[RepairOperation]
    assumptions: list[str] = Field(default_factory=list)


def load_llm_repair_config(path: str = "configs/llm_repair_config.yaml") -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return _default_config()
    loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or not isinstance(loaded.get("llm_repair"), dict):
        return _default_config()
    merged = _default_config()
    merged.update(loaded["llm_repair"])
    return merged


def should_invoke_llm_repair(
    enable_llm_repair: bool,
    config: dict[str, Any],
    remaining_issues: list[dict[str, Any]],
) -> tuple[bool, str]:
    if not enable_llm_repair:
        return False, "llm_repair_flag_disabled"
    if not config.get("enabled", True):
        return False, "llm_repair_config_disabled"
    if config.get("only_when_unresolved", True) and not remaining_issues:
        return False, "no_remaining_issues"

    allowed = set(config.get("allowed_issue_types", []))
    if not allowed:
        return False, "allowed_issue_types_empty"

    if not any(str(issue.get("issue_type")) in allowed for issue in remaining_issues):
        return False, "issue_type_not_allowed"
    return True, "invoke"


def build_llm_input(
    protocol_text: str,
    parsed_steps: list[dict[str, Any]],
    workflow_before_llm_repair: Workflow,
    validation_issues_before_llm: list[dict[str, Any]],
    applied_rule_repairs: list[dict[str, Any]],
    remaining_issues_after_rule_repair: list[dict[str, Any]],
    api_registry_path: str = "configs/api_registry.yaml",
    initial_lab_state_path: str = "configs/initial_lab_state.yaml",
    expected_lab_state_path: str = "configs/initial_lab_state.yaml",
    notice_path: str = "configs/llm_repair_notice.txt",
) -> dict[str, Any]:
    available_apis = _build_available_apis_min_pack(
        api_registry_path=api_registry_path,
        remaining_issues=remaining_issues_after_rule_repair,
    )
    lab_state_summary = _build_min_lab_state_summary(
        initial_lab_state_path=initial_lab_state_path,
        workflow=workflow_before_llm_repair,
        remaining_issues=remaining_issues_after_rule_repair,
    )
    lab_state_initial = _load_lab_state(initial_lab_state_path)
    lab_state_expected = _load_lab_state(expected_lab_state_path)
    notice = _load_notice_text(notice_path)

    payload = LLMRepairInput(
        protocol_text=protocol_text,
        parsed_steps=parsed_steps,
        workflow_before_llm_repair=[call.model_dump() for call in workflow_before_llm_repair.api_calls],
        validation_issues_before_llm=validation_issues_before_llm,
        applied_rule_repairs=applied_rule_repairs,
        remaining_issues_after_rule_repair=remaining_issues_after_rule_repair,
        available_apis=available_apis,
        lab_state_summary=lab_state_summary,
        lab_state_initial=lab_state_initial,
        lab_state_expected=lab_state_expected,
        notice=notice,
        task="Repair the workflow so it resolves remaining validation issues while preserving protocol intent.",
    )
    return payload.model_dump()


def invoke_llm_repair(
    llm_input: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    provider = str(config.get("provider", "openai")).lower()
    model = str(config.get("model", "gpt-4.1"))
    temperature = float(config.get("temperature", 0.0))
    max_retries = int(config.get("max_retries", 2))
    timeout_sec = int(config.get("timeout_sec", 30))

    if provider == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            return {
                "llm_invoked": True,
                "llm_output_valid_json": False,
                "raw_output": "",
                "parsed_output": None,
                "failure_reason": "missing_openai_api_key",
                "provider": provider,
                "model": model,
            }
        return {
            "llm_invoked": True,
            "llm_output_valid_json": False,
            "raw_output": "",
            "parsed_output": None,
            "failure_reason": "provider_call_not_implemented_yet",
            "provider": provider,
            "model": model,
        }

    if provider != "deepseek":
        return {
            "llm_invoked": True,
            "llm_output_valid_json": False,
            "raw_output": "",
            "parsed_output": None,
            "failure_reason": "unsupported_provider",
            "provider": provider,
            "model": model,
        }

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return {
            "llm_invoked": True,
            "llm_output_valid_json": False,
            "raw_output": "",
            "parsed_output": None,
            "failure_reason": "missing_deepseek_api_key",
            "provider": provider,
            "model": model,
        }

    endpoint = str(config.get("endpoint", "https://api.deepseek.com/chat/completions"))
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": _build_llm_messages(llm_input),
    }

    last_error = "provider_request_failed"
    for _ in range(max_retries + 1):
        try:
            raw_response = _http_post_json(
                endpoint=endpoint,
                payload=payload,
                api_key=api_key,
                timeout_sec=timeout_sec,
            )
            content = _extract_deepseek_content(raw_response)
            if not content:
                last_error = "empty_model_output"
                continue
            parsed_output, parse_error = parse_llm_output(content)
            if parse_error is None and parsed_output is not None:
                return {
                    "llm_invoked": True,
                    "llm_output_valid_json": True,
                    "raw_output": content,
                    "parsed_output": parsed_output.model_dump(),
                    "failure_reason": None,
                    "provider": provider,
                    "model": model,
                }
            return {
                "llm_invoked": True,
                "llm_output_valid_json": False,
                "raw_output": content,
                "parsed_output": None,
                "failure_reason": parse_error,
                "provider": provider,
                "model": model,
            }
        except TimeoutError:
            last_error = "provider_timeout"
        except urllib.error.HTTPError as exc:
            last_error = f"http_error_{exc.code}"
        except Exception:
            last_error = "provider_request_failed"

    return {
        "llm_invoked": True,
        "llm_output_valid_json": False,
        "raw_output": "",
        "parsed_output": None,
        "failure_reason": last_error,
        "provider": provider,
        "model": model,
    }


def parse_llm_output(raw_output: str) -> tuple[LLMRepairOutput | None, str | None]:
    try:
        loaded = json.loads(raw_output)
    except Exception:
        return None, "invalid_json"
    try:
        parsed = LLMRepairOutput.model_validate(loaded)
    except ValidationError:
        return None, "invalid_output_schema"
    return parsed, None


def validate_operations_before_apply(
    operations: list[dict[str, Any]],
    workflow: Workflow,
    api_registry: dict[str, dict[str, Any]] | None = None,
    api_registry_path: str = "configs/api_registry.yaml",
) -> tuple[bool, str | None]:
    registry = api_registry if api_registry is not None else _load_api_registry(api_registry_path)
    call_ids = {call.call_id for call in workflow.api_calls}
    used_targets: set[tuple[str, str]] = set()

    for op_item in operations:
        try:
            op = RepairOperation.model_validate(op_item)
        except ValidationError:
            return False, "invalid_operation_schema"

        if op.target_call_id not in call_ids:
            return False, "target_call_id_not_found"

        if op.op in {"insert_before", "insert_after", "replace_call"}:
            if op.new_call is None:
                return False, "new_call_required"
            if op.new_call.api not in registry:
                return False, "unknown_api"
            required_args = registry[op.new_call.api]["required_args"]
            if any(arg not in op.new_call.args for arg in required_args):
                return False, "required_args_missing"

        if op.op == "delete_call":
            return False, "operation_not_supported_delete_call"

        op_key = (op.op, op.target_call_id)
        if op_key in used_targets and op.op in {"replace_call", "delete_call"}:
            return False, "operation_conflict"
        used_targets.add(op_key)

    return True, None


def apply_llm_operations(
    workflow: Workflow,
    operations: list[dict[str, Any]],
    api_registry: dict[str, dict[str, Any]],
) -> tuple[Workflow | None, dict[str, Any]]:
    ok, error = validate_operations_before_apply(
        operations=operations,
        workflow=workflow,
        api_registry=api_registry,
    )
    if not ok:
        return None, {
            "patch_applied": False,
            "error": error,
            "operations_count": len(operations),
            "applied_operations": [],
        }

    parsed_ops: list[RepairOperation] = []
    for op_item in operations:
        try:
            parsed_ops.append(RepairOperation.model_validate(op_item))
        except ValidationError:
            return None, {
                "patch_applied": False,
                "error": "invalid_operation_schema",
                "operations_count": len(operations),
                "applied_operations": [],
            }

    calls = [ApiCall.model_validate(call.model_dump()) for call in workflow.api_calls]
    applied_operations: list[dict[str, Any]] = []

    for op in parsed_ops:
        index = _find_call_index(calls, op.target_call_id)
        if index is None:
            return None, {
                "patch_applied": False,
                "error": "target_call_id_not_found",
                "operations_count": len(operations),
                "applied_operations": applied_operations,
            }

        if op.op == "insert_before":
            new_call = ApiCall(call_id="", api=op.new_call.api, args=op.new_call.args)
            calls.insert(index, new_call)
        elif op.op == "insert_after":
            new_call = ApiCall(call_id="", api=op.new_call.api, args=op.new_call.args)
            calls.insert(index + 1, new_call)
        elif op.op == "replace_call":
            new_call = ApiCall(
                call_id=calls[index].call_id,
                api=op.new_call.api,
                args=op.new_call.args,
                source_step_id=calls[index].source_step_id,
            )
            calls[index] = new_call
        else:
            return None, {
                "patch_applied": False,
                "error": "operation_not_supported",
                "operations_count": len(operations),
                "applied_operations": applied_operations,
            }

        applied_operations.append(
            {
                "op": op.op,
                "target_call_id": op.target_call_id,
                "new_call": op.new_call.model_dump() if op.new_call else None,
            }
        )

    patched = Workflow(
        workflow_id=workflow.workflow_id,
        protocol_id=workflow.protocol_id,
        api_calls=_renumber_calls(calls),
    )
    return patched, {
        "patch_applied": True,
        "error": None,
        "operations_count": len(operations),
        "applied_operations": applied_operations,
    }


def _build_available_apis_min_pack(
    api_registry_path: str,
    remaining_issues: list[dict[str, Any]],
) -> list[APIConstraint]:
    registry = _load_api_registry(api_registry_path)
    issue_apis = {str(issue.get("api")) for issue in remaining_issues if issue.get("api")}
    candidate_names = set(issue_apis)
    if not candidate_names:
        candidate_names = set(registry.keys())

    # Keep minimal but useful local neighborhood for frequent repairs.
    candidate_names.update({"tube.uncap", "pipette.attach_tip", "heater.set_temperature"})

    out: list[APIConstraint] = []
    for name in sorted(candidate_names):
        if name not in registry:
            continue
        item = registry[name]
        out.append(
            APIConstraint(
                name=name,
                description=item.get("description", ""),
                required_args=item.get("required_args", []),
                arg_types=item.get("arg_types", {}),
            )
        )
    return out


def _build_min_lab_state_summary(
    initial_lab_state_path: str,
    workflow: Workflow,
    remaining_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    state = _load_lab_state(initial_lab_state_path)

    related_tubes: set[str] = set()
    related_reagents: set[str] = set()
    for call in workflow.api_calls:
        target = call.args.get("target")
        container = call.args.get("container")
        tube_id = call.args.get("tube_id")
        source = call.args.get("source")
        item = call.args.get("item")
        for value in (target, container, tube_id, item):
            if isinstance(value, str) and "tube" in value:
                related_tubes.add(value)
        if isinstance(source, str):
            related_reagents.add(source)

    for issue in remaining_issues:
        msg = str(issue.get("message", ""))
        for token in ("sample_tube", "buffer", "lysis_buffer"):
            if token in msg and "tube" in token:
                related_tubes.add(token)
            if token in msg and "tube" not in token:
                related_reagents.add(token)

    summary = {
        "fridge": {"is_open": state.get("fridge", {}).get("is_open")},
        "pipette": {"has_tip": state.get("pipette", {}).get("has_tip")},
        "heater": {
            "temperature_c": state.get("heater", {}).get("temperature_c"),
            "items": state.get("heater", {}).get("items", []),
        },
        "tubes": {},
        "reagents": {},
    }
    for tube in sorted(related_tubes):
        if tube in state.get("tubes", {}):
            summary["tubes"][tube] = state["tubes"][tube]
    for reagent in sorted(related_reagents):
        if reagent in state.get("reagents", {}):
            summary["reagents"][reagent] = state["reagents"][reagent]
    return summary


def _load_api_registry(path: str) -> dict[str, dict[str, Any]]:
    payload = Path(path).read_text(encoding="utf-8")
    loaded = yaml.safe_load(payload)
    apis = loaded.get("apis", []) if isinstance(loaded, dict) else []
    registry: dict[str, dict[str, Any]] = {}
    for item in apis:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        name = item["name"]
        params = item.get("parameters", {})
        required_args: list[str] = []
        arg_types: dict[str, str] = {}
        if isinstance(params, dict):
            for key, value in params.items():
                required_args.append(key)
                if isinstance(value, str):
                    arg_types[key] = value
                elif isinstance(value, dict):
                    arg_types[key] = str(value.get("type", "unknown"))
                else:
                    arg_types[key] = "unknown"
        registry[name] = {
            "description": str(item.get("description", "")),
            "required_args": required_args,
            "arg_types": arg_types,
        }
    return registry


def _load_lab_state(path: str) -> dict[str, Any]:
    state_path = Path(path)
    if not state_path.exists():
        return {}
    loaded = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _load_notice_text(path: str) -> str:
    notice_path = Path(path)
    if not notice_path.exists():
        return ""
    return notice_path.read_text(encoding="utf-8")


def load_api_registry(path: str = "configs/api_registry.yaml") -> dict[str, dict[str, Any]]:
    return _load_api_registry(path)


def _find_call_index(calls: list[ApiCall], call_id: str) -> int | None:
    for idx, call in enumerate(calls):
        if call.call_id == call_id:
            return idx
    return None


def _renumber_calls(calls: list[ApiCall]) -> list[ApiCall]:
    out: list[ApiCall] = []
    for idx, call in enumerate(calls, start=1):
        out.append(
            ApiCall(
                call_id=f"c{idx}",
                api=call.api,
                args=call.args,
                source_step_id=call.source_step_id,
            )
        )
    return out


def _default_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "provider": "deepseek",
        "model": "deepseek-chat",
        "temperature": 0.0,
        "max_retries": 2,
        "timeout_sec": 30,
        "endpoint": "https://api.deepseek.com/chat/completions",
        "mode": "operations",
        "only_when_unresolved": True,
        "save_debug_files": True,
        "require_json_output": True,
        "require_api_registry_constraint": True,
        "allow_full_workflow_rewrite": False,
        "api_registry_path": "configs/api_registry.yaml",
        "initial_lab_state_path": "configs/initial_lab_state.yaml",
        "expected_lab_state_path": "configs/initial_lab_state.yaml",
        "notice_path": "configs/llm_repair_notice.txt",
        "allowed_issue_types": ["PreconditionViolation", "OrderViolation", "MissingParameter"],
    }


def _build_llm_messages(llm_input: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You repair lab workflows. You must preserve protocol intent. "
                "Use only the provided APIs. Return valid JSON only. "
                "Do not invent unavailable APIs. Prefer minimal local edits."
            ),
        },
        {
            "role": "system",
            "content": (
                "Resolve only remaining validation issues after rule-based repair. "
                "Keep as much original workflow as possible. "
                "Do not modify correct steps unless necessary. "
                "Output must be machine-readable JSON."
            ),
        },
        {"role": "user", "content": json.dumps(llm_input, ensure_ascii=False)},
    ]


def _http_post_json(
    endpoint: str,
    payload: dict[str, Any],
    api_key: str,
    timeout_sec: int,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url=endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        body = response.read().decode("utf-8")
    loaded = json.loads(body)
    if not isinstance(loaded, dict):
        raise ValueError("invalid_response_shape")
    return loaded


def _extract_deepseek_content(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices", [])
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message", {})
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    return content if isinstance(content, str) else ""
