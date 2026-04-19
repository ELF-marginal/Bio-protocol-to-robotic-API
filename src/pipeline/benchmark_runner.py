from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.models.contracts import ExecutionResult, ProtocolInput
from src.pipeline.executor import execute_workflow
from src.pipeline.llm_grounder import (
    load_llm_grounder_config,
    run_grounder_backend,
)
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
from src.pipeline.llm_parser import (
    load_llm_parser_config,
    run_parser_backend,
)
from src.pipeline.repair import repair_workflow
from src.pipeline.validator import validate_workflow
from src.pipeline.workflow_planner import compose_workflow
from src.utils.io import dump_json, ensure_dir


@dataclass
class MetricCounter:
    matched: int = 0
    total: int = 0

    def update(self, matched: int, total: int) -> None:
        self.matched += matched
        self.total += total

    def rate(self) -> float:
        if self.total == 0:
            return 1.0
        return self.matched / self.total


@dataclass
class CaseResult:
    case_id: str
    success: bool
    checks: dict[str, bool]
    details: dict[str, Any]
    failure_reasons: list[str]


def run_benchmark(
    cases_dir: Path,
    output_dir: Path,
    enable_llm_repair: bool = False,
    enable_llm_parser: bool = False,
    enable_llm_grounder: bool = False,
) -> dict[str, Any]:
    ensure_dir(output_dir)
    case_files = sorted(cases_dir.glob("*.yaml"))
    results: list[CaseResult] = []

    parsing_counter = MetricCounter()
    grounding_counter = MetricCounter()
    parameter_counter = MetricCounter()
    sequence_counter = MetricCounter()
    precondition_violation_count = 0

    repaired_cases = 0
    unrepaired_cases = 0
    unrepairable_cases = 0
    repaired_case_ids: list[str] = []
    unrepaired_case_ids: list[str] = []
    unrepairable_case_ids: list[str] = []
    repair_debug_records: list[dict[str, Any]] = []
    llm_invoked_cases = 0
    llm_repaired_cases = 0
    post_llm_validation_pass_cases = 0
    llm_before_issue_sum = 0
    llm_after_issue_sum = 0
    parser_llm_invoked_cases = 0
    parser_llm_accepted_cases = 0
    parser_llm_fallback_cases = 0
    parser_llm_schema_fail_cases = 0
    parser_failure_reason_counts: dict[str, int] = {}
    grounding_llm_invoked_cases = 0
    grounding_llm_success_cases = 0
    grounding_unregistered_api_cases = 0
    grounding_failure_reason_counts: dict[str, int] = {}

    for case_file in case_files:
        loaded_case = yaml.safe_load(case_file.read_text(encoding="utf-8"))
        if not isinstance(loaded_case, dict):
            results.append(
                CaseResult(
                    case_id=case_file.stem,
                    success=False,
                    checks={
                        "parsing_check": False,
                        "grounding_check": False,
                        "sequence_check": False,
                        "parameter_check": False,
                        "final_state_check": False,
                        "validation_after_check": False,
                        "execution_check": False,
                    },
                    details={
                        "execution_success": False,
                        "api_calls": [],
                        "executed_calls": 0,
                        "validation_before": {"valid": False, "issue_count": 1, "issues": []},
                        "repair_result": {"repaired": False, "applied_repairs": []},
                        "validation_after": {"valid": False, "issue_count": 1, "issues": []},
                        "llm_repair_result": {},
                        "llm_parser_result": {},
                        "llm_validation_result": {},
                        "metrics": {
                            "parsing": {"matched": 0, "total": 1},
                            "grounding": {"matched": 0, "total": 1},
                            "sequence": {"matched": 0, "total": 1},
                            "parameter": {"matched": 0, "total": 1},
                            "final_state": {"matched": 0, "total": 1},
                        },
                    },
                    failure_reasons=[f"invalid_case_file: {case_file.name} is empty or not a YAML mapping"],
                )
            )
            continue

        case = loaded_case
        case_id = str(case.get("case_id", case_file.stem))
        protocol_text = case.get("input_protocol")
        expected_success = bool(case.get("expected_success", True))
        failure_reasons: list[str] = []
        if not isinstance(protocol_text, str) or not protocol_text.strip():
            results.append(
                CaseResult(
                    case_id=case_id,
                    success=False,
                    checks={
                        "parsing_check": False,
                        "grounding_check": False,
                        "sequence_check": False,
                        "parameter_check": False,
                        "final_state_check": False,
                        "validation_after_check": False,
                        "execution_check": False,
                    },
                    details={
                        "execution_success": False,
                        "api_calls": [],
                        "executed_calls": 0,
                        "validation_before": {"valid": False, "issue_count": 1, "issues": []},
                        "repair_result": {"repaired": False, "applied_repairs": []},
                        "validation_after": {"valid": False, "issue_count": 1, "issues": []},
                        "llm_repair_result": {},
                        "llm_parser_result": {},
                        "llm_validation_result": {},
                        "metrics": {
                            "parsing": {"matched": 0, "total": 1},
                            "grounding": {"matched": 0, "total": 1},
                            "sequence": {"matched": 0, "total": 1},
                            "parameter": {"matched": 0, "total": 1},
                            "final_state": {"matched": 0, "total": 1},
                        },
                    },
                    failure_reasons=[f"invalid_case_file: {case_file.name} missing non-empty input_protocol"],
                )
            )
            continue

        protocol = ProtocolInput(
            protocol_id=case_id,
            title=case_id,
            source="benchmark_case",
            raw_text=protocol_text,
        )
        llm_parser_cfg = load_llm_parser_config()
        parser_backend = run_parser_backend(
            protocol=protocol,
            enable_llm_parser=enable_llm_parser,
            config=llm_parser_cfg,
        )
        parsed = parser_backend["parsed"]
        protocol = parser_backend["protocol"]
        preprocess_payload = parser_backend["parser_preprocess"]
        llm_parser_result = parser_backend["llm_parser_result"]
        llm_parser_input_payload = parser_backend.get("llm_parser_input")
        llm_parser_raw_output_payload = parser_backend.get("llm_parser_raw_output")
        llm_parser_parsed_output_payload = parser_backend.get("llm_parser_parsed_output")
        llm_grounder_input_payload: dict[str, Any] | None = None
        llm_grounder_raw_output_payload: dict[str, Any] | None = None
        llm_grounder_parsed_output_payload: dict[str, Any] | None = None

        llm_grounder_cfg = load_llm_grounder_config()
        grounder_backend = run_grounder_backend(
            parsed_protocol=parsed,
            enable_llm_grounder=enable_llm_grounder,
            config=llm_grounder_cfg,
        )
        grounded = grounder_backend["workflow"]
        grounding_result = grounder_backend["grounding_result"]
        grounding_validation_result = grounder_backend["grounding_validation_result"]
        llm_grounder_input_payload = grounder_backend.get("llm_grounder_input")
        llm_grounder_raw_output_payload = grounder_backend.get("llm_grounder_raw_output")
        llm_grounder_parsed_output_payload = grounder_backend.get("llm_grounder_parsed_output")

        if llm_parser_result.get("llm_parser_invoked", False):
            parser_llm_invoked_cases += 1
        if llm_parser_result.get("llm_parser_accepted", False):
            parser_llm_accepted_cases += 1
        if llm_parser_result.get("llm_parser_invoked", False) and llm_parser_result.get("llm_parser_fallback_used", False):
            parser_llm_fallback_cases += 1
        if llm_parser_result.get("llm_parser_invoked", False) and not llm_parser_result.get("llm_parser_schema_valid", False):
            parser_llm_schema_fail_cases += 1
        parser_reason = llm_parser_result.get("llm_parser_failure_reason")
        if isinstance(parser_reason, str) and parser_reason:
            parser_failure_reason_counts[parser_reason] = parser_failure_reason_counts.get(parser_reason, 0) + 1

        if grounding_result.get("llm_grounder_invoked", False):
            grounding_llm_invoked_cases += 1
        if grounding_result.get("grounding_valid", True):
            grounding_llm_success_cases += 1
        if grounding_result.get("contains_unregistered_api", False):
            grounding_unregistered_api_cases += 1
        gr = grounding_result.get("grounding_failure_reason")
        if isinstance(gr, str) and gr:
            grounding_failure_reason_counts[gr] = grounding_failure_reason_counts.get(gr, 0) + 1

        workflow_before_repair = compose_workflow(grounded) if grounding_result.get("grounding_valid", True) else grounded

        if grounding_result.get("grounding_valid", True):
            validation_before = validate_workflow(workflow_before_repair)
            repair_payload = repair_workflow(workflow_before_repair, validation_before)
            workflow_after_repair = (
                repair_payload["workflow"] if repair_payload.get("repaired") else workflow_before_repair
            )
            validation_after = validate_workflow(workflow_after_repair)
            workflow_final = workflow_after_repair
        else:
            validation_before = {"valid": False, "issue_count": 1, "issues": [{"issue_type": "GroundingError", "message": grounding_result.get("grounding_failure_reason")}]}
            repair_payload = {"repaired": False, "applied_repairs": []}
            workflow_after_repair = workflow_before_repair
            validation_after = validation_before
            workflow_final = workflow_after_repair

        llm_repair_result = {
            "llm_invoked": False,
            "llm_output_valid_json": False,
            "llm_patch_applied": False,
            "llm_patch_accepted": False,
            "llm_repair_success": False,
            "llm_failure_reason": None,
        }
        llm_validation_result = {
            "validation_before_llm_issue_count": 0,
            "validation_after_llm_issue_count": 0,
            "remaining_issues_after_llm": [],
        }
        llm_input_payload: dict[str, Any] | None = None
        llm_raw_output_payload: dict[str, Any] | None = None
        llm_parsed_output_payload: dict[str, Any] | None = None
        workflow_before_llm_patch_payload: dict[str, Any] | None = None
        workflow_after_llm_patch_payload: dict[str, Any] | None = None

        llm_cfg = load_llm_repair_config()
        remaining_issues = validation_after.get("issues", [])
        invoke_llm = False
        invoke_reason = "grounding_invalid_skip_llm_repair"
        if grounding_result.get("grounding_valid", True):
            invoke_llm, invoke_reason = should_invoke_llm_repair(
                enable_llm_repair=enable_llm_repair,
                config=llm_cfg,
                remaining_issues=remaining_issues,
            )
        if invoke_llm:
            repair_api_registry_path = str(llm_cfg.get("api_registry_path", "configs/api_registry.yaml"))
            repair_initial_lab_state_path = str(llm_cfg.get("initial_lab_state_path", "configs/initial_lab_state.yaml"))
            repair_expected_lab_state_path = str(llm_cfg.get("expected_lab_state_path", "configs/initial_lab_state.yaml"))
            repair_notice_path = str(llm_cfg.get("notice_path", "configs/llm_repair_notice.txt"))
            llm_invoked_cases += 1
            llm_repair_result["llm_invoked"] = True

            llm_input_payload = build_llm_input(
                protocol_text=protocol.raw_text,
                parsed_steps=[step.model_dump() for step in parsed.steps],
                workflow_before_llm_repair=workflow_after_repair,
                validation_issues_before_llm=validation_before.get("issues", []),
                applied_rule_repairs=repair_payload.get("applied_repairs", []),
                remaining_issues_after_rule_repair=remaining_issues,
                api_registry_path=repair_api_registry_path,
                initial_lab_state_path=repair_initial_lab_state_path,
                expected_lab_state_path=repair_expected_lab_state_path,
                notice_path=repair_notice_path,
            )
            llm_invocation = invoke_llm_repair(llm_input_payload, llm_cfg)
            llm_raw_output_payload = {
                "provider": llm_invocation.get("provider"),
                "model": llm_invocation.get("model"),
                "raw_output": llm_invocation.get("raw_output", ""),
                "failure_reason": llm_invocation.get("failure_reason"),
            }

            raw_output = llm_invocation.get("raw_output", "")
            if llm_invocation.get("parsed_output") is not None:
                llm_parsed_output_payload = llm_invocation["parsed_output"]
                llm_repair_result["llm_output_valid_json"] = True
            elif isinstance(raw_output, str) and raw_output.strip():
                parsed_output, parse_error = parse_llm_output(raw_output)
                if parse_error is None and parsed_output is not None:
                    llm_parsed_output_payload = parsed_output.model_dump()
                    llm_repair_result["llm_output_valid_json"] = True
                else:
                    llm_repair_result["llm_failure_reason"] = parse_error
            else:
                llm_repair_result["llm_failure_reason"] = llm_invocation.get("failure_reason")

            if llm_parsed_output_payload is not None:
                api_registry = load_api_registry(path=repair_api_registry_path)
                ok_ops, ops_error = validate_operations_before_apply(
                    operations=llm_parsed_output_payload.get("operations", []),
                    workflow=workflow_after_repair,
                    api_registry=api_registry,
                )
                if ok_ops:
                    workflow_before_llm_patch_payload = workflow_after_repair.model_dump()
                    patched_workflow, patch_meta = apply_llm_operations(
                        workflow=workflow_after_repair,
                        operations=llm_parsed_output_payload.get("operations", []),
                        api_registry=api_registry,
                    )
                    llm_repair_result["llm_patch_applied"] = bool(patch_meta.get("patch_applied", False))
                    if patched_workflow is not None:
                        workflow_after_llm_patch_payload = patched_workflow.model_dump()
                        llm_validation_after = validate_workflow(patched_workflow)
                        llm_validation_result = {
                            "validation_before_llm_issue_count": len(remaining_issues),
                            "validation_after_llm_issue_count": llm_validation_after.get("issue_count", 0),
                            "remaining_issues_after_llm": llm_validation_after.get("issues", []),
                        }
                        if llm_validation_after.get("valid", False):
                            workflow_final = patched_workflow
                            validation_after = llm_validation_after
                            llm_repair_result["llm_patch_accepted"] = True
                            llm_repair_result["llm_repair_success"] = True
                            llm_repair_result["llm_failure_reason"] = None
                            llm_repaired_cases += 1
                            post_llm_validation_pass_cases += 1
                        else:
                            llm_repair_result["llm_patch_accepted"] = False
                            llm_repair_result["llm_failure_reason"] = "revalidate_failed_after_patch"
                    else:
                        llm_repair_result["llm_failure_reason"] = patch_meta.get("error", "patch_apply_failed")
                else:
                    llm_repair_result["llm_failure_reason"] = ops_error

            if llm_validation_result["validation_before_llm_issue_count"] == 0:
                llm_validation_result = {
                    "validation_before_llm_issue_count": len(remaining_issues),
                    "validation_after_llm_issue_count": validation_after.get("issue_count", 0),
                    "remaining_issues_after_llm": validation_after.get("issues", []),
                }
            llm_before_issue_sum += llm_validation_result["validation_before_llm_issue_count"]
            llm_after_issue_sum += llm_validation_result["validation_after_llm_issue_count"]
        else:
            llm_repair_result["llm_failure_reason"] = invoke_reason

        if not validation_before.get("valid", True):
            if repair_payload.get("repaired") and validation_after.get("valid", False):
                repaired_cases += 1
                repaired_case_ids.append(case_id)
            elif repair_payload.get("repaired") and not validation_after.get("valid", False):
                unrepairable_cases += 1
                unrepairable_case_ids.append(case_id)
                failure_reasons.append("workflow remains invalid after repair.")
            else:
                unrepaired_cases += 1
                unrepaired_case_ids.append(case_id)
                failure_reasons.append("workflow invalid and no repair applied.")

        if grounding_result.get("grounding_valid", True):
            execution = execute_workflow(workflow_final)
        else:
            execution = ExecutionResult(
                workflow_id=workflow_final.workflow_id,
                success=False,
                executed_calls=0,
                events=[],
                final_state={},
                state_snapshots=[],
            )

        api_calls = workflow_final.api_calls
        api_names = [call.api for call in api_calls]

        parsing_matched, parsing_total, parsing_failures = _evaluate_parsed_steps(
            parsed.model_dump(), case.get("expected_parsed_steps", [])
        )
        parsing_counter.update(parsing_matched, parsing_total)

        grounding_matched, grounding_total, grounding_failures = _evaluate_grounding(
            api_names,
            case.get("expected_actions", []),
            case.get("must_not_include", []),
        )
        grounding_counter.update(grounding_matched, grounding_total)
        failure_reasons.extend(grounding_failures)

        sequence_matched, sequence_total, sequence_failures = _evaluate_sequence(
            api_names, case.get("expected_workflow_sequence", [])
        )
        sequence_counter.update(sequence_matched, sequence_total)
        failure_reasons.extend(sequence_failures)

        parameter_matched, parameter_total, parameter_failures = _evaluate_parameters(
            api_calls, case.get("expected_parameters", [])
        )
        parameter_counter.update(parameter_matched, parameter_total)

        final_state_matched, final_state_total, final_state_failures = _evaluate_final_state(
            execution.final_state, case.get("expected_final_state", {})
        )

        execution_check = execution.success == expected_success

        precondition_violation_count += sum(
            1 for event in execution.events if "PreconditionViolation" in event.message
        )

        checks = {
            "parsing_check": parsing_matched == parsing_total,
            "grounding_check": grounding_matched == grounding_total,
            "sequence_check": sequence_matched == sequence_total,
            "parameter_check": parameter_matched == parameter_total,
            "final_state_check": final_state_matched == final_state_total,
            "validation_after_check": validation_after.get("valid", True),
            "execution_check": execution_check,
        }
        workflow_match = (grounding_matched == grounding_total) and (sequence_matched == sequence_total)
        success = workflow_match

        details = {
            "execution_success": execution.success,
            "api_calls": api_names,
            "executed_calls": execution.executed_calls,
            "validation_before": validation_before,
            "repair_result": {
                "repaired": repair_payload.get("repaired", False),
                "applied_repairs": repair_payload.get("applied_repairs", []),
            },
            "validation_after": validation_after,
            "grounding_result": grounding_result,
            "grounding_validation_result": grounding_validation_result,
            "llm_repair_result": llm_repair_result,
            "llm_parser_result": llm_parser_result,
            "llm_validation_result": llm_validation_result,
            "metrics": {
                "parsing": {"matched": parsing_matched, "total": parsing_total},
                "grounding": {"matched": grounding_matched, "total": grounding_total},
                "sequence": {"matched": sequence_matched, "total": sequence_total},
                "parameter": {"matched": parameter_matched, "total": parameter_total},
                "final_state": {"matched": final_state_matched, "total": final_state_total},
            },
        }

        repair_debug_records.append(
            {
                "case_id": case_id,
                "repaired": repair_payload.get("repaired", False),
                "validation_before_issue_count": validation_before.get("issue_count", 0),
                "validation_after_issue_count": validation_after.get("issue_count", 0),
                "applied_repairs": repair_payload.get("applied_repairs", []),
                "remaining_issues_after_repair": validation_after.get("issues", []),
                "llm_invoked": llm_repair_result.get("llm_invoked", False),
                "llm_patch_accepted": llm_repair_result.get("llm_patch_accepted", False),
                "llm_failure_reason": llm_repair_result.get("llm_failure_reason"),
                "parser_backend_mode": llm_parser_result.get("parser_backend_mode", "rule_only"),
                "parser_fallback_used": llm_parser_result.get("llm_parser_fallback_used", False),
                "parser_failure_reason": llm_parser_result.get("llm_parser_failure_reason"),
            }
        )

        results.append(
            CaseResult(
                case_id=case_id,
                success=success,
                checks=checks,
                details=details,
                failure_reasons=failure_reasons,
            )
        )

        case_out = output_dir / case_id
        ensure_dir(case_out)
        dump_json(case_out / "parser_preprocess.json", preprocess_payload)
        dump_json(case_out / "llm_parser_result.json", llm_parser_result)
        dump_json(case_out / "parsed_protocol.json", parsed.model_dump())
        dump_json(case_out / "grounded_workflow.json", grounded.model_dump())
        dump_json(case_out / "workflow_before_repair.json", workflow_before_repair.model_dump())
        dump_json(case_out / "validation_before.json", validation_before)
        dump_json(case_out / "repair_result.json", details["repair_result"])
        dump_json(case_out / "validation_after.json", validation_after)
        dump_json(case_out / "workflow.json", workflow_final.model_dump())
        if llm_input_payload is not None:
            dump_json(case_out / "llm_input.json", llm_input_payload)
        if llm_parser_input_payload is not None:
            dump_json(case_out / "llm_parser_input.json", llm_parser_input_payload)
        if llm_parser_raw_output_payload is not None:
            dump_json(case_out / "llm_parser_raw_output.json", llm_parser_raw_output_payload)
        if llm_parser_parsed_output_payload is not None:
            dump_json(case_out / "llm_parser_parsed_output.json", llm_parser_parsed_output_payload)
        dump_json(case_out / "grounding_result.json", grounding_result)
        dump_json(case_out / "grounding_validation_result.json", grounding_validation_result)
        if llm_grounder_input_payload is not None:
            dump_json(case_out / "llm_grounder_input.json", llm_grounder_input_payload)
        if llm_grounder_raw_output_payload is not None:
            dump_json(case_out / "llm_grounder_raw_output.json", llm_grounder_raw_output_payload)
        if llm_grounder_parsed_output_payload is not None:
            dump_json(case_out / "llm_grounder_parsed_output.json", llm_grounder_parsed_output_payload)
        if llm_raw_output_payload is not None:
            dump_json(case_out / "llm_raw_output.json", llm_raw_output_payload)
        if llm_parsed_output_payload is not None:
            dump_json(case_out / "llm_parsed_output.json", llm_parsed_output_payload)
        if workflow_before_llm_patch_payload is not None:
            dump_json(case_out / "workflow_before_llm_patch.json", workflow_before_llm_patch_payload)
        if workflow_after_llm_patch_payload is not None:
            dump_json(case_out / "workflow_after_llm_patch.json", workflow_after_llm_patch_payload)
        dump_json(case_out / "llm_patch_result.json", llm_repair_result)
        dump_json(case_out / "llm_validation_result.json", llm_validation_result)
        dump_json(case_out / "execution_result.json", execution.model_dump(mode="json"))
        dump_json(
            case_out / "case_result.json",
            {
                "case_id": case_id,
                "success": success,
                "checks": checks,
                "failure_reasons": failure_reasons,
                "details": details,
            },
        )

    total = len(results)
    passed = sum(1 for r in results if r.success)
    exec_success = sum(1 for r in results if r.details["execution_success"])

    summary = {
        "total_cases": total,
        "passed_cases": passed,
        "parsing_accuracy": parsing_counter.rate(),
        "grounding_accuracy": grounding_counter.rate(),
        "parameter_accuracy": parameter_counter.rate(),
        "sequence_accuracy": sequence_counter.rate(),
        "executability_rate": (exec_success / total) if total else 0.0,
        "pass_rate": (passed / total) if total else 0.0,
        "precondition_violation_count": precondition_violation_count,
        "repair_stats": {
            "repaired_cases": repaired_cases,
            "unrepaired_cases": unrepaired_cases,
            "unrepairable_cases": unrepairable_cases,
            "repaired_case_ids": repaired_case_ids,
            "unrepaired_case_ids": unrepaired_case_ids,
            "unrepairable_case_ids": unrepairable_case_ids,
        },
        "llm_stats": {
            "llm_invoked_cases": llm_invoked_cases,
            "llm_repaired_cases": llm_repaired_cases,
            "llm_repair_success_rate": (llm_repaired_cases / llm_invoked_cases) if llm_invoked_cases else 0.0,
            "post_llm_validation_pass_cases": post_llm_validation_pass_cases,
            "avg_remaining_issues_before_llm": (llm_before_issue_sum / llm_invoked_cases) if llm_invoked_cases else 0.0,
            "avg_remaining_issues_after_llm": (llm_after_issue_sum / llm_invoked_cases) if llm_invoked_cases else 0.0,
        },
        "parser_llm_stats": {
            "parser_llm_invoked_cases": parser_llm_invoked_cases,
            "parser_llm_accept_rate": (parser_llm_accepted_cases / parser_llm_invoked_cases) if parser_llm_invoked_cases else 0.0,
            "parser_llm_fallback_rate": (parser_llm_fallback_cases / parser_llm_invoked_cases) if parser_llm_invoked_cases else 0.0,
            "parser_llm_schema_fail_cases": parser_llm_schema_fail_cases,
            "parser_failure_reason_counts": parser_failure_reason_counts,
        },
        "grounding_llm_stats": {
            "grounding_llm_invoked_cases": grounding_llm_invoked_cases,
            "grounding_llm_success_rate": (grounding_llm_success_cases / grounding_llm_invoked_cases) if grounding_llm_invoked_cases else 0.0,
            "unregistered_api_case_count": grounding_unregistered_api_cases,
            "grounding_failure_reason_counts": grounding_failure_reason_counts,
        },
        "cases": [
            {
                "case_id": r.case_id,
                "success": r.success,
                "checks": r.checks,
                "failure_reasons": r.failure_reasons,
                "metrics": r.details["metrics"],
                "executed_calls": r.details["executed_calls"],
                "execution_success": r.details["execution_success"],
                "validation_before_issue_count": r.details["validation_before"].get("issue_count", 0),
                "validation_after_issue_count": r.details["validation_after"].get("issue_count", 0),
                "repaired": r.details["repair_result"].get("repaired", False),
                "applied_repairs": r.details["repair_result"].get("applied_repairs", []),
                "llm_invoked": r.details.get("llm_repair_result", {}).get("llm_invoked", False),
                "llm_output_valid_json": r.details.get("llm_repair_result", {}).get("llm_output_valid_json", False),
                "llm_patch_applied": r.details.get("llm_repair_result", {}).get("llm_patch_applied", False),
                "llm_patch_accepted": r.details.get("llm_repair_result", {}).get("llm_patch_accepted", False),
                "llm_repair_success": r.details.get("llm_repair_result", {}).get("llm_repair_success", False),
                "llm_failure_reason": r.details.get("llm_repair_result", {}).get("llm_failure_reason"),
                "validation_before_llm_issue_count": r.details.get("llm_validation_result", {}).get("validation_before_llm_issue_count", 0),
                "validation_after_llm_issue_count": r.details.get("llm_validation_result", {}).get("validation_after_llm_issue_count", 0),
                "parser_backend_mode": r.details.get("llm_parser_result", {}).get("parser_backend_mode", "rule_only"),
                "llm_parser_invoked": r.details.get("llm_parser_result", {}).get("llm_parser_invoked", False),
                "llm_parser_valid_json": r.details.get("llm_parser_result", {}).get("llm_parser_valid_json", False),
                "llm_parser_schema_valid": r.details.get("llm_parser_result", {}).get("llm_parser_schema_valid", False),
                "llm_parser_accepted": r.details.get("llm_parser_result", {}).get("llm_parser_accepted", False),
                "llm_parser_fallback_used": r.details.get("llm_parser_result", {}).get("llm_parser_fallback_used", False),
                "llm_parser_failure_reason": r.details.get("llm_parser_result", {}).get("llm_parser_failure_reason"),
                "grounding_backend_mode": r.details.get("grounding_result", {}).get("grounding_backend_mode", "rule_only"),
                "grounding_valid": r.details.get("grounding_result", {}).get("grounding_valid", True),
                "contains_unregistered_api": r.details.get("grounding_result", {}).get("contains_unregistered_api", False),
                "grounding_failure_reason": r.details.get("grounding_result", {}).get("grounding_failure_reason"),
            }
            for r in results
        ],
    }

    dump_json(output_dir / "benchmark_summary.json", summary)
    dump_json(output_dir / "repair_debug.json", {"records": repair_debug_records})
    _write_summary_markdown(output_dir / "summary_report.md", summary)
    return summary


def _evaluate_parsed_steps(parsed: dict[str, Any], expected_steps: list[dict[str, Any]]) -> tuple[int, int, list[str]]:
    failures: list[str] = []
    matched = 0
    total = 0
    actual_steps = parsed.get("steps", [])

    for idx, expected in enumerate(expected_steps):
        if idx >= len(actual_steps):
            total += len(expected.keys())
            failures.append(f"parsed step missing at index {idx}")
            continue
        actual = actual_steps[idx]

        if "action" in expected:
            total += 1
            if actual.get("action") == expected["action"]:
                matched += 1
            else:
                failures.append(
                    f"parsed action mismatch at step {idx}: expected={expected['action']}, actual={actual.get('action')}"
                )

        for field in ("source", "target", "item"):
            if field in expected:
                total += 1
                actual_value = actual.get("entities", {}).get(field)
                if actual_value == expected[field]:
                    matched += 1
                else:
                    failures.append(
                        f"parsed entity mismatch at step {idx}.{field}: expected={expected[field]}, actual={actual_value}"
                    )

        if "parameters" in expected and isinstance(expected["parameters"], dict):
            for key, value in expected["parameters"].items():
                total += 1
                actual_value = actual.get("parameters", {}).get(key)
                if actual_value == value:
                    matched += 1
                else:
                    failures.append(
                        f"parsed parameter mismatch at step {idx}.{key}: expected={value}, actual={actual_value}"
                    )

    return matched, total, failures


def _evaluate_grounding(
    api_names: list[str], expected_actions: list[str], must_not_include: list[str]
) -> tuple[int, int, list[str]]:
    failures: list[str] = []
    matched = 0
    total = 0

    for action in expected_actions:
        total += 1
        if action in api_names:
            matched += 1
        else:
            failures.append(f"missing expected action: {action}")

    for action in must_not_include:
        total += 1
        if action not in api_names:
            matched += 1
        else:
            failures.append(f"found forbidden action: {action}")

    return matched, total, failures


def _evaluate_sequence(api_names: list[str], expected_sequence: list[str]) -> tuple[int, int, list[str]]:
    failures: list[str] = []
    matched = 0
    total = len(expected_sequence)
    if total == 0:
        return 0, 0, failures

    for idx, expected_api in enumerate(expected_sequence):
        actual_api = api_names[idx] if idx < len(api_names) else None
        if actual_api == expected_api:
            matched += 1
        else:
            failures.append(
                f"sequence mismatch at index {idx}: expected={expected_api}, actual={actual_api}"
            )
    return matched, total, failures


def _evaluate_parameters(api_calls: list[Any], expected_parameters: Any) -> tuple[int, int, list[str]]:
    failures: list[str] = []
    matched = 0
    total = 0
    parameter_specs = _normalize_parameter_specs(expected_parameters)

    for spec in parameter_specs:
        api = spec["api"]
        args = spec["args"]
        occurrence = spec["occurrence"]
        target_call = _find_api_call(api_calls, api, occurrence)

        for key, value in args.items():
            total += 1
            if target_call is None:
                failures.append(f"parameter check failed: api {api} occurrence {occurrence} not found")
                continue
            actual_value = target_call.args.get(key)
            if actual_value == value:
                matched += 1
            else:
                failures.append(
                    f"parameter mismatch for {api}.{key}: expected={value}, actual={actual_value}"
                )
    return matched, total, failures


def _normalize_parameter_specs(expected_parameters: Any) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []

    if isinstance(expected_parameters, dict):
        for api, args in expected_parameters.items():
            if isinstance(args, dict):
                specs.append({"api": api, "args": args, "occurrence": 1})
        return specs

    if isinstance(expected_parameters, list):
        for item in expected_parameters:
            if not isinstance(item, dict):
                continue
            api = item.get("api")
            args = item.get("args")
            if not isinstance(api, str) or not isinstance(args, dict):
                continue
            occurrence = item.get("occurrence", 1)
            if not isinstance(occurrence, int) or occurrence <= 0:
                occurrence = 1
            specs.append({"api": api, "args": args, "occurrence": occurrence})
    return specs


def _find_api_call(api_calls: list[Any], api: str, occurrence: int) -> Any | None:
    count = 0
    for call in api_calls:
        if call.api == api:
            count += 1
            if count == occurrence:
                return call
    return None


def _evaluate_final_state(final_state: dict[str, Any], expected_state: Any) -> tuple[int, int, list[str]]:
    failures: list[str] = []
    matched = 0
    total = 0

    expected_paths = _flatten_dict(expected_state)
    for path, expected_value in expected_paths.items():
        total += 1
        exists, actual_value = _get_by_path(final_state, path)
        if not exists:
            failures.append(f"final_state missing path: {path}")
            continue
        if actual_value == expected_value:
            matched += 1
        else:
            failures.append(
                f"final_state mismatch at {path}: expected={expected_value}, actual={actual_value}"
            )

    return matched, total, failures


def _flatten_dict(payload: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    flattened: dict[str, Any] = {}
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flattened.update(_flatten_dict(value, path))
        else:
            flattened[path] = value
    return flattened


def _get_by_path(payload: Any, path: str) -> tuple[bool, Any]:
    current = payload
    for token in path.split("."):
        if isinstance(current, dict):
            if token not in current:
                return False, None
            current = current[token]
            continue
        if isinstance(current, list) and token.isdigit():
            idx = int(token)
            if idx < 0 or idx >= len(current):
                return False, None
            current = current[idx]
            continue
        return False, None
    return True, current


def _write_summary_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Benchmark Summary",
        "",
        f"- Total Cases: {summary['total_cases']}",
        f"- Passed Cases: {summary['passed_cases']}",
        f"- Parsing Accuracy: {summary['parsing_accuracy']:.2%}",
        f"- Grounding Accuracy: {summary['grounding_accuracy']:.2%}",
        f"- Parameter Accuracy: {summary['parameter_accuracy']:.2%}",
        f"- Sequence Accuracy: {summary['sequence_accuracy']:.2%}",
        f"- Executability Rate: {summary['executability_rate']:.2%}",
        f"- Pass Rate: {summary['pass_rate']:.2%}",
        f"- Precondition Violations: {summary['precondition_violation_count']}",
        f"- Repaired Cases: {summary['repair_stats']['repaired_cases']}",
        f"- Unrepaired Cases: {summary['repair_stats']['unrepaired_cases']}",
        f"- Unrepairable Cases: {summary['repair_stats']['unrepairable_cases']}",
        f"- Repaired IDs: {summary['repair_stats']['repaired_case_ids']}",
        f"- Unrepaired IDs: {summary['repair_stats']['unrepaired_case_ids']}",
        f"- Unrepairable IDs: {summary['repair_stats']['unrepairable_case_ids']}",
        f"- LLM Invoked Cases: {summary['llm_stats']['llm_invoked_cases']}",
        f"- LLM Repaired Cases: {summary['llm_stats']['llm_repaired_cases']}",
        f"- LLM Repair Success Rate: {summary['llm_stats']['llm_repair_success_rate']:.2%}",
        f"- Post LLM Validation Pass Cases: {summary['llm_stats']['post_llm_validation_pass_cases']}",
        f"- Avg Remaining Issues Before LLM: {summary['llm_stats']['avg_remaining_issues_before_llm']:.2f}",
        f"- Avg Remaining Issues After LLM: {summary['llm_stats']['avg_remaining_issues_after_llm']:.2f}",
        f"- Parser LLM Invoked Cases: {summary.get('parser_llm_stats', {}).get('parser_llm_invoked_cases', 0)}",
        f"- Parser LLM Accept Rate: {summary.get('parser_llm_stats', {}).get('parser_llm_accept_rate', 0.0):.2%}",
        f"- Parser LLM Fallback Rate: {summary.get('parser_llm_stats', {}).get('parser_llm_fallback_rate', 0.0):.2%}",
        f"- Parser LLM Schema Fail Cases: {summary.get('parser_llm_stats', {}).get('parser_llm_schema_fail_cases', 0)}",
        f"- Parser Failure Reasons: {summary.get('parser_llm_stats', {}).get('parser_failure_reason_counts', {})}",
        f"- Grounding LLM Invoked Cases: {summary.get('grounding_llm_stats', {}).get('grounding_llm_invoked_cases', 0)}",
        f"- Grounding LLM Success Rate: {summary.get('grounding_llm_stats', {}).get('grounding_llm_success_rate', 0.0):.2%}",
        f"- Unregistered API Case Count: {summary.get('grounding_llm_stats', {}).get('unregistered_api_case_count', 0)}",
        f"- Grounding Failure Reasons: {summary.get('grounding_llm_stats', {}).get('grounding_failure_reason_counts', {})}",
        "",
        "## Case Results",
        "",
        "| Case ID | Result | Repaired | LLM Invoked | LLM Accepted | Before Issues | After Issues | LLM Failure | Failure Reason |",
        "|---|---|---|---|---|---:|---:|---|---|",
    ]

    def _truncate(value: str, limit: int) -> str:
        text = value.replace("\n", " ").strip()
        if len(text) <= limit:
            return text
        return f"{text[: limit - 3]}..."

    for case in summary["cases"]:
        result = "PASS" if case["success"] else "FAIL"
        repaired = "yes" if case["repaired"] else "no"
        llm_invoked = "yes" if case.get("llm_invoked", False) else "no"
        llm_accepted = "yes" if case.get("llm_patch_accepted", False) else "no"
        llm_failure = _truncate(str(case.get("llm_failure_reason", "-")), 40).replace("|", "\\|")
        reason = "; ".join(case["failure_reasons"]) if case["failure_reasons"] else "-"
        reason = _truncate(reason, 100).replace("|", "\\|")
        lines.append(
            f"| {case['case_id']} | {result} | {repaired} | {llm_invoked} | {llm_accepted} | "
            f"{case['validation_before_llm_issue_count']} | {case['validation_after_llm_issue_count']} | "
            f"{llm_failure} | {reason} |"
        )

    lines.extend(
        [
            "",
            "## Parser LLM",
            "",
            "| Case ID | Backend Mode | Invoked | Accepted | Fallback | Valid JSON | Schema Valid | Failure Reason |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for case in summary["cases"]:
        backend_mode = str(case.get("parser_backend_mode", "rule_only"))
        invoked = "yes" if case.get("llm_parser_invoked", False) else "no"
        accepted = "yes" if case.get("llm_parser_accepted", False) else "no"
        fallback = "yes" if case.get("llm_parser_fallback_used", False) else "no"
        valid_json = "yes" if case.get("llm_parser_valid_json", False) else "no"
        schema_valid = "yes" if case.get("llm_parser_schema_valid", False) else "no"
        parser_failure = _truncate(str(case.get("llm_parser_failure_reason", "-")), 80).replace("|", "\\|")
        lines.append(
            f"| {case['case_id']} | {backend_mode} | {invoked} | {accepted} | {fallback} | "
            f"{valid_json} | {schema_valid} | {parser_failure} |"
        )
    lines.extend(
        [
            "",
            "## Grounding LLM",
            "",
            "| Case ID | Backend Mode | Grounding Valid | Unregistered API | Failure Reason |",
            "|---|---|---|---|---|",
        ]
    )
    for case in summary["cases"]:
        backend_mode = str(case.get("grounding_backend_mode", "rule_only"))
        g_valid = "yes" if case.get("grounding_valid", True) else "no"
        has_unreg = "yes" if case.get("contains_unregistered_api", False) else "no"
        g_reason = _truncate(str(case.get("grounding_failure_reason", "-")), 80).replace("|", "\\|")
        lines.append(
            f"| {case['case_id']} | {backend_mode} | {g_valid} | {has_unreg} | {g_reason} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


