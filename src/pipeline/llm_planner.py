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
from src.pipeline.workflow_planner import compose_workflow


class APISpec(BaseModel):
    name: str
    parameters: dict[str, str] = Field(default_factory=dict)


class LLMPlannerInput(BaseModel):
    protocol_id: str
    operations: list[dict[str, Any]]
    operation_api_groups: list[dict[str, Any]]
    available_apis: list[APISpec]
    lab_state_initial: dict[str, Any] = Field(default_factory=dict)
    lab_state_expected: dict[str, Any] = Field(default_factory=dict)
    planner_task_instruction: str
    notice: str = ""


class LLMPlannerOutput(BaseModel):
    decision: str
    reasoning_summary: str
    workflow: dict[str, Any]
    merge_summary: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def load_llm_planner_config(path: str = "configs/llm_planner_config.yaml") -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return _default_config()
    loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or not isinstance(loaded.get("llm_planner"), dict):
        return _default_config()
    merged = _default_config()
    merged.update(loaded["llm_planner"])
    return merged


def build_llm_planner_input(
    protocol_id: str,
    operations: list[dict[str, Any]],
    operation_api_groups: list[dict[str, Any]],
    api_registry_path: str = "configs/api_registry.yaml",
    initial_lab_state_path: str = "configs/initial_lab_state.yaml",
    expected_lab_state_path: str = "configs/initial_lab_state.yaml",
    notice_path: str = "configs/llm_planner_notice.txt",
) -> dict[str, Any]:
    payload = LLMPlannerInput(
        protocol_id=protocol_id,
        operations=operations,
        operation_api_groups=operation_api_groups,
        available_apis=_load_available_apis(api_registry_path),
        lab_state_initial=_load_lab_state(initial_lab_state_path),
        lab_state_expected=_load_lab_state(expected_lab_state_path),
        planner_task_instruction=(
            "You are a workflow planner for lab-robot execution. "
            "Merge operation-level API groups into one executable workflow. "
            "You must remove or merge redundant temporal actions when they can be done together. "
            "Example: fridge.open -> pick tube -> fridge.close + fridge.open -> pick buffer -> fridge.close "
            "should be optimized into fridge.open -> pick tube -> pick buffer -> fridge.close when safe. "
            "You must infer and add hidden but necessary actions so API calls are continuous and executable for a robot. "
            "Respect operation context, API preconditions, lab_state_initial, and lab_state_expected. "
            "Output one final workflow.api_calls list (not grouped by operation)."
        ),
        notice=_load_notice_text(notice_path),
    )
    return payload.model_dump()


def invoke_llm_planner(llm_input: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    provider = str(config.get("provider", "deepseek")).lower()
    model = str(config.get("model", "deepseek-chat"))
    temperature = float(config.get("temperature", 0.0))
    max_retries = int(config.get("max_retries", 2))
    timeout_seconds = int(config.get("timeout_seconds", 60))

    if provider != "deepseek":
        return _invocation_result(provider=provider, model=model, failure_reason="unsupported_provider")

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return _invocation_result(provider=provider, model=model, failure_reason="missing_deepseek_api_key")

    endpoint = str(config.get("endpoint", "https://api.deepseek.com/chat/completions"))
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": _build_llm_planner_messages(llm_input),
    }
    last_error = "provider_request_failed"
    for _ in range(max_retries + 1):
        try:
            response_json = _http_post_json(
                endpoint=endpoint,
                payload=payload,
                api_key=api_key,
                timeout_sec=timeout_seconds,
            )
            content = _extract_content(response_json)
            if not isinstance(content, str) or not content.strip():
                last_error = "empty_model_output"
                continue

            parsed, parse_error = parse_llm_planner_output(content)
            if parse_error is None and parsed is not None:
                return {
                    "llm_planner_invoked": True,
                    "llm_planner_valid_json": True,
                    "raw_output": content,
                    "parsed_output": parsed.model_dump(),
                    "failure_reason": None,
                    "provider": provider,
                    "model": model,
                }
            return {
                "llm_planner_invoked": True,
                "llm_planner_valid_json": False,
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
    return _invocation_result(provider=provider, model=model, failure_reason=last_error)


def parse_llm_planner_output(raw_output: str) -> tuple[LLMPlannerOutput | None, str | None]:
    cleaned = _strip_code_fence(raw_output)
    candidate = _extract_json_object(cleaned) or cleaned
    try:
        loaded = json.loads(candidate)
    except Exception:
        return None, "invalid_json"
    normalized = _normalize_planner_shape(loaded)
    if normalized is None:
        return None, "invalid_output_schema"
    try:
        parsed = LLMPlannerOutput.model_validate(normalized)
    except ValidationError:
        return None, "invalid_output_schema"
    return parsed, None


def normalize_planner_output(parsed_output: LLMPlannerOutput) -> dict[str, Any]:
    workflow = parsed_output.workflow if isinstance(parsed_output.workflow, dict) else {}
    calls = workflow.get("api_calls", [])
    if not isinstance(calls, list):
        calls = []
    normalized_calls: list[dict[str, Any]] = []
    for idx, call in enumerate(calls, start=1):
        if not isinstance(call, dict):
            continue
        item = dict(call)
        item["call_id"] = str(item.get("call_id", f"c{idx}"))
        item["api"] = str(item.get("api", ""))
        if not isinstance(item.get("args"), dict):
            item["args"] = {}
        if "source_step_id" in item and item["source_step_id"] is not None:
            item["source_step_id"] = str(item["source_step_id"])
        else:
            item["source_step_id"] = None
        normalized_calls.append(item)
    return {
        "decision": parsed_output.decision,
        "reasoning_summary": parsed_output.reasoning_summary,
        "workflow": {"api_calls": normalized_calls},
        "merge_summary": parsed_output.merge_summary,
        "assumptions": parsed_output.assumptions,
    }


def validate_planner_output(
    normalized_output: dict[str, Any],
    api_registry_path: str = "configs/api_registry.yaml",
) -> dict[str, Any]:
    workflow = normalized_output.get("workflow", {})
    api_calls = workflow.get("api_calls", []) if isinstance(workflow, dict) else []
    if not isinstance(api_calls, list):
        return {
            "planner_valid": False,
            "failure_reason": "invalid_workflow_schema",
            "contains_unregistered_api": False,
            "unregistered_apis": [],
            "issues": ["workflow.api_calls must be a list"],
        }

    issues: list[str] = []
    for idx, call in enumerate(api_calls):
        if not isinstance(call, dict):
            issues.append(f"api_calls[{idx}] must be object")
            continue
        api = call.get("api")
        args = call.get("args")
        if not isinstance(api, str) or not api.strip():
            issues.append(f"api_calls[{idx}].api must be non-empty string")
        if not isinstance(args, dict):
            issues.append(f"api_calls[{idx}].args must be object")

    if issues:
        return {
            "planner_valid": False,
            "failure_reason": "invalid_workflow_schema",
            "contains_unregistered_api": False,
            "unregistered_apis": [],
            "issues": issues,
        }

    registered = {api.name for api in _load_available_apis(api_registry_path)}
    unregistered = sorted(
        {
            str(call.get("api"))
            for call in api_calls
            if isinstance(call, dict) and isinstance(call.get("api"), str) and call.get("api") not in registered
        }
    )
    return {
        "planner_valid": True,
        "failure_reason": None,
        "contains_unregistered_api": bool(unregistered),
        "unregistered_apis": unregistered,
        "issues": [],
    }


def run_planner_backend(
    protocol_id: str,
    operations: list[dict[str, Any]],
    operation_api_groups: list[dict[str, Any]],
    enable_llm_planner: bool,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or load_llm_planner_config()
    if enable_llm_planner:
        cfg["enabled"] = True

    fallback = build_fallback_workflow(protocol_id=protocol_id, operation_api_groups=operation_api_groups)
    planner_result: dict[str, Any] = {
        "planner_backend_mode": "fallback_rule_planner",
        "llm_planner_invoked": False,
        "llm_planner_valid_json": False,
        "llm_planner_schema_valid": False,
        "llm_planner_accepted": False,
        "llm_planner_fallback_used": True,
        "planner_valid": True,
        "contains_unregistered_api": False,
        "unregistered_apis": [],
        "planner_failure_reason": "llm_planner_disabled",
    }
    llm_input_payload: dict[str, Any] | None = None
    llm_raw_output_payload: dict[str, Any] | None = None
    llm_parsed_output_payload: dict[str, Any] | None = None
    validation_result: dict[str, Any] = {
        "planner_valid": True,
        "failure_reason": None,
        "contains_unregistered_api": False,
        "unregistered_apis": [],
        "issues": [],
    }

    if not enable_llm_planner:
        return {
            "workflow": fallback,
            "planner_result": planner_result,
            "planner_validation_result": validation_result,
            "llm_planner_input": None,
            "llm_planner_raw_output": None,
            "llm_planner_parsed_output": None,
        }

    planner_result["planner_backend_mode"] = "llm_primary"
    planner_result["llm_planner_invoked"] = True
    llm_input_payload = build_llm_planner_input(
        protocol_id=protocol_id,
        operations=operations,
        operation_api_groups=operation_api_groups,
        api_registry_path=str(cfg.get("api_registry_path", "configs/api_registry.yaml")),
        initial_lab_state_path=str(cfg.get("initial_lab_state_path", "configs/initial_lab_state.yaml")),
        expected_lab_state_path=str(cfg.get("expected_lab_state_path", "configs/initial_lab_state.yaml")),
        notice_path=str(cfg.get("notice_path", "configs/llm_planner_notice.txt")),
    )
    invocation = invoke_llm_planner(llm_input_payload, cfg)
    llm_raw_output_payload = {
        "provider": invocation.get("provider"),
        "model": invocation.get("model"),
        "raw_output": invocation.get("raw_output", ""),
        "failure_reason": invocation.get("failure_reason"),
    }

    parsed_output: LLMPlannerOutput | None = None
    if invocation.get("parsed_output") is not None:
        try:
            parsed_output = LLMPlannerOutput.model_validate(invocation["parsed_output"])
            planner_result["llm_planner_valid_json"] = True
            planner_result["llm_planner_schema_valid"] = True
            llm_parsed_output_payload = parsed_output.model_dump()
        except ValidationError:
            planner_result["planner_failure_reason"] = "invalid_output_schema"
    else:
        raw_output = invocation.get("raw_output", "")
        if isinstance(raw_output, str) and raw_output.strip():
            parsed_output, parse_error = parse_llm_planner_output(raw_output)
            if parse_error is None and parsed_output is not None:
                planner_result["llm_planner_valid_json"] = True
                planner_result["llm_planner_schema_valid"] = True
                llm_parsed_output_payload = parsed_output.model_dump()
            else:
                planner_result["planner_failure_reason"] = parse_error
        else:
            planner_result["planner_failure_reason"] = invocation.get("failure_reason")

    if parsed_output is None:
        return {
            "workflow": fallback,
            "planner_result": planner_result,
            "planner_validation_result": validation_result,
            "llm_planner_input": llm_input_payload,
            "llm_planner_raw_output": llm_raw_output_payload,
            "llm_planner_parsed_output": llm_parsed_output_payload,
        }

    normalized = normalize_planner_output(parsed_output)
    validation_result = validate_planner_output(
        normalized_output=normalized,
        api_registry_path=str(cfg.get("api_registry_path", "configs/api_registry.yaml")),
    )
    planner_result["planner_valid"] = bool(validation_result["planner_valid"])
    planner_result["contains_unregistered_api"] = bool(validation_result["contains_unregistered_api"])
    planner_result["unregistered_apis"] = list(validation_result["unregistered_apis"])
    planner_result["planner_failure_reason"] = validation_result["failure_reason"]

    if not validation_result["planner_valid"]:
        planner_result["llm_planner_fallback_used"] = True
        planner_result["llm_planner_accepted"] = False
        return {
            "workflow": fallback,
            "planner_result": planner_result,
            "planner_validation_result": validation_result,
            "llm_planner_input": llm_input_payload,
            "llm_planner_raw_output": llm_raw_output_payload,
            "llm_planner_parsed_output": llm_parsed_output_payload,
        }

    workflow = _workflow_from_api_calls(protocol_id=protocol_id, api_calls=normalized["workflow"]["api_calls"])
    workflow = compose_workflow(workflow)
    planner_result["llm_planner_fallback_used"] = False
    planner_result["llm_planner_accepted"] = True
    planner_result["planner_failure_reason"] = None
    return {
        "workflow": workflow,
        "planner_result": planner_result,
        "planner_validation_result": validation_result,
        "llm_planner_input": llm_input_payload,
        "llm_planner_raw_output": llm_raw_output_payload,
        "llm_planner_parsed_output": llm_parsed_output_payload,
    }


def build_fallback_workflow(protocol_id: str, operation_api_groups: list[dict[str, Any]]) -> Workflow:
    flattened: list[ApiCall] = []
    for group in operation_api_groups:
        operation_id = str(group.get("operation_id", "op_unknown"))
        for idx, item in enumerate(group.get("api_calls", []), start=1):
            if not isinstance(item, dict):
                continue
            source_step_id = item.get("source_step_id")
            if not isinstance(source_step_id, str) or not source_step_id.strip():
                source_step_id = f"{operation_id}_s{idx}"
            flattened.append(
                ApiCall(
                    call_id="",
                    api=str(item.get("api", "")),
                    args=item.get("args", {}) if isinstance(item.get("args"), dict) else {},
                    source_step_id=source_step_id,
                )
            )

    merged = _merge_adjacent_fridge_sessions(flattened)
    merged = _remove_adjacent_exact_duplicates(merged)
    workflow = Workflow(
        workflow_id=f"wf_{protocol_id}",
        protocol_id=protocol_id,
        api_calls=_renumber_calls(merged),
    )
    return compose_workflow(workflow)


def _merge_adjacent_fridge_sessions(calls: list[ApiCall]) -> list[ApiCall]:
    out: list[ApiCall] = []
    i = 0
    while i < len(calls):
        current = calls[i]
        if current.api == "fridge.close" and i + 1 < len(calls) and calls[i + 1].api == "fridge.open":
            i += 2
            continue
        out.append(current)
        i += 1
    return out


def _remove_adjacent_exact_duplicates(calls: list[ApiCall]) -> list[ApiCall]:
    out: list[ApiCall] = []
    for call in calls:
        if out and out[-1].api == call.api and out[-1].args == call.args:
            continue
        out.append(call)
    return out


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


def _normalize_planner_shape(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if "workflow" in payload:
        out = dict(payload)
        out.setdefault("decision", "accept")
        out.setdefault("reasoning_summary", "normalized_wrapper_output")
        out.setdefault("merge_summary", [])
        out.setdefault("assumptions", [])
        return out
    if "api_calls" in payload:
        return {
            "decision": "accept",
            "reasoning_summary": "normalized_direct_workflow_output",
            "workflow": {"api_calls": payload.get("api_calls", [])},
            "merge_summary": payload.get("merge_summary", []),
            "assumptions": payload.get("assumptions", []),
        }
    for key in ("result", "output", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            normalized = _normalize_planner_shape(nested)
            if normalized is not None:
                return normalized
    return None


def _build_llm_planner_messages(llm_input: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a lab workflow planner that merges operation-level API groups into one executable API list. "
                "Return strict JSON only, no markdown and no code fences."
            ),
        },
        {
            "role": "system",
            "content": (
                "Output schema: "
                "{\"decision\":\"...\",\"reasoning_summary\":\"...\","
                "\"workflow\":{\"api_calls\":[{\"call_id\":\"c1\",\"api\":\"...\",\"args\":{},\"source_step_id\":\"...\"}]},"
                "\"merge_summary\":[],\"assumptions\":[]} . "
                "Hard requirements: "
                "1) Remove or merge temporally redundant open/close-type operations when safe. "
                "2) Preserve operation intent while adding hidden prerequisite actions for continuity and executability. "
                "3) Ensure each API call uses available_apis and valid arguments. "
                "4) Ensure final sequence is physically executable and consistent with lab_state_initial and lab_state_expected. "
                "5) Output one final flat api_calls list, not grouped."
            ),
        },
        {"role": "user", "content": json.dumps(llm_input, ensure_ascii=False)},
    ]


def _invocation_result(provider: str, model: str, failure_reason: str) -> dict[str, Any]:
    return {
        "llm_planner_invoked": True,
        "llm_planner_valid_json": False,
        "raw_output": "",
        "parsed_output": None,
        "failure_reason": failure_reason,
        "provider": provider,
        "model": model,
    }


def _http_post_json(endpoint: str, payload: dict[str, Any], api_key: str, timeout_sec: int) -> dict[str, Any]:
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


def _extract_content(response_json: dict[str, Any]) -> str:
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


def _load_available_apis(api_registry_path: str) -> list[APISpec]:
    payload = Path(api_registry_path).read_text(encoding="utf-8")
    loaded = yaml.safe_load(payload)
    apis = loaded.get("apis", []) if isinstance(loaded, dict) else []
    out: list[APISpec] = []
    for item in apis:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        params = item.get("parameters", {})
        param_types: dict[str, str] = {}
        if isinstance(params, dict):
            for key, value in params.items():
                param_types[str(key)] = str(value)
        out.append(APISpec(name=name, parameters=param_types))
    return out


def _load_lab_state(path: str) -> dict[str, Any]:
    state_path = Path(path)
    if not state_path.exists():
        return {}
    loaded = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _workflow_from_api_calls(protocol_id: str, api_calls: list[dict[str, Any]]) -> Workflow:
    calls: list[ApiCall] = []
    for idx, call in enumerate(api_calls, start=1):
        calls.append(
            ApiCall(
                call_id=str(call.get("call_id", f"c{idx}")),
                api=str(call.get("api", "")),
                args=call.get("args", {}) if isinstance(call.get("args", {}), dict) else {},
                source_step_id=call.get("source_step_id"),
            )
        )
    return Workflow(workflow_id=f"wf_{protocol_id}", protocol_id=protocol_id, api_calls=calls)


def _default_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "provider": "deepseek",
        "model": "deepseek-chat",
        "temperature": 0.0,
        "max_retries": 2,
        "timeout_seconds": 60,
        "endpoint": "https://api.deepseek.com/chat/completions",
        "api_registry_path": "configs/api_registry.yaml",
        "initial_lab_state_path": "configs/initial_lab_state.yaml",
        "expected_lab_state_path": "configs/initial_lab_state.yaml",
        "notice_path": "configs/llm_planner_notice.txt",
        "save_debug_files": True,
        "require_json_output": True,
        "require_schema_validation": True,
    }


def _load_notice_text(path: str) -> str:
    notice_path = Path(path)
    if not notice_path.exists():
        return ""
    return notice_path.read_text(encoding="utf-8")


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


def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]
