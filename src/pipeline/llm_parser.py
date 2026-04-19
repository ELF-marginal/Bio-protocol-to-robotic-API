from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from src.models.contracts import ParsedProtocol, ParsedStep, ProtocolInput
from src.pipeline.mock_parser import parse_protocol


class LLMParserInput(BaseModel):
    protocol_text: str
    task: str


class LLMParserOutput(BaseModel):
    decision: str
    reasoning_summary: str
    parsed_protocol: dict[str, Any]
    assumptions: list[str] = Field(default_factory=list)


SUPPORTED_ACTIONS = {"take", "add", "mix", "incubate"}


def load_llm_parser_config(path: str = "configs/llm_parser_config.yaml") -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return _default_config()
    loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or not isinstance(loaded.get("llm_parser"), dict):
        return _default_config()
    merged = _default_config()
    merged.update(loaded["llm_parser"])
    return merged


def preprocess_protocol_text(raw_text: str, max_chars: int = 8000) -> dict[str, Any]:
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    replacements = {
        "\u03bcl": "uL",
        "\u00b5l": "uL",
        "ul": "uL",
        "\u03bcL": "uL",
        "\u00b5L": "uL",
        "\u2103": "C",
        "\u00b0C": "C",
    }

    normalized = text
    replacement_count = 0
    for src, tgt in replacements.items():
        count = normalized.count(src)
        if count > 0:
            replacement_count += count
            normalized = normalized.replace(src, tgt)

    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    clipped = normalized[:max_chars]

    return {
        "raw_text": raw_text,
        "processed_text": clipped,
        "replacement_count": replacement_count,
        "was_clipped": len(normalized) > max_chars,
        "raw_length": len(raw_text),
        "processed_length": len(clipped),
    }


def split_protocol_sentences(processed_text: str) -> list[str]:
    return [line.strip() for line in re.split(r"[.;\n]+", processed_text) if line.strip()]


def build_parser_quality_report(parsed: ParsedProtocol, sentences: list[str]) -> dict[str, Any]:
    missing_fields_summary: list[dict[str, Any]] = []
    low_confidence_steps: list[dict[str, Any]] = []
    missing_fields_count = 0
    has_unknown_action = False
    has_unsupported_action = False

    for step in parsed.steps:
        missing: list[str] = []
        signals: list[str] = []
        action_lower = step.action.lower().strip()

        if action_lower == "unknown":
            has_unknown_action = True
            signals.append("unknown_action")
        elif action_lower not in SUPPORTED_ACTIONS:
            has_unsupported_action = True
            signals.append("unsupported_action")

        if action_lower == "add":
            if "source" not in step.entities:
                missing.append("entities.source")
            if "target" not in step.entities:
                missing.append("entities.target")
            if "volume_ul" not in step.parameters:
                missing.append("parameters.volume_ul")
        elif action_lower == "mix":
            if "target" not in step.entities:
                missing.append("entities.target")
            if "times" not in step.parameters:
                missing.append("parameters.times")
        elif action_lower == "incubate":
            if "target" not in step.entities:
                missing.append("entities.target")
            if "temperature_c" not in step.parameters:
                missing.append("parameters.temperature_c")
            if "duration_min" not in step.parameters:
                missing.append("parameters.duration_min")

        # If parser filled required entities but raw text does not explicitly mention them,
        # treat as low-confidence inferred fields to allow conservative LLM refine.
        raw_lower = step.raw_text.lower()
        if action_lower in {"add", "mix", "incubate"}:
            if step.entities.get("target") and all(token not in raw_lower for token in ("tube", "sample")):
                signals.append("implicit_target_inferred")
            if action_lower == "add" and step.entities.get("source") and step.entities["source"] not in raw_lower:
                signals.append("implicit_source_inferred")
            if action_lower == "incubate":
                if "temperature_c" in step.parameters and "c" not in raw_lower:
                    signals.append("implicit_temperature_inferred")
                if "duration_min" in step.parameters and all(t not in raw_lower for t in ("min", "minute")):
                    signals.append("implicit_duration_inferred")

        if missing:
            missing_fields_count += len(missing)
            missing_fields_summary.append(
                {
                    "step_id": step.step_id,
                    "missing_fields": missing,
                    "reason": "missing required fields for action",
                }
            )
            signals.append("missing_required_fields")

        if signals:
            low_confidence_steps.append(
                {
                    "step_id": step.step_id,
                    "raw_text": step.raw_text,
                    "signals": signals,
                }
            )

    has_complex_sentence = any(
        marker in sentence.lower()
        for sentence in sentences
        for marker in (" then ", " and ", ", then ", " after ", " followed by ")
    )

    return {
        "step_count": len(parsed.steps),
        "sentence_count": len(sentences),
        "missing_fields_count": missing_fields_count,
        "missing_fields_summary": missing_fields_summary,
        "suspected_low_confidence_steps": low_confidence_steps,
        "has_unknown_action": has_unknown_action,
        "has_unsupported_action": has_unsupported_action,
        "has_complex_sentence": has_complex_sentence,
    }


def build_llm_parser_input(protocol_text: str) -> dict[str, Any]:
    payload = LLMParserInput(
        protocol_text=protocol_text,
        task=(
            "Parse the protocol text into structured steps. "
            "Return strict JSON output."
        ),
    )
    return payload.model_dump()


def invoke_llm_parser(llm_input: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    provider = str(config.get("provider", "deepseek")).lower()
    model = str(config.get("model", "deepseek-chat"))
    temperature = float(config.get("temperature", 0.0))
    max_retries = int(config.get("max_retries", 2))
    timeout_seconds = int(config.get("timeout_seconds", 60))

    if provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            return _invocation_result(provider=provider, model=model, failure_reason="missing_deepseek_api_key")

        endpoint = str(config.get("endpoint", "https://api.deepseek.com/chat/completions"))
        payload = {
            "model": model,
            "temperature": temperature,
            "messages": _build_llm_parser_messages(llm_input),
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

                parsed, parse_error = parse_llm_parser_output(content)
                if parse_error is None and parsed is not None:
                    return {
                        "llm_parser_invoked": True,
                        "llm_parser_valid_json": True,
                        "raw_output": content,
                        "parsed_output": parsed.model_dump(),
                        "failure_reason": None,
                        "provider": provider,
                        "model": model,
                    }
                return {
                    "llm_parser_invoked": True,
                    "llm_parser_valid_json": False,
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

    if provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        return _invocation_result(provider=provider, model=model, failure_reason="missing_openai_api_key")

    return _invocation_result(provider=provider, model=model, failure_reason="unsupported_provider")


def parse_llm_parser_output(raw_output: str) -> tuple[LLMParserOutput | None, str | None]:
    cleaned = _strip_code_fence(raw_output)
    candidate = _extract_json_object(cleaned) or cleaned
    try:
        loaded = json.loads(candidate)
    except Exception:
        return None, "invalid_json"

    normalized = _normalize_llm_parser_output_shape(loaded)
    if normalized is None:
        return None, "invalid_output_schema"

    try:
        parsed = LLMParserOutput.model_validate(normalized)
    except ValidationError:
        return None, "invalid_output_schema"
    return parsed, None


def to_parsed_protocol_or_none(payload: dict[str, Any]) -> ParsedProtocol | None:
    try:
        protocol_id = str(payload.get("protocol_id", "llm_refined_protocol"))
        steps_raw = payload.get("steps", [])
        if not isinstance(steps_raw, list):
            return None

        steps: list[ParsedStep] = []
        for idx, item in enumerate(steps_raw, start=1):
            if not isinstance(item, dict):
                return None
            steps.append(
                ParsedStep(
                    step_id=str(item.get("step_id", f"s{idx}")),
                    raw_text=str(item.get("raw_text", "")),
                    action=str(item.get("action", "unknown")),
                    entities=item.get("entities", {}) if isinstance(item.get("entities", {}), dict) else {},
                    parameters=item.get("parameters", {}) if isinstance(item.get("parameters", {}), dict) else {},
                )
            )
        return ParsedProtocol(protocol_id=protocol_id, steps=steps)
    except Exception:
        return None


def _invocation_result(provider: str, model: str, failure_reason: str) -> dict[str, Any]:
    return {
        "llm_parser_invoked": True,
        "llm_parser_valid_json": False,
        "raw_output": "",
        "parsed_output": None,
        "failure_reason": failure_reason,
        "provider": provider,
        "model": model,
    }


def _default_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "provider": "deepseek",
        "model": "deepseek-chat",
        "temperature": 0.0,
        "max_retries": 2,
        "timeout_seconds": 60,
        "endpoint": "https://api.deepseek.com/chat/completions",
        "save_debug_files": True,
        "require_json_output": True,
        "require_schema_validation": True,
    }


def _build_llm_parser_messages(llm_input: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a strict lab protocol parser. "
                "Return JSON only, no markdown, no code fences."
            ),
        },
        {
            "role": "system",
            "content": (
                "Output schema MUST be: "
                "{\"decision\":\"accept\",\"reasoning_summary\":\"...\","
                "\"parsed_protocol\":{\"protocol_id\":\"...\",\"steps\":["
                "{\"step_id\":\"s1\",\"raw_text\":\"...\",\"action\":\"...\","
                "\"entities\":{},\"parameters\":{}}]},\"assumptions\":[]} . "
                "Each step must contain action/entities/parameters. "
                "Map destination/container/tube to entities.target. "
                "Map volume/temperature/duration into parameters.volume_ul/temperature_c/duration_min when possible."
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


def run_parser_backend(
    protocol: ProtocolInput,
    enable_llm_parser: bool,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preprocess_payload = preprocess_protocol_text(protocol.raw_text)
    processed_protocol = ProtocolInput(
        protocol_id=protocol.protocol_id,
        title=protocol.title,
        source=protocol.source,
        raw_text=preprocess_payload["processed_text"],
    )

    llm_cfg = config or load_llm_parser_config()
    llm_parser_result: dict[str, Any] = {
        "parser_backend_mode": "rule_only",
        "llm_parser_invoked": False,
        "llm_parser_valid_json": False,
        "llm_parser_schema_valid": False,
        "llm_parser_accepted": False,
        "llm_parser_fallback_used": False,
        "llm_parser_failure_reason": None,
    }
    llm_parser_input_payload: dict[str, Any] | None = None
    llm_parser_raw_output_payload: dict[str, Any] | None = None
    llm_parser_parsed_output_payload: dict[str, Any] | None = None

    if not enable_llm_parser:
        parsed = parse_protocol(processed_protocol)
        parser_quality_report = build_parser_quality_report(parsed, split_protocol_sentences(processed_protocol.raw_text))
        llm_parser_result["llm_parser_failure_reason"] = "llm_parser_disabled"
        return {
            "parsed": parsed,
            "protocol": processed_protocol,
            "parser_preprocess": preprocess_payload,
            "parser_quality_report": parser_quality_report,
            "llm_parser_result": llm_parser_result,
            "llm_parser_input": None,
            "llm_parser_raw_output": None,
            "llm_parser_parsed_output": None,
        }

    llm_cfg["enabled"] = True
    llm_parser_result["parser_backend_mode"] = "llm_primary"
    llm_parser_result["llm_parser_invoked"] = True

    llm_parser_input_payload = build_llm_parser_input(protocol_text=processed_protocol.raw_text)
    invocation = invoke_llm_parser(llm_parser_input_payload, llm_cfg)
    llm_parser_raw_output_payload = {
        "provider": invocation.get("provider"),
        "model": invocation.get("model"),
        "raw_output": invocation.get("raw_output", ""),
        "failure_reason": invocation.get("failure_reason"),
    }

    parsed_output: LLMParserOutput | None = None
    if invocation.get("parsed_output") is not None:
        llm_parser_result["llm_parser_valid_json"] = True
        parsed_output = LLMParserOutput.model_validate(invocation["parsed_output"])
        llm_parser_parsed_output_payload = parsed_output.model_dump()
    else:
        raw_output = invocation.get("raw_output", "")
        if isinstance(raw_output, str) and raw_output.strip():
            parsed_output, parse_error = parse_llm_parser_output(raw_output)
            if parse_error is None and parsed_output is not None:
                llm_parser_result["llm_parser_valid_json"] = True
                llm_parser_parsed_output_payload = parsed_output.model_dump()
            else:
                llm_parser_result["llm_parser_failure_reason"] = parse_error
        else:
            llm_parser_result["llm_parser_failure_reason"] = invocation.get("failure_reason")

    if parsed_output is None:
        fallback = parse_protocol(processed_protocol)
        llm_parser_result["llm_parser_fallback_used"] = True
        parser_quality_report = build_parser_quality_report(
            fallback, split_protocol_sentences(processed_protocol.raw_text)
        )
        return {
            "parsed": fallback,
            "protocol": processed_protocol,
            "parser_preprocess": preprocess_payload,
            "parser_quality_report": parser_quality_report,
            "llm_parser_result": llm_parser_result,
            "llm_parser_input": llm_parser_input_payload,
            "llm_parser_raw_output": llm_parser_raw_output_payload,
            "llm_parser_parsed_output": llm_parser_parsed_output_payload,
        }

    normalized_payload = _normalize_llm_parsed_protocol_payload(
        protocol_id=processed_protocol.protocol_id,
        parsed_protocol=parsed_output.parsed_protocol,
    )
    valid, reason = _validate_llm_parsed_protocol_payload(normalized_payload)
    if not valid:
        fallback = parse_protocol(processed_protocol)
        llm_parser_result["llm_parser_schema_valid"] = False
        llm_parser_result["llm_parser_failure_reason"] = reason
        llm_parser_result["llm_parser_fallback_used"] = True
        parser_quality_report = build_parser_quality_report(
            fallback, split_protocol_sentences(processed_protocol.raw_text)
        )
        return {
            "parsed": fallback,
            "protocol": processed_protocol,
            "parser_preprocess": preprocess_payload,
            "parser_quality_report": parser_quality_report,
            "llm_parser_result": llm_parser_result,
            "llm_parser_input": llm_parser_input_payload,
            "llm_parser_raw_output": llm_parser_raw_output_payload,
            "llm_parser_parsed_output": llm_parser_parsed_output_payload,
        }

    parsed = to_parsed_protocol_or_none(normalized_payload)
    if parsed is None:
        fallback = parse_protocol(processed_protocol)
        llm_parser_result["llm_parser_schema_valid"] = False
        llm_parser_result["llm_parser_failure_reason"] = "invalid_schema"
        llm_parser_result["llm_parser_fallback_used"] = True
        parser_quality_report = build_parser_quality_report(
            fallback, split_protocol_sentences(processed_protocol.raw_text)
        )
        return {
            "parsed": fallback,
            "protocol": processed_protocol,
            "parser_preprocess": preprocess_payload,
            "parser_quality_report": parser_quality_report,
            "llm_parser_result": llm_parser_result,
            "llm_parser_input": llm_parser_input_payload,
            "llm_parser_raw_output": llm_parser_raw_output_payload,
            "llm_parser_parsed_output": llm_parser_parsed_output_payload,
        }

    llm_parser_result["llm_parser_schema_valid"] = True
    llm_parser_result["llm_parser_accepted"] = True
    llm_parser_result["llm_parser_failure_reason"] = None
    parser_quality_report = build_parser_quality_report(parsed, split_protocol_sentences(processed_protocol.raw_text))
    return {
        "parsed": parsed,
        "protocol": processed_protocol,
        "parser_preprocess": preprocess_payload,
        "parser_quality_report": parser_quality_report,
        "llm_parser_result": llm_parser_result,
        "llm_parser_input": llm_parser_input_payload,
        "llm_parser_raw_output": llm_parser_raw_output_payload,
        "llm_parser_parsed_output": llm_parser_parsed_output_payload,
    }


def _validate_llm_parsed_protocol_payload(parsed_protocol: dict[str, Any]) -> tuple[bool, str | None]:
    if not isinstance(parsed_protocol, dict):
        return False, "invalid_schema"
    steps = parsed_protocol.get("steps")
    if not isinstance(steps, list):
        return False, "invalid_schema"
    if len(steps) == 0:
        return False, "empty_steps"
    for step in steps:
        if not isinstance(step, dict):
            return False, "invalid_schema"
        action = step.get("action")
        if not isinstance(action, str) or not action.strip():
            return False, "invalid_step_action"
        entities = step.get("entities", {})
        parameters = step.get("parameters", {})
        if not isinstance(entities, dict) or not isinstance(parameters, dict):
            return False, "invalid_entities_or_parameters"
    return True, None


def _normalize_llm_parsed_protocol_payload(
    protocol_id: str,
    parsed_protocol: dict[str, Any],
) -> dict[str, Any]:
    steps_raw = parsed_protocol.get("steps")
    if not isinstance(steps_raw, list):
        steps_raw = parsed_protocol.get("protocol_steps")
    if not isinstance(steps_raw, list):
        steps_raw = []
    normalized_steps: list[dict[str, Any]] = []
    for idx, step in enumerate(steps_raw, start=1):
        step_dict = dict(step) if isinstance(step, dict) else {}
        params = step_dict.get("parameters", {})
        if not isinstance(params, dict):
            params = {}
        step_dict["parameters"] = params
        entities = step_dict.get("entities", {})
        if not isinstance(entities, dict):
            entities = {}
        step_dict["entities"] = entities
        if "step_id" not in step_dict:
            step_num = step_dict.get("step_number")
            if isinstance(step_num, int) and step_num > 0:
                step_dict["step_id"] = f"s{step_num}"
            else:
                step_dict["step_id"] = f"s{idx}"
        if "raw_text" not in step_dict:
            step_dict["raw_text"] = ""
        if not isinstance(step_dict.get("action"), str):
            step_dict["action"] = "unknown"
        step_dict["action"] = str(step_dict.get("action", "unknown")).strip() or "unknown"
        normalized_steps.append(step_dict)
    return {"protocol_id": protocol_id, "steps": normalized_steps}


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


def _normalize_llm_parser_output_shape(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    # Preferred wrapper shape
    if "parsed_protocol" in payload:
        out = dict(payload)
        out.setdefault("decision", "accept")
        out.setdefault("reasoning_summary", "normalized_wrapper_output")
        out.setdefault("assumptions", [])
        return out

    # Common fallback from models: direct ParsedProtocol
    if "steps" in payload or "protocol_steps" in payload:
        return {
            "decision": "accept",
            "reasoning_summary": "normalized_direct_parsed_protocol_output",
            "parsed_protocol": payload,
            "assumptions": [],
        }

    # Nested fallback wrappers from providers/tooling
    for key in ("result", "output", "data", "parsed"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            nested_normalized = _normalize_llm_parser_output_shape(nested)
            if nested_normalized is not None:
                return nested_normalized
    return None
