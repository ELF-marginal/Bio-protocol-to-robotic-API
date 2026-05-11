from __future__ import annotations

from typing import Any

from src.models.contracts import Workflow
from src.pipeline.llm_repair import (
    apply_llm_operations,
    build_llm_input,
    invoke_llm_repair,
    load_api_registry,
    load_llm_repair_config,
    parse_llm_output,
    should_invoke_llm_repair,
    validate_operations_before_apply,
)
from src.pipeline.validator import validate_workflow


def repair_workflow(
    workflow: Workflow,
    validation_result: dict[str, Any],
    protocol_text: str = "",
    parsed_steps: list[dict[str, Any]] | None = None,
    api_domain_path: str = "configs/api_registry.yaml",
    lab_state_path: str = "configs/initial_lab_state.yaml",
    api_domain: dict[str, Any] | None = None,
    lab_state: dict[str, Any] | None = None,
    safety_rules: list[dict[str, Any]] | None = None,
    enable_llm_repair: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or load_llm_repair_config()
    max_repair_rounds = int(cfg.get("max_repair_rounds", 1))
    current_workflow = workflow
    current_validation = validation_result
    repair_rounds: list[dict[str, Any]] = []
    applied_repairs: list[dict[str, Any]] = []

    for round_index in range(1, max_repair_rounds + 1):
        remaining_issues = current_validation.get("issues", [])
        invoke, invoke_reason = should_invoke_llm_repair(
            enable_llm_repair=enable_llm_repair,
            config=cfg,
            remaining_issues=remaining_issues,
        )
        if not invoke:
            repair_rounds.append(
                {
                    "round": round_index,
                    "llm_invoked": False,
                    "skipped_reason": invoke_reason,
                    "validation_before": current_validation,
                }
            )
            break

        llm_input_payload = build_llm_input(
            protocol_text=protocol_text,
            parsed_steps=parsed_steps or [],
            workflow_before_llm_repair=current_workflow,
            validation_issues_before_llm=validation_result.get("issues", []),
            applied_rule_repairs=[],
            remaining_issues_after_rule_repair=remaining_issues,
            api_domain_path=api_domain_path,
            lab_state_path=lab_state_path,
            api_domain=api_domain,
            lab_state=lab_state,
            safety_rules=safety_rules,
            simulated_final_state_before_repair=current_validation.get("final_state", {}),
        )
        llm_invocation = invoke_llm_repair(llm_input_payload, cfg)
        llm_raw_output_payload = {
            "provider": llm_invocation.get("provider"),
            "model": llm_invocation.get("model"),
            "raw_output": llm_invocation.get("raw_output", ""),
            "failure_reason": llm_invocation.get("failure_reason"),
        }

        parsed_output_payload: dict[str, Any] | None = None
        if llm_invocation.get("parsed_output") is not None:
            parsed_output_payload = llm_invocation["parsed_output"]
        else:
            raw_output = llm_invocation.get("raw_output", "")
            if isinstance(raw_output, str) and raw_output.strip():
                parsed_output, parse_error = parse_llm_output(raw_output)
                if parse_error is not None or parsed_output is None:
                    repair_rounds.append(
                        {
                            "round": round_index,
                            "llm_invoked": True,
                            "llm_input": llm_input_payload,
                            "llm_raw_output": llm_raw_output_payload,
                            "llm_parsed_output": None,
                            "patch_result": {"patch_applied": False, "error": parse_error},
                            "validation_before": current_validation,
                            "validation_after": current_validation,
                        }
                    )
                    break
                parsed_output_payload = parsed_output.model_dump()
            else:
                repair_rounds.append(
                    {
                        "round": round_index,
                        "llm_invoked": True,
                        "llm_input": llm_input_payload,
                        "llm_raw_output": llm_raw_output_payload,
                        "llm_parsed_output": None,
                        "patch_result": {"patch_applied": False, "error": llm_invocation.get("failure_reason")},
                        "validation_before": current_validation,
                        "validation_after": current_validation,
                    }
                )
                break

        api_registry = load_api_registry(api_domain_path)
        operations = parsed_output_payload.get("operations", []) if isinstance(parsed_output_payload, dict) else []
        ok_ops, ops_error = validate_operations_before_apply(
            operations=operations,
            workflow=current_workflow,
            api_registry=api_registry,
        )
        if not ok_ops:
            repair_rounds.append(
                {
                    "round": round_index,
                    "llm_invoked": True,
                    "llm_input": llm_input_payload,
                    "llm_raw_output": llm_raw_output_payload,
                    "llm_parsed_output": parsed_output_payload,
                    "patch_result": {"patch_applied": False, "error": ops_error},
                    "validation_before": current_validation,
                    "validation_after": current_validation,
                }
            )
            break

        patched_workflow, patch_meta = apply_llm_operations(
            workflow=current_workflow,
            operations=operations,
            api_registry=api_registry,
        )
        if patched_workflow is None:
            repair_rounds.append(
                {
                    "round": round_index,
                    "llm_invoked": True,
                    "llm_input": llm_input_payload,
                    "llm_raw_output": llm_raw_output_payload,
                    "llm_parsed_output": parsed_output_payload,
                    "patch_result": patch_meta,
                    "validation_before": current_validation,
                    "validation_after": current_validation,
                }
            )
            break

        validation_after = validate_workflow(
            workflow=patched_workflow,
            api_domain_path=api_domain_path,
            lab_state_path=lab_state_path,
            api_domain=api_domain,
            lab_state=lab_state,
            safety_rules=safety_rules,
        )
        applied_repairs.extend(patch_meta.get("applied_operations", []))
        repair_rounds.append(
            {
                "round": round_index,
                "llm_invoked": True,
                "llm_input": llm_input_payload,
                "llm_raw_output": llm_raw_output_payload,
                "llm_parsed_output": parsed_output_payload,
                "patch_result": patch_meta,
                "workflow_before_patch": current_workflow.model_dump(),
                "workflow_after_patch": patched_workflow.model_dump(),
                "validation_before": current_validation,
                "validation_after": validation_after,
            }
        )
        current_workflow = patched_workflow
        current_validation = validation_after
        if current_validation.get("valid", False):
            break

    return {
        "repaired": bool(applied_repairs),
        "repair_success": bool(current_validation.get("valid", False)),
        "applied_repairs": applied_repairs,
        "workflow": current_workflow,
        "validation_result": current_validation,
        "rounds": repair_rounds,
        "max_repair_rounds": max_repair_rounds,
    }
