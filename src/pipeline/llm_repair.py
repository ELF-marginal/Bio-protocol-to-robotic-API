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
    arg_types: dict[str, Any] = Field(default_factory=dict)
    preconditions: dict[str, Any] = Field(default_factory=dict)
    effects: dict[str, Any] = Field(default_factory=dict)


class LLMRepairInput(BaseModel):
    protocol_text: str
    parsed_steps: list[dict[str, Any]]
    workflow_before_llm_repair: list[dict[str, Any]]
    validation_issues_before_llm: list[dict[str, Any]]
    applied_rule_repairs: list[dict[str, Any]]
    remaining_issues_after_rule_repair: list[dict[str, Any]]
    available_apis: list[APIConstraint]
    api_domain: dict[str, Any]
    safety_rules: list[dict[str, Any]] = Field(default_factory=list)
    lab_state_initial: dict[str, Any]
    simulated_final_state_before_repair: dict[str, Any]
    task: str


class RepairNewCall(BaseModel):
    call_id: str | None = None
    api: str
    args: dict[str, Any] = Field(default_factory=dict)
    source_step_id: str | None = None


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
    api_domain_path: str = "configs/api_registry.yaml",
    lab_state_path: str = "configs/initial_lab_state.yaml",
    api_domain: dict[str, Any] | None = None,
    lab_state: dict[str, Any] | None = None,
    safety_rules: list[dict[str, Any]] | None = None,
    simulated_final_state_before_repair: dict[str, Any] | None = None,
) -> dict[str, Any]:
    domain_payload = api_domain if api_domain is not None else _load_yaml_mapping(api_domain_path)
    lab_state_payload = lab_state if lab_state is not None else _load_yaml_mapping(lab_state_path)
    available_apis = _build_available_apis_from_domain(domain_payload)

    payload = LLMRepairInput(
        protocol_text=protocol_text,
        parsed_steps=parsed_steps,
        workflow_before_llm_repair=[call.model_dump() for call in workflow_before_llm_repair.api_calls],
        validation_issues_before_llm=validation_issues_before_llm,
        applied_rule_repairs=applied_rule_repairs,
        remaining_issues_after_rule_repair=remaining_issues_after_rule_repair,
        available_apis=available_apis,
        api_domain=domain_payload,
        safety_rules=safety_rules or [],
        lab_state_initial=lab_state_payload,
        simulated_final_state_before_repair=simulated_final_state_before_repair or {},
        task=(
            "Repair the workflow with a minimal patch so validator simulation passes. "
            "Do not rewrite the whole workflow. Use operations only: insert_before, insert_after, "
            "replace_call, delete_call. Use only APIs from available_apis/api_domain.actions. "
            "Respect declared units, object-specific numeric limits, and state predicates such as "
            "tip_clean/tip_used and container_clean/container_used. Obey every safety rule in safety_rules."
        ),
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
    cleaned = _strip_code_fence(raw_output)
    candidate = _extract_json_value(cleaned) or cleaned
    try:
        loaded = json.loads(candidate)
    except Exception:
        return None, "invalid_json"
    normalized = _normalize_repair_output_shape(loaded)
    if normalized is None:
        return None, "invalid_output_schema"
    try:
        parsed = LLMRepairOutput.model_validate(normalized)
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

        if op.op not in {"insert_before", "insert_after", "replace_call", "delete_call"}:
            return False, "operation_not_supported"

        op_key = (op.op, op.target_call_id)
        if op_key in used_targets and op.op in {"replace_call", "delete_call"}:
            return False, "operation_conflict"
        used_targets.add(op_key)
        if op.op in {"insert_before", "insert_after", "replace_call"} and op.new_call and op.new_call.call_id:
            call_ids.add(op.new_call.call_id)

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
    insert_after_offsets: dict[tuple[str, str], int] = {}

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
            new_call = _api_call_from_repair_new_call(op.new_call)
            calls.insert(index, new_call)
        elif op.op == "insert_after":
            new_call = _api_call_from_repair_new_call(op.new_call)
            offset_key = (op.op, op.target_call_id)
            offset = insert_after_offsets.get(offset_key, 0)
            calls.insert(index + 1 + offset, new_call)
            insert_after_offsets[offset_key] = offset + 1
        elif op.op == "replace_call":
            new_call = ApiCall(
                call_id=calls[index].call_id,
                api=op.new_call.api,
                args=op.new_call.args,
                source_step_id=calls[index].source_step_id,
            )
            calls[index] = new_call
        elif op.op == "delete_call":
            removed = calls.pop(index)
            applied_operations.append(
                {
                    "op": op.op,
                    "target_call_id": op.target_call_id,
                    "deleted_call": removed.model_dump(),
                    "new_call": None,
                }
            )
            continue
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


def _build_available_apis_from_domain(api_domain: dict[str, Any]) -> list[APIConstraint]:
    actions = api_domain.get("actions", {})
    if not isinstance(actions, dict):
        return []
    out: list[APIConstraint] = []
    for name, spec in sorted(actions.items()):
        if not isinstance(name, str) or not isinstance(spec, dict):
            continue
        params = spec.get("parameters", {})
        required_args: list[str] = []
        arg_types: dict[str, Any] = {}
        if isinstance(params, dict):
            for key, value in params.items():
                required_args.append(str(key))
                arg_types[str(key)] = value
        out.append(
            APIConstraint(
                name=name,
                required_args=required_args,
                arg_types=arg_types,
                preconditions=spec.get("preconditions", {}) if isinstance(spec.get("preconditions", {}), dict) else {},
                effects=spec.get("effects", {}) if isinstance(spec.get("effects", {}), dict) else {},
            )
        )
    return out


def _api_call_from_repair_new_call(new_call: RepairNewCall) -> ApiCall:
    return ApiCall(
        call_id=new_call.call_id or "",
        api=new_call.api,
        args=new_call.args,
        source_step_id=new_call.source_step_id,
    )


def _load_api_registry(path: str) -> dict[str, dict[str, Any]]:
    loaded = _load_yaml_mapping(path)
    if isinstance(loaded.get("actions"), dict):
        return _load_registry_from_domain(loaded)

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


def _load_registry_from_domain(api_domain: dict[str, Any]) -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    actions = api_domain.get("actions", {})
    if not isinstance(actions, dict):
        return registry
    for name, spec in actions.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            continue
        params = spec.get("parameters", {})
        required_args: list[str] = []
        arg_types: dict[str, Any] = {}
        if isinstance(params, dict):
            for key, value in params.items():
                required_args.append(str(key))
                arg_types[str(key)] = value
        registry[name] = {
            "description": str(spec.get("description", "")),
            "required_args": required_args,
            "arg_types": arg_types,
        }
    return registry


def _load_yaml_mapping(path: str) -> dict[str, Any]:
    loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


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
        "max_repair_rounds": 1,
        "timeout_sec": 30,
        "endpoint": "https://api.deepseek.com/chat/completions",
        "mode": "operations",
        "only_when_unresolved": True,
        "save_debug_files": True,
        "require_json_output": True,
        "require_api_registry_constraint": True,
        "allow_full_workflow_rewrite": False,
        "allowed_issue_types": [
            "UnknownAPI",
            "SafetyRuleBindingError",
            "safety_violation",
            "safety_rule_violation",
            "PreconditionViolation",
            "MissingParameter",
            "ParameterTypeError",
            "ParameterRangeError",
            "ParameterUnitError",
        ],
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
                "Allowed operations are insert_before, insert_after, replace_call, delete_call. "
                "Return an object with decision, reasoning_summary, operations, and assumptions. "
                "Each operation must use op, target_call_id, and at most one new_call. "
                "For delete_call, omit new_call or set it to null. Output must be machine-readable JSON."
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


def _strip_code_fence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if len(lines) >= 2:
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            return "\n".join(lines).strip()
    return s


def _extract_json_value(text: str) -> str | None:
    object_start = text.find("{")
    array_start = text.find("[")
    starts = [idx for idx in (object_start, array_start) if idx != -1]
    if not starts:
        return None
    start = min(starts)
    open_char = text[start]
    close_char = "}" if open_char == "{" else "]"
    end = text.rfind(close_char)
    if end == -1 or end <= start:
        return None
    return text[start : end + 1]


def _normalize_repair_output_shape(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list):
        return {
            "decision": "repair",
            "reasoning_summary": "normalized_direct_operations_list",
            "operations": _normalize_repair_operations(payload),
            "assumptions": [],
        }

    if not isinstance(payload, dict):
        return None

    if "operations" in payload:
        out = dict(payload)
        out.setdefault("decision", "repair")
        out.setdefault("reasoning_summary", "normalized_wrapper_output")
        out.setdefault("assumptions", [])
        operations = out.get("operations")
        out["operations"] = _normalize_repair_operations(operations if isinstance(operations, list) else [])
        return out

    for key in ("result", "output", "data", "repair"):
        nested = payload.get(key)
        if isinstance(nested, (dict, list)):
            normalized = _normalize_repair_output_shape(nested)
            if normalized is not None:
                return normalized

    if _looks_like_repair_operation(payload):
        return {
            "decision": "repair",
            "reasoning_summary": "normalized_single_operation",
            "operations": _normalize_repair_operations([payload]),
            "assumptions": [],
        }
    return None


def _normalize_repair_operations(raw_operations: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in raw_operations:
        if not isinstance(item, dict):
            continue
        op_name = item.get("op", item.get("operation"))
        target_call_id = item.get("target_call_id", item.get("reference_call_id"))
        if not isinstance(op_name, str) or not isinstance(target_call_id, str):
            continue

        new_calls = item.get("new_calls")
        if isinstance(new_calls, list):
            for new_call in new_calls:
                normalized.append(
                    {
                        "op": op_name,
                        "target_call_id": target_call_id,
                        "new_call": _normalize_new_call(new_call),
                    }
                )
            continue

        out = {
            "op": op_name,
            "target_call_id": target_call_id,
        }
        if "new_call" in item:
            out["new_call"] = _normalize_new_call(item.get("new_call"))
        elif op_name != "delete_call":
            out["new_call"] = None
        normalized.append(out)
    return _add_cleanup_deletes_for_replaced_tip_transfers(normalized)


def _normalize_new_call(raw_call: Any) -> dict[str, Any] | None:
    if not isinstance(raw_call, dict):
        return None
    api = raw_call.get("api")
    args = raw_call.get("args", {})
    if not isinstance(api, str):
        return None
    return {
        **({"call_id": raw_call["call_id"]} if isinstance(raw_call.get("call_id"), str) else {}),
        "api": api,
        "args": args if isinstance(args, dict) else {},
        **({"source_step_id": raw_call["source_step_id"]} if isinstance(raw_call.get("source_step_id"), str) else {}),
    }


def _looks_like_repair_operation(payload: dict[str, Any]) -> bool:
    return (
        ("op" in payload or "operation" in payload)
        and ("target_call_id" in payload or "reference_call_id" in payload)
    )


def _add_cleanup_deletes_for_replaced_tip_transfers(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = list(operations)
    existing_deletes = {
        op.get("target_call_id")
        for op in out
        if op.get("op") == "delete_call" and isinstance(op.get("target_call_id"), str)
    }
    for op in operations:
        if op.get("op") != "replace_call":
            continue
        new_call = op.get("new_call")
        if not isinstance(new_call, dict) or new_call.get("api") != "transfer":
            continue
        target_call_id = op.get("target_call_id")
        if not isinstance(target_call_id, str):
            continue
        next_call_id = _next_numeric_call_id(target_call_id)
        if next_call_id and next_call_id not in existing_deletes:
            out.append({"op": "delete_call", "target_call_id": next_call_id, "new_call": None})
            existing_deletes.add(next_call_id)
        args = new_call.get("args", {})
        if isinstance(args, dict):
            tip = args.get("tip")
            pipette = args.get("pipette")
            if isinstance(tip, str) and isinstance(pipette, str) and not _has_detach_after_transfer(out, target_call_id, tip):
                out.append(
                    {
                        "op": "insert_after",
                        "target_call_id": target_call_id,
                        "new_call": {
                            "api": "detach_tip",
                            "args": {
                                "pipette": pipette,
                                "tip": tip,
                            },
                        },
                    }
                )
    return out


def _next_numeric_call_id(call_id: str) -> str | None:
    prefix = call_id.rstrip("0123456789")
    suffix = call_id[len(prefix):]
    if not suffix:
        return None
    return f"{prefix}{int(suffix) + 1}"


def _has_detach_after_transfer(operations: list[dict[str, Any]], target_call_id: str, tip: str) -> bool:
    for op in operations:
        if op.get("op") != "insert_after" or op.get("target_call_id") != target_call_id:
            continue
        new_call = op.get("new_call")
        if not isinstance(new_call, dict) or new_call.get("api") != "detach_tip":
            continue
        args = new_call.get("args", {})
        if isinstance(args, dict) and args.get("tip") == tip:
            return True
    return False
