from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.models.contracts import ApiCall, Workflow


@dataclass
class ValidationIssue:
    issue_type: str
    severity: str
    call_id: str | None
    api: str | None
    message: str
    suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_type": self.issue_type,
            "severity": self.severity,
            "call_id": self.call_id,
            "api": self.api,
            "message": self.message,
            "suggestion": self.suggestion,
        }


def validate_workflow(
    workflow: Workflow,
    api_registry_path: str = "configs/api_registry.yaml",
) -> dict[str, Any]:
    registry = _load_registry(api_registry_path)
    issues: list[ValidationIssue] = []

    fridge_open = False
    has_tip = False
    uncapped_tubes: set[str] = set()
    heater_temperature_set = False

    for call in workflow.api_calls:
        api = call.api
        args = call.args

        # API existence
        if api not in registry:
            issues.append(
                ValidationIssue(
                    issue_type="UnknownAPI",
                    severity="error",
                    call_id=call.call_id,
                    api=api,
                    message=f"API {api} is not defined in registry.",
                )
            )
            continue

        # Required parameters
        missing = [name for name in registry[api] if name not in args]
        if missing:
            issues.append(
                ValidationIssue(
                    issue_type="MissingParameter",
                    severity="error",
                    call_id=call.call_id,
                    api=api,
                    message=f"Missing required parameters: {missing}",
                )
            )

        # Sequence rules
        if api == "fridge.open":
            fridge_open = True
        elif api == "fridge.close":
            if not fridge_open:
                issues.append(
                    ValidationIssue(
                        issue_type="OrderViolation",
                        severity="error",
                        call_id=call.call_id,
                        api=api,
                        message="fridge.close called before fridge.open.",
                        suggestion="Insert fridge.open before this call.",
                    )
                )
            fridge_open = False
        elif api == "tube.uncap":
            tube_id = _get_str(args, "tube_id")
            if tube_id:
                uncapped_tubes.add(tube_id)
        elif api == "tube.cap":
            tube_id = _get_str(args, "tube_id")
            if tube_id and tube_id in uncapped_tubes:
                uncapped_tubes.remove(tube_id)
        elif api == "pipette.attach_tip":
            has_tip = True
        elif api == "pipette.discard_tip":
            has_tip = False
        elif api == "pipette.transfer":
            if not has_tip:
                issues.append(
                    ValidationIssue(
                        issue_type="PreconditionViolation",
                        severity="error",
                        call_id=call.call_id,
                        api=api,
                        message="pipette.transfer called without tip attached.",
                        suggestion="Insert pipette.attach_tip before this call.",
                    )
                )
            target = _get_str(args, "target")
            if target and target not in uncapped_tubes:
                issues.append(
                    ValidationIssue(
                        issue_type="PreconditionViolation",
                        severity="error",
                        call_id=call.call_id,
                        api=api,
                        message=f"pipette.transfer target {target} is not uncapped.",
                        suggestion=f"Insert tube.uncap(tube_id={target}) before this call.",
                    )
                )
        elif api == "pipette.mix":
            if not has_tip:
                issues.append(
                    ValidationIssue(
                        issue_type="PreconditionViolation",
                        severity="error",
                        call_id=call.call_id,
                        api=api,
                        message="pipette.mix called without tip attached.",
                        suggestion="Insert pipette.attach_tip before this call.",
                    )
                )
            container = _get_str(args, "container")
            if container and container not in uncapped_tubes:
                issues.append(
                    ValidationIssue(
                        issue_type="PreconditionViolation",
                        severity="error",
                        call_id=call.call_id,
                        api=api,
                        message=f"pipette.mix container {container} is not uncapped.",
                        suggestion=f"Insert tube.uncap(tube_id={container}) before this call.",
                    )
                )
        elif api == "heater.set_temperature":
            heater_temperature_set = True
        elif api == "heater.place":
            if not heater_temperature_set:
                issues.append(
                    ValidationIssue(
                        issue_type="PreconditionViolation",
                        severity="error",
                        call_id=call.call_id,
                        api=api,
                        message="heater.place called before heater.set_temperature.",
                        suggestion="Insert heater.set_temperature before this call.",
                    )
                )

    return {
        "valid": not any(issue.severity == "error" for issue in issues),
        "issue_count": len(issues),
        "issues": [issue.to_dict() for issue in issues],
    }


def _load_registry(path: str) -> dict[str, set[str]]:
    payload = Path(path).read_text(encoding="utf-8")
    loaded = yaml.safe_load(payload)
    apis = loaded.get("apis", []) if isinstance(loaded, dict) else []
    registry: dict[str, set[str]] = {}
    for api_item in apis:
        if not isinstance(api_item, dict):
            continue
        name = api_item.get("name")
        params = api_item.get("parameters", {})
        if not isinstance(name, str):
            continue
        if isinstance(params, dict):
            registry[name] = set(params.keys())
        else:
            registry[name] = set()
    return registry


def _get_str(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    return value if isinstance(value, str) else None
