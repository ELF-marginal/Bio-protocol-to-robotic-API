from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from src.models.contracts import ApiCall, ParsedProtocol, Workflow
from src.pipeline.mock_grounding import ground_to_workflow


class APISpec(BaseModel):
    name: str
    parameters: dict[str, str] = Field(default_factory=dict)


class LLMGroundingInput(BaseModel):
    protocol_id: str
    parsed_protocol: dict[str, Any]
    available_apis: list[APISpec]
    lab_state_initial: dict[str, Any] = Field(default_factory=dict)
    lab_state_expected: dict[str, Any] = Field(default_factory=dict)
    grounding_task_instruction: str
    notice: str = ""


class LLMGroundingOutput(BaseModel):
    decision: str
    reasoning_summary: str
    workflow: dict[str, Any]
    contains_unregistered_api: bool = False
    unregistered_apis: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def load_llm_grounding_config(path: str = "configs/llm_grounding_config.yaml") -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return _default_config()
    loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or not isinstance(loaded.get("llm_grounding"), dict):
        return _default_config()
    merged = _default_config()
    merged.update(loaded["llm_grounding"])
    return merged


def build_llm_grounding_input(
    parsed_protocol: ParsedProtocol,
    api_registry_path: str = "configs/api_registry.yaml",
    initial_lab_state_path: str = "configs/initial_lab_state.yaml",
    expected_lab_state_path: str = "configs/initial_lab_state.yaml",
    notice_path: str = "configs/llm_grounder_notice.txt",
) -> dict[str, Any]:
    payload = LLMGroundingInput(
        protocol_id=parsed_protocol.protocol_id,
        parsed_protocol=parsed_protocol.model_dump(),
        available_apis=_load_available_apis(api_registry_path),
        lab_state_initial=_load_lab_state(initial_lab_state_path),
        lab_state_expected=_load_lab_state(expected_lab_state_path),
        grounding_task_instruction=(
            "Convert parsed protocol steps to API workflow calls. "
            "Prefer registered APIs. Respect initial/expected lab-state constraints when planning calls. "
            "If any API in workflow is not in available_apis, you must set contains_unregistered_api=true "
            "and include each distinct unknown API name in unregistered_apis. "
            "If all APIs are registered, you must set contains_unregistered_api=false and unregistered_apis=[]."
        ),
        notice=_load_notice_text(notice_path),
    )
    return payload.model_dump()


def invoke_llm_grounding(
    llm_input: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    provider = str(config.get("provider", "deepseek")).lower()
    model = str(config.get("model", "deepseek-chat"))
    temperature = float(config.get("temperature", 0.0))
    max_retries = int(config.get("max_retries", 2))
    timeout_seconds = int(config.get("timeout_seconds", 60))

    if provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            return _invocation_result(provider, model, "missing_deepseek_api_key")
        endpoint = str(config.get("endpoint", "https://api.deepseek.com/chat/completions"))
        payload = {
            "model": model,
            "temperature": temperature,
            "messages": _build_llm_grounding_messages(llm_input),
        }
        last_error = "provider_request_failed"
        for _ in range(max_retries + 1):
            try:
                response_json = _http_post_json(endpoint, payload, api_key, timeout_seconds)
                content = _extract_content(response_json)
                if not isinstance(content, str) or not content.strip():
                    last_error = "empty_model_output"
                    continue
                parsed, parse_error = parse_llm_grounding_output(content)
                if parse_error is None and parsed is not None:
                    return {
                        "llm_grounding_invoked": True,
                        "llm_grounding_valid_json": True,
                        "raw_output": content,
                        "parsed_output": parsed.model_dump(),
                        "failure_reason": None,
                        "provider": provider,
                        "model": model,
                    }
                return {
                    "llm_grounding_invoked": True,
                    "llm_grounding_valid_json": False,
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
        return _invocation_result(provider, model, last_error)

    return _invocation_result(provider, model, "unsupported_provider")


def parse_llm_grounding_output(raw_output: str) -> tuple[LLMGroundingOutput | None, str | None]:
    cleaned = _strip_code_fence(raw_output)
    candidate = _extract_json_object(cleaned) or cleaned
    try:
        loaded = json.loads(candidate)
    except Exception:
        return None, "invalid_json"
    normalized = _normalize_grounding_shape(loaded)
    if normalized is None:
        return None, "invalid_output_schema"
    try:
        parsed = LLMGroundingOutput.model_validate(normalized)
    except ValidationError:
        return None, "invalid_output_schema"
    return parsed, None


def normalize_grounding_output(parsed_output: LLMGroundingOutput) -> dict[str, Any]:
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
    unregistered_apis = [str(api) for api in parsed_output.unregistered_apis]
    contains_unregistered_api = bool(parsed_output.contains_unregistered_api or unregistered_apis)
    return {
        "decision": parsed_output.decision,
        "reasoning_summary": parsed_output.reasoning_summary,
        "workflow": {"api_calls": normalized_calls},
        "contains_unregistered_api": contains_unregistered_api,
        "unregistered_apis": unregistered_apis,
        "assumptions": parsed_output.assumptions,
    }


def validate_grounding_output(
    normalized_output: dict[str, Any],
    api_registry_path: str = "configs/api_registry.yaml",
) -> dict[str, Any]:
    workflow = normalized_output.get("workflow", {})
    api_calls = workflow.get("api_calls", []) if isinstance(workflow, dict) else []
    if not isinstance(api_calls, list):
        return {
            "grounding_valid": False,
            "failure_reason": "invalid_workflow_schema",
            "contains_unregistered_api": False,
            "unregistered_apis": [],
            "model_contains_unregistered_api": bool(normalized_output.get("contains_unregistered_api", False)),
            "model_unregistered_apis": normalized_output.get("unregistered_apis", []),
            "scanner_contains_unregistered_api": False,
            "scanner_unregistered_apis": [],
            "model_scanner_consistent": False,
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
            "grounding_valid": False,
            "failure_reason": "invalid_workflow_schema",
            "contains_unregistered_api": False,
            "unregistered_apis": [],
            "model_contains_unregistered_api": bool(normalized_output.get("contains_unregistered_api", False)),
            "model_unregistered_apis": normalized_output.get("unregistered_apis", []),
            "scanner_contains_unregistered_api": False,
            "scanner_unregistered_apis": [],
            "model_scanner_consistent": False,
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

    scanner_contains_unregistered_api = bool(unregistered)
    declared_unregistered = normalized_output.get("contains_unregistered_api", False)
    declared_list = normalized_output.get("unregistered_apis", [])
    if not isinstance(declared_list, list):
        declared_list = []
    model_unregistered = sorted(str(x) for x in declared_list)

    # Final safety decision uses union of model declaration and scanner result.
    contains_unregistered_api = bool(declared_unregistered) or scanner_contains_unregistered_api
    final_unregistered = sorted(set(model_unregistered) | set(unregistered))

    # Trust scanner over model self-report; record mismatch as issue.
    consistent = True
    if bool(declared_unregistered) != scanner_contains_unregistered_api:
        issues.append("contains_unregistered_api_mismatch_with_scanner")
        consistent = False
    if model_unregistered != unregistered:
        issues.append("unregistered_apis_mismatch_with_scanner")
        consistent = False

    return {
        "grounding_valid": not contains_unregistered_api,
        "failure_reason": "unregistered_api_detected" if contains_unregistered_api else None,
        "contains_unregistered_api": contains_unregistered_api,
        "unregistered_apis": final_unregistered,
        "model_contains_unregistered_api": bool(declared_unregistered),
        "model_unregistered_apis": model_unregistered,
        "scanner_contains_unregistered_api": scanner_contains_unregistered_api,
        "scanner_unregistered_apis": unregistered,
        "model_scanner_consistent": consistent,
        "issues": issues,
    }


def run_grounding_backend(
    parsed_protocol: ParsedProtocol,
    enable_llm_grounding: bool,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or load_llm_grounding_config()
    if enable_llm_grounding:
        cfg["enabled"] = True

    grounding_result: dict[str, Any] = {
        "grounding_backend_mode": "rule_only",
        "llm_grounding_invoked": False,
        "llm_grounding_valid_json": False,
        "llm_grounding_schema_valid": False,
        "llm_grounding_accepted": False,
        "llm_grounding_fallback_used": False,
        "grounding_valid": True,
        "contains_unregistered_api": False,
        "unregistered_apis": [],
        "model_contains_unregistered_api": False,
        "model_unregistered_apis": [],
        "scanner_contains_unregistered_api": False,
        "scanner_unregistered_apis": [],
        "model_scanner_consistent": True,
        "grounding_failure_reason": None,
        "grounding_issues": [],
    }
    llm_input_payload: dict[str, Any] | None = None
    llm_raw_output_payload: dict[str, Any] | None = None
    llm_parsed_output_payload: dict[str, Any] | None = None

    if not enable_llm_grounding:
        workflow = ground_to_workflow(parsed_protocol)
        grounding_result["grounding_failure_reason"] = "llm_grounding_disabled"
        return {
            "workflow": workflow,
            "grounding_result": grounding_result,
            "llm_grounding_input": None,
            "llm_grounding_raw_output": None,
            "llm_grounding_parsed_output": None,
            "grounding_validation_result": {
                "grounding_valid": True,
                "failure_reason": None,
                "contains_unregistered_api": False,
                "unregistered_apis": [],
                "issues": [],
            },
        }

    grounding_result["grounding_backend_mode"] = "llm_primary"
    grounding_result["llm_grounding_invoked"] = True
    llm_input_payload = build_llm_grounding_input(
        parsed_protocol=parsed_protocol,
        api_registry_path=str(cfg.get("api_registry_path", "configs/api_registry.yaml")),
        initial_lab_state_path=str(cfg.get("initial_lab_state_path", "configs/initial_lab_state.yaml")),
        expected_lab_state_path=str(cfg.get("expected_lab_state_path", "configs/initial_lab_state.yaml")),
        notice_path=str(cfg.get("notice_path", "configs/llm_grounder_notice.txt")),
    )
    invocation = invoke_llm_grounding(llm_input_payload, cfg)
    llm_raw_output_payload = {
        "provider": invocation.get("provider"),
        "model": invocation.get("model"),
        "raw_output": invocation.get("raw_output", ""),
        "failure_reason": invocation.get("failure_reason"),
    }

    parsed_output: LLMGroundingOutput | None = None
    if invocation.get("parsed_output") is not None:
        try:
            parsed_output = LLMGroundingOutput.model_validate(invocation["parsed_output"])
            grounding_result["llm_grounding_valid_json"] = True
            grounding_result["llm_grounding_schema_valid"] = True
            llm_parsed_output_payload = parsed_output.model_dump()
        except ValidationError:
            grounding_result["grounding_failure_reason"] = "invalid_output_schema"
    else:
        raw_output = invocation.get("raw_output", "")
        if isinstance(raw_output, str) and raw_output.strip():
            parsed_output, parse_error = parse_llm_grounding_output(raw_output)
            if parse_error is None and parsed_output is not None:
                grounding_result["llm_grounding_valid_json"] = True
                grounding_result["llm_grounding_schema_valid"] = True
                llm_parsed_output_payload = parsed_output.model_dump()
            else:
                grounding_result["grounding_failure_reason"] = parse_error
        else:
            grounding_result["grounding_failure_reason"] = invocation.get("failure_reason")

    if parsed_output is None:
        fallback_workflow = ground_to_workflow(parsed_protocol)
        grounding_result["llm_grounding_fallback_used"] = True
        return {
            "workflow": fallback_workflow,
            "grounding_result": grounding_result,
            "llm_grounding_input": llm_input_payload,
            "llm_grounding_raw_output": llm_raw_output_payload,
            "llm_grounding_parsed_output": llm_parsed_output_payload,
            "grounding_validation_result": {
                "grounding_valid": True,
                "failure_reason": None,
                "contains_unregistered_api": False,
                "unregistered_apis": [],
                "issues": ["llm_grounding_fallback_to_rule"],
            },
        }

    normalized = normalize_grounding_output(parsed_output)
    validation = validate_grounding_output(normalized_output=normalized)
    grounding_result["grounding_valid"] = bool(validation["grounding_valid"])
    grounding_result["contains_unregistered_api"] = bool(validation["contains_unregistered_api"])
    grounding_result["unregistered_apis"] = list(validation["unregistered_apis"])
    grounding_result["model_contains_unregistered_api"] = bool(validation["model_contains_unregistered_api"])
    grounding_result["model_unregistered_apis"] = list(validation["model_unregistered_apis"])
    grounding_result["scanner_contains_unregistered_api"] = bool(validation["scanner_contains_unregistered_api"])
    grounding_result["scanner_unregistered_apis"] = list(validation["scanner_unregistered_apis"])
    grounding_result["model_scanner_consistent"] = bool(validation["model_scanner_consistent"])
    grounding_result["grounding_failure_reason"] = validation["failure_reason"]
    grounding_result["grounding_issues"] = list(validation["issues"])

    workflow = _workflow_from_api_calls(
        protocol_id=parsed_protocol.protocol_id,
        api_calls=normalized["workflow"]["api_calls"],
    )
    grounding_result["llm_grounding_accepted"] = True
    return {
        "workflow": workflow,
        "grounding_result": grounding_result,
        "llm_grounding_input": llm_input_payload,
        "llm_grounding_raw_output": llm_raw_output_payload,
        "llm_grounding_parsed_output": llm_parsed_output_payload,
        "grounding_validation_result": validation,
    }


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


def _build_llm_grounding_messages(llm_input: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You convert parsed protocol JSON into API workflow JSON. "
                "Return JSON only, no markdown."
            ),
        },
        {
            "role": "system",
            "content": (
                "Output schema: "
                "{\"decision\":\"...\",\"reasoning_summary\":\"...\","
                "\"workflow\":{\"api_calls\":[{\"call_id\":\"c1\",\"api\":\"...\",\"args\":{},\"source_step_id\":\"s1\"}]},"
                "\"contains_unregistered_api\":false,\"unregistered_apis\":[],\"assumptions\":[]} . "
                "Use available_apis first. You MUST consider lab_state_initial and lab_state_expected constraints. "
                "Rules for unregistered API reporting are mandatory: "
                "1) Every api in workflow.api_calls must be checked against available_apis.name. "
                "2) If can not find a suitable api in available_apis, must must must set contains_unregistered_api=true. (weight=99999)"
                "3) Put every distinct unregistered api name into unregistered_apis. "
                "4) If all api calls are registered, set contains_unregistered_api=false and unregistered_apis=[]. "
                "5) contains_unregistered_api must exactly match whether unregistered_apis is empty."
                "6) If the state of the lab after you complete the experiment is not the expected state, please call the appropriate API again to change the lab state."
                "7) The 'expected laboratory state' is, in essence, the state to which one hopes to restore the laboratory upon the completion of an experiment."
                "8) （最重要）如果你需要的一个api在可用API库没有匹配的内容，比如你想要一个离心操作，但是api库里任何离心类的api都没有，这时你编造一个假操作占位， 并且这时一定要contains_unregistered_api=true"
            ), 
        },
        {"role": "user", "content": json.dumps(llm_input, ensure_ascii=False)},
    ]


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


def _normalize_grounding_shape(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if "workflow" in payload:
        out = dict(payload)
        out.setdefault("decision", "accept")
        out.setdefault("reasoning_summary", "normalized_wrapper_output")
        out.setdefault("contains_unregistered_api", False)
        out.setdefault("unregistered_apis", [])
        out.setdefault("assumptions", [])
        return out
    if "api_calls" in payload:
        return {
            "decision": "accept",
            "reasoning_summary": "normalized_direct_workflow_output",
            "workflow": {"api_calls": payload.get("api_calls", [])},
            "contains_unregistered_api": bool(payload.get("contains_unregistered_api", False)),
            "unregistered_apis": payload.get("unregistered_apis", []),
            "assumptions": payload.get("assumptions", []),
        }
    for key in ("result", "output", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            normalized = _normalize_grounding_shape(nested)
            if normalized is not None:
                return normalized
    return None


def _invocation_result(provider: str, model: str, failure_reason: str) -> dict[str, Any]:
    return {
        "llm_grounding_invoked": True,
        "llm_grounding_valid_json": False,
        "raw_output": "",
        "parsed_output": None,
        "failure_reason": failure_reason,
        "provider": provider,
        "model": model,
    }


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
    return Workflow(
        workflow_id=f"wf_{protocol_id}",
        protocol_id=protocol_id,
        api_calls=calls,
    )


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
        "notice_path": "configs/llm_grounder_notice.txt",
        "save_debug_files": True,
        "require_json_output": True,
        "require_schema_validation": True,
    }


def _load_notice_text(path: str) -> str:
    notice_path = Path(path)
    if not notice_path.exists():
        return ""
    return notice_path.read_text(encoding="utf-8")
