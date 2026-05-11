from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.models.contracts import Workflow
from src.pipeline.domain_simulator import simulate_workflow


def validate_workflow(
    workflow: Workflow,
    api_domain_path: str = "configs/api_registry.yaml",
    lab_state_path: str = "configs/initial_lab_state.yaml",
    safety_rules_path: str | None = None,
    api_domain: dict[str, Any] | None = None,
    lab_state: dict[str, Any] | None = None,
    safety_rules: list[dict[str, Any]] | None = None,
    stop_on_error: bool = False,
) -> dict[str, Any]:
    domain_payload = api_domain if api_domain is not None else _load_yaml_mapping(api_domain_path)
    state_payload = lab_state if lab_state is not None else _load_yaml_mapping(lab_state_path)
    safety_payload = safety_rules if safety_rules is not None else _load_yaml_list(safety_rules_path)
    validation = simulate_workflow(
        workflow=workflow,
        api_domain=domain_payload,
        lab_state=state_payload,
        safety_rules=safety_payload,
        stop_on_error=stop_on_error,
    )
    return {
        "valid": validation["valid"],
        "issue_count": validation["issue_count"],
        "issues": validation["issues"],
        "final_state": validation["final_state"],
        "state_snapshots": validation["state_snapshots"],
    }


def _load_yaml_mapping(path: str) -> dict[str, Any]:
    loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _load_yaml_list(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    rule_path = Path(path)
    if not rule_path.exists():
        return []
    loaded = yaml.safe_load(rule_path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, list) else []
