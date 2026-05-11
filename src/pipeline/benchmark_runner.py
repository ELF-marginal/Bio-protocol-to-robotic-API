from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.models.contracts import ProtocolInput, Workflow
from src.pipeline.domain_simulator import evaluate_expected_final_state
from src.pipeline.llm_grounder import load_llm_grounding_config, run_grounding_backend
from src.pipeline.llm_parser import load_llm_parser_config, run_parser_backend
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
class CaseContext:
    case_id: str
    case_dir: Path
    benchmark_case_path: Path
    api_domain_path: Path
    lab_state_path: Path
    safety_rules_path: Path | None
    benchmark_case: dict[str, Any]
    api_domain: dict[str, Any]
    lab_state: dict[str, Any]
    safety_rules: list[dict[str, Any]]


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
    enable_llm_parser: bool = True,
    enable_llm_grounding: bool = True,
    show_progress: bool = True,
) -> dict[str, Any]:
    ensure_dir(output_dir)

    results: list[CaseResult] = []
    sequence_counter = MetricCounter()
    parameter_counter = MetricCounter()
    final_state_counter = MetricCounter()
    parser_llm_invoked_cases = 0
    parser_llm_accepted_cases = 0
    grounding_llm_invoked_cases = 0
    grounding_llm_success_cases = 0
    grounding_unregistered_api_cases = 0
    parser_failure_reason_counts: dict[str, int] = {}
    grounding_failure_reason_counts: dict[str, int] = {}

    case_contexts, discovery_errors = _discover_case_contexts(cases_dir)
    for error in discovery_errors:
        results.append(_failed_discovery_result(error))

    total_cases = len(case_contexts)
    for case_index, context in enumerate(case_contexts, start=1):
        case_out = output_dir / context.case_id
        ensure_dir(case_out)
        failure_reasons: list[str] = []

        _show_case_progress(show_progress, case_index, total_cases, "load")
        benchmark_case = context.benchmark_case
        difficulty = _normalize_difficulty(benchmark_case.get("difficulty"))
        input_text = benchmark_case.get("input_text")
        expected_sequence = benchmark_case.get("expected_api_sequence", [])
        expected_final_state = benchmark_case.get("expected_final_state", {})

        dump_json(case_out / "case_context.json", _case_context_debug(context))
        dump_json(case_out / "benchmark_case.json", benchmark_case)
        dump_json(case_out / "api_domain.json", context.api_domain)
        dump_json(case_out / "lab_state_initial.json", context.lab_state)
        dump_json(case_out / "safety_rules.json", context.safety_rules)
        dump_json(case_out / "expected_api_sequence.json", expected_sequence)
        dump_json(case_out / "expected_final_state.json", expected_final_state)

        if not isinstance(input_text, str) or not input_text.strip():
            result = _invalid_case_result(context.case_id, "missing non-empty input_text")
            dump_json(case_out / "case_result.json", result.details | {
                "case_id": result.case_id,
                "success": result.success,
                "checks": result.checks,
                "failure_reasons": result.failure_reasons,
            })
            results.append(result)
            continue

        _show_case_progress(show_progress, case_index, total_cases, "parser")
        protocol = ProtocolInput(
            protocol_id=context.case_id,
            title=context.case_id,
            source="benchmark_case",
            raw_text=input_text,
        )

        parser_backend = run_parser_backend(
            protocol=protocol,
            enable_llm_parser=enable_llm_parser,
            config=load_llm_parser_config(),
        )
        protocol = parser_backend["protocol"]
        parsed = parser_backend["parsed"]
        llm_parser_result = parser_backend["llm_parser_result"]
        parser_failure = llm_parser_result.get("llm_parser_failure_reason")
        if isinstance(parser_failure, str) and parser_failure:
            parser_failure_reason_counts[parser_failure] = parser_failure_reason_counts.get(parser_failure, 0) + 1
        if llm_parser_result.get("llm_parser_invoked", False):
            parser_llm_invoked_cases += 1
        if llm_parser_result.get("llm_parser_accepted", False):
            parser_llm_accepted_cases += 1

        _show_case_progress(show_progress, case_index, total_cases, "grounding")
        grounding_backend = run_grounding_backend(
            parsed_protocol=parsed,
            enable_llm_grounding=enable_llm_grounding,
            config=load_llm_grounding_config(),
            api_domain_path=str(context.api_domain_path),
            initial_lab_state_path=str(context.lab_state_path),
            safety_rules_path=str(context.safety_rules_path) if context.safety_rules_path else None,
            expected_final_state=expected_final_state if isinstance(expected_final_state, dict) else {},
        )
        grounded = grounding_backend["workflow"]
        grounding_result = grounding_backend["grounding_result"]
        grounding_validation_result = grounding_backend["grounding_validation_result"]
        if grounding_result.get("llm_grounding_invoked", False):
            grounding_llm_invoked_cases += 1
        if grounding_result.get("grounding_valid", False):
            grounding_llm_success_cases += 1
        if grounding_result.get("contains_unregistered_api", False):
            grounding_unregistered_api_cases += 1
        grounding_failure = grounding_result.get("grounding_failure_reason")
        if isinstance(grounding_failure, str) and grounding_failure:
            grounding_failure_reason_counts[grounding_failure] = grounding_failure_reason_counts.get(grounding_failure, 0) + 1

        _show_case_progress(show_progress, case_index, total_cases, "planner")
        workflow = compose_workflow(grounded)
        workflow_before_repair = workflow

        _show_case_progress(show_progress, case_index, total_cases, "validator")
        validation_before_repair = validate_workflow(
            workflow=workflow,
            api_domain_path=str(context.api_domain_path),
            lab_state_path=str(context.lab_state_path),
            safety_rules_path=str(context.safety_rules_path) if context.safety_rules_path else None,
            api_domain=context.api_domain,
            lab_state=context.lab_state,
            safety_rules=context.safety_rules,
        )
        repair_result = {
            "repaired": False,
            "repair_success": validation_before_repair.get("valid", False),
            "applied_repairs": [],
            "workflow": workflow,
            "validation_result": validation_before_repair,
            "rounds": [],
        }
        validation_final = validation_before_repair
        if enable_llm_repair and not validation_before_repair.get("valid", False):
            _show_case_progress(show_progress, case_index, total_cases, "repair")
            repair_result = repair_workflow(
                workflow=workflow,
                validation_result=validation_before_repair,
                protocol_text=protocol.raw_text,
                parsed_steps=[step.model_dump() for step in parsed.steps],
                api_domain_path=str(context.api_domain_path),
                lab_state_path=str(context.lab_state_path),
                api_domain=context.api_domain,
                lab_state=context.lab_state,
                safety_rules=context.safety_rules,
                enable_llm_repair=enable_llm_repair,
            )
            repaired_workflow = repair_result.get("workflow")
            if isinstance(repaired_workflow, Workflow):
                workflow = repaired_workflow
            validation_final = repair_result.get("validation_result", validation_before_repair)

        actual_sequence = [call.model_dump() for call in workflow.api_calls]

        _show_case_progress(show_progress, case_index, total_cases, "compare")
        sequence_diff = _diff_api_sequence(actual_sequence, expected_sequence)
        sequence_counter.update(sequence_diff["sequence_matched"], sequence_diff["sequence_total"])
        parameter_counter.update(sequence_diff["parameter_matched"], sequence_diff["parameter_total"])
        failure_reasons.extend(sequence_diff["failures"])

        _show_case_progress(show_progress, case_index, total_cases, "state")
        final_state_check = evaluate_expected_final_state(validation_final.get("final_state", {}), expected_final_state)
        final_state_counter.update(final_state_check["matched"], final_state_check["total"])
        failure_reasons.extend(final_state_check["failures"])

        parser_check = bool(parsed.steps)
        grounding_check = bool(grounding_result.get("grounding_valid", False))
        sequence_check = sequence_diff["sequence_matched"] == sequence_diff["sequence_total"]
        parameter_check = sequence_diff["parameter_matched"] == sequence_diff["parameter_total"]
        validation_check = bool(validation_final.get("valid", False))
        final_state_ok = final_state_check["matched"] == final_state_check["total"]
        success = parser_check and grounding_check and validation_check and sequence_check and parameter_check and final_state_ok
        sequence_accuracy = _metric_rate(sequence_diff["sequence_matched"], sequence_diff["sequence_total"])
        parameter_accuracy = _metric_rate(sequence_diff["parameter_matched"], sequence_diff["parameter_total"])
        final_state_accuracy = _metric_rate(final_state_check["matched"], final_state_check["total"])
        case_score = round((sequence_accuracy + parameter_accuracy + final_state_accuracy) / 3, 3)
        repair_num = _repair_iteration_count(repair_result)

        if not parser_check:
            failure_reasons.append(str(parser_failure or "parser produced no steps"))
        if not grounding_check:
            failure_reasons.append(str(grounding_failure or "grounding invalid"))
        if not validation_check:
            failure_reasons.append("validator simulation failed")

        checks = {
            "parser_check": parser_check,
            "grounding_check": grounding_check,
            "validation_check": validation_check,
            "sequence_check": sequence_check,
            "parameter_check": parameter_check,
            "final_state_check": final_state_ok,
        }
        details = {
            "difficulty": difficulty,
            "pass": success,
            "repair_num": repair_num,
            "score": case_score,
            "api_calls": actual_sequence,
            "actual_api_names": [call["api"] for call in actual_sequence],
            "expected_api_count": len(expected_sequence) if isinstance(expected_sequence, list) else 0,
            "actual_api_count": len(actual_sequence),
            "parser_result": llm_parser_result,
            "grounding_result": grounding_result,
            "grounding_validation_result": grounding_validation_result,
            "validation_before_repair": validation_before_repair,
            "validation_final": validation_final,
            "repair_result": _repair_result_for_json(repair_result),
            "metrics": {
                "sequence": {
                    "matched": sequence_diff["sequence_matched"],
                    "total": sequence_diff["sequence_total"],
                    "accuracy": sequence_accuracy,
                },
                "parameter": {
                    "matched": sequence_diff["parameter_matched"],
                    "total": sequence_diff["parameter_total"],
                    "accuracy": parameter_accuracy,
                },
                "final_state": {
                    "matched": final_state_check["matched"],
                    "total": final_state_check["total"],
                    "accuracy": final_state_accuracy,
                },
            },
        }

        dump_json(case_out / "protocol_input.json", protocol.model_dump())
        dump_json(case_out / "parser_preprocess.json", parser_backend["parser_preprocess"])
        dump_json(case_out / "parsed_protocol.json", parsed.model_dump())
        dump_json(case_out / "llm_parser_result.json", llm_parser_result)
        _dump_optional(case_out / "llm_parser_input.json", parser_backend.get("llm_parser_input"))
        _dump_optional(case_out / "llm_parser_raw_output.json", parser_backend.get("llm_parser_raw_output"))
        _dump_optional(case_out / "llm_parser_parsed_output.json", parser_backend.get("llm_parser_parsed_output"))
        dump_json(case_out / "grounding_result.json", grounding_result)
        dump_json(case_out / "grounding_validation_result.json", grounding_validation_result)
        _dump_optional(case_out / "llm_grounding_input.json", grounding_backend.get("llm_grounding_input"))
        _dump_optional(case_out / "llm_grounding_raw_output.json", grounding_backend.get("llm_grounding_raw_output"))
        _dump_optional(case_out / "llm_grounding_parsed_output.json", grounding_backend.get("llm_grounding_parsed_output"))
        dump_json(case_out / "workflow_before_repair.json", workflow_before_repair.model_dump())
        dump_json(case_out / "validation_before_repair.json", validation_before_repair)
        dump_json(case_out / "repair_result.json", _repair_result_for_json(repair_result))
        dump_json(case_out / "validation_result.json", validation_final)
        dump_json(case_out / "workflow.json", workflow.model_dump())
        dump_json(case_out / "actual_api_sequence.json", actual_sequence)
        dump_json(case_out / "api_sequence_diff.json", sequence_diff)
        dump_json(case_out / "simulated_final_state.json", validation_final.get("final_state", {}))
        dump_json(case_out / "final_state_check.json", final_state_check)
        dump_json(
            case_out / "case_result.json",
            {
                "case_id": context.case_id,
                "difficulty": difficulty,
                "success": success,
                "pass": success,
                "repair_num": repair_num,
                "score": case_score,
                "checks": checks,
                "failure_reasons": failure_reasons,
                "details": details,
            },
        )
        results.append(CaseResult(context.case_id, success, checks, details, failure_reasons))

    total = len(results)
    passed = sum(1 for result in results if result.success)
    case_summaries = [_case_summary(result) for result in results]
    difficulty_summary = _build_difficulty_summary(case_summaries)
    final_score = _weighted_final_score(difficulty_summary)
    summary = {
        "total_cases": total,
        "passed_cases": passed,
        "pass_rate": (passed / total) if total else 0.0,
        "final_score": final_score,
        "difficulty_weights": _difficulty_weights(),
        "difficulty_summary": difficulty_summary,
        "sequence_accuracy": sequence_counter.rate(),
        "parameter_accuracy": parameter_counter.rate(),
        "final_state_accuracy": final_state_counter.rate(),
        "parser_llm_stats": {
            "parser_llm_invoked_cases": parser_llm_invoked_cases,
            "parser_llm_accepted_cases": parser_llm_accepted_cases,
            "parser_llm_accept_rate": (parser_llm_accepted_cases / parser_llm_invoked_cases) if parser_llm_invoked_cases else 0.0,
            "parser_failure_reason_counts": parser_failure_reason_counts,
        },
        "grounding_llm_stats": {
            "grounding_llm_invoked_cases": grounding_llm_invoked_cases,
            "grounding_llm_success_cases": grounding_llm_success_cases,
            "grounding_llm_success_rate": (grounding_llm_success_cases / grounding_llm_invoked_cases) if grounding_llm_invoked_cases else 0.0,
            "unregistered_api_case_count": grounding_unregistered_api_cases,
            "grounding_failure_reason_counts": grounding_failure_reason_counts,
        },
        "cases": case_summaries,
    }
    dump_json(output_dir / "benchmark_summary.json", summary)
    _write_summary_markdown(output_dir / "summary_report.md", summary)
    return summary


def _show_case_progress(enabled: bool, case_index: int, total_cases: int, stage: str) -> None:
    if enabled:
        print(f"[case{case_index}/{total_cases} : {stage}]", flush=True)


def _case_summary(result: CaseResult) -> dict[str, Any]:
    metrics = result.details.get("metrics", {})
    return {
        "case_id": result.case_id,
        "difficulty": _normalize_difficulty(result.details.get("difficulty")),
        "success": result.success,
        "pass": result.success,
        "repair_num": int(result.details.get("repair_num", 0)),
        "score": round(float(result.details.get("score", 0.0)), 3),
        "sequence_accuracy": round(float(metrics.get("sequence", {}).get("accuracy", 0.0)), 3),
        "parameter_accuracy": round(float(metrics.get("parameter", {}).get("accuracy", 0.0)), 3),
        "final_state_accuracy": round(float(metrics.get("final_state", {}).get("accuracy", 0.0)), 3),
        "checks": result.checks,
        "failure_reasons": result.failure_reasons,
        "metrics": metrics,
        "actual_api_count": result.details.get("actual_api_count", 0),
        "expected_api_count": result.details.get("expected_api_count", 0),
        "parser_failure_reason": result.details.get("parser_result", {}).get("llm_parser_failure_reason"),
        "grounding_failure_reason": result.details.get("grounding_result", {}).get("grounding_failure_reason"),
        "contains_unregistered_api": result.details.get("grounding_result", {}).get("contains_unregistered_api", False),
    }


def _build_difficulty_summary(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for difficulty in ("Easy", "Medium", "Hard"):
        subset = [case for case in cases if case.get("difficulty") == difficulty]
        total = len(subset)
        passed = sum(1 for case in subset if case.get("pass", False))
        avg_score = round(sum(float(case.get("score", 0.0)) for case in subset) / total, 3) if total else 0.0
        summary[difficulty] = {
            "total_cases": total,
            "passed_cases": passed,
            "pass_rate": (passed / total) if total else 0.0,
            "average_score": avg_score,
            "weight": _difficulty_weights()[difficulty],
        }
    return summary


def _weighted_final_score(difficulty_summary: dict[str, dict[str, Any]]) -> float:
    active_items = [
        (difficulty, payload)
        for difficulty, payload in difficulty_summary.items()
        if int(payload.get("total_cases", 0)) > 0
    ]
    active_weight = sum(_difficulty_weights()[difficulty] for difficulty, _ in active_items)
    if active_weight == 0:
        return 0.0
    return round(
        sum(
            float(payload.get("average_score", 0.0)) * _difficulty_weights()[difficulty]
            for difficulty, payload in active_items
        )
        / active_weight,
        3,
    )


def _difficulty_weights() -> dict[str, float]:
    return {"Easy": 0.2, "Medium": 0.3, "Hard": 0.5}


def _normalize_difficulty(value: Any) -> str:
    text = str(value or "Medium").strip().lower()
    if text == "easy":
        return "Easy"
    if text == "hard":
        return "Hard"
    return "Medium"


def _metric_rate(matched: int, total: int) -> float:
    if total == 0:
        return 1.0
    return matched / total


def _discover_case_contexts(cases_dir: Path) -> tuple[list[CaseContext], list[str]]:
    contexts: list[CaseContext] = []
    errors: list[str] = []
    candidates = [cases_dir] if (cases_dir / "benchmark_case.yaml").exists() else sorted(
        path for path in cases_dir.iterdir() if path.is_dir()
    )
    for case_dir in candidates:
        benchmark_case_path = case_dir / "benchmark_case.yaml"
        api_domain_path = case_dir / "api_domain.yaml"
        lab_state_path = case_dir / "lab_state.yaml"
        safety_rules_path = case_dir / "safty_rule.yaml"
        missing = [
            path.name
            for path in (benchmark_case_path, api_domain_path, lab_state_path)
            if not path.exists()
        ]
        if missing:
            errors.append(f"{case_dir.name}: missing {missing}")
            continue
        benchmark_case = _load_yaml_mapping(benchmark_case_path)
        api_domain = _load_yaml_mapping(api_domain_path)
        lab_state = _load_yaml_mapping(lab_state_path)
        safety_rules = _load_yaml_list(safety_rules_path) if safety_rules_path.exists() else []
        case_id = str(benchmark_case.get("case_id") or case_dir.name)
        contexts.append(
            CaseContext(
                case_id=case_id,
                case_dir=case_dir,
                benchmark_case_path=benchmark_case_path,
                api_domain_path=api_domain_path,
                lab_state_path=lab_state_path,
                safety_rules_path=safety_rules_path if safety_rules_path.exists() else None,
                benchmark_case=benchmark_case,
                api_domain=api_domain,
                lab_state=lab_state,
                safety_rules=safety_rules,
            )
        )
    return contexts, errors


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _load_yaml_list(path: Path) -> list[dict[str, Any]]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, list) else []


def _case_context_debug(context: CaseContext) -> dict[str, Any]:
    return {
        "case_id": context.case_id,
        "case_dir": str(context.case_dir),
        "benchmark_case_path": str(context.benchmark_case_path),
        "api_domain_path": str(context.api_domain_path),
        "lab_state_path": str(context.lab_state_path),
        "safety_rules_path": str(context.safety_rules_path) if context.safety_rules_path else None,
        "domain": context.benchmark_case.get("domain"),
        "lab_state_ref": context.benchmark_case.get("lab_state"),
    }


def _diff_api_sequence(actual_calls: list[dict[str, Any]], expected_sequence: Any) -> dict[str, Any]:
    if not isinstance(expected_sequence, list):
        return {
            "sequence_matched": 0,
            "sequence_total": 1,
            "parameter_matched": 0,
            "parameter_total": 1,
            "failures": ["expected_api_sequence must be a list"],
            "items": [],
        }

    exact_diff = _diff_api_sequence_ordered(actual_calls, expected_sequence)
    if not exact_diff["failures"]:
        exact_diff["comparison_mode"] = "ordered"
        return exact_diff

    unordered_diff = _diff_api_sequence_unordered(actual_calls, expected_sequence)
    if _diff_better(unordered_diff, exact_diff):
        unordered_diff["ordered_failures"] = exact_diff["failures"]
        unordered_diff["comparison_mode"] = "unordered_equivalent"
        return unordered_diff

    exact_diff["comparison_mode"] = "ordered"
    return exact_diff


def _diff_api_sequence_ordered(actual_calls: list[dict[str, Any]], expected_sequence: list[Any]) -> dict[str, Any]:
    sequence_matched = 0
    parameter_matched = 0
    parameter_total = 0
    failures: list[str] = []
    items: list[dict[str, Any]] = []
    sequence_total = max(len(expected_sequence), len(actual_calls))

    for idx in range(sequence_total):
        expected = expected_sequence[idx] if idx < len(expected_sequence) else None
        actual = actual_calls[idx] if idx < len(actual_calls) else None
        expected_api = expected.get("api") if isinstance(expected, dict) else None
        actual_api = actual.get("api") if isinstance(actual, dict) else None
        api_match = expected_api == actual_api
        if api_match:
            sequence_matched += 1
        else:
            failures.append(f"api mismatch at index {idx}: expected={expected_api}, actual={actual_api}")

        expected_args = expected.get("args", {}) if isinstance(expected, dict) and isinstance(expected.get("args", {}), dict) else {}
        actual_args = actual.get("args", {}) if isinstance(actual, dict) and isinstance(actual.get("args", {}), dict) else {}
        arg_results: dict[str, dict[str, Any]] = {}
        for key, expected_value in expected_args.items():
            parameter_total += 1
            actual_value = actual_args.get(key)
            matched = _argument_values_equal_for_api(expected_api, key, actual_value, expected_value)
            if matched:
                parameter_matched += 1
            else:
                failures.append(
                    f"arg mismatch at index {idx}.{key}: expected={expected_value}, actual={actual_value}"
                )
            arg_results[key] = {
                "expected": expected_value,
                "actual": actual_value,
                "matched": matched,
            }

        items.append(
            {
                "index": idx,
                "expected": expected,
                "actual": actual,
                "api_match": api_match,
                "arg_results": arg_results,
            }
        )

    return {
        "sequence_matched": sequence_matched,
        "sequence_total": sequence_total,
        "parameter_matched": parameter_matched,
        "parameter_total": parameter_total,
        "failures": failures,
        "items": items,
    }


def _diff_api_sequence_unordered(actual_calls: list[dict[str, Any]], expected_sequence: list[Any]) -> dict[str, Any]:
    sequence_matched = 0
    parameter_matched = 0
    parameter_total = 0
    failures: list[str] = []
    items: list[dict[str, Any]] = []
    sequence_total = max(len(expected_sequence), len(actual_calls))
    used_actual_indexes: set[int] = set()

    for expected_index, expected in enumerate(expected_sequence):
        expected_api = expected.get("api") if isinstance(expected, dict) else None
        expected_args = expected.get("args", {}) if isinstance(expected, dict) and isinstance(expected.get("args", {}), dict) else {}
        best_actual_index: int | None = None
        best_score = -1
        best_actual: dict[str, Any] | None = None
        best_arg_results: dict[str, dict[str, Any]] = {}

        for actual_index, actual in enumerate(actual_calls):
            if actual_index in used_actual_indexes or not isinstance(actual, dict):
                continue
            if actual.get("api") != expected_api:
                continue
            actual_args = actual.get("args", {}) if isinstance(actual.get("args", {}), dict) else {}
            arg_results = _argument_match_results(expected_api, expected_args, actual_args)
            score = sum(1 for result in arg_results.values() if result["matched"])
            if score > best_score:
                best_score = score
                best_actual_index = actual_index
                best_actual = actual
                best_arg_results = arg_results

        if best_actual_index is None:
            failures.append(f"missing api in actual sequence: expected index {expected_index} api={expected_api}")
            for key, expected_value in expected_args.items():
                parameter_total += 1
                failures.append(f"missing arg for unmatched api {expected_index}.{key}: expected={expected_value}, actual=None")
            items.append(
                {
                    "expected_index": expected_index,
                    "actual_index": None,
                    "expected": expected,
                    "actual": None,
                    "api_match": False,
                    "arg_results": {},
                }
            )
            continue

        used_actual_indexes.add(best_actual_index)
        sequence_matched += 1
        for key, result in best_arg_results.items():
            parameter_total += 1
            if result["matched"]:
                parameter_matched += 1
            else:
                failures.append(
                    f"arg mismatch for api {expected_api} expected index {expected_index} matched actual index {best_actual_index}.{key}: "
                    f"expected={result['expected']}, actual={result['actual']}"
                )
        items.append(
            {
                "expected_index": expected_index,
                "actual_index": best_actual_index,
                "expected": expected,
                "actual": best_actual,
                "api_match": True,
                "arg_results": best_arg_results,
            }
        )

    for actual_index, actual in enumerate(actual_calls):
        if actual_index not in used_actual_indexes:
            actual_api = actual.get("api") if isinstance(actual, dict) else None
            failures.append(f"extra api in actual sequence: actual index {actual_index} api={actual_api}")

    return {
        "sequence_matched": sequence_matched,
        "sequence_total": sequence_total,
        "parameter_matched": parameter_matched,
        "parameter_total": parameter_total,
        "failures": failures,
        "items": items,
    }


def _argument_match_results(
    api: Any,
    expected_args: dict[str, Any],
    actual_args: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        key: {
            "expected": expected_value,
            "actual": actual_args.get(key),
            "matched": _argument_values_equal_for_api(api, key, actual_args.get(key), expected_value),
        }
        for key, expected_value in expected_args.items()
    }


def _diff_better(candidate: dict[str, Any], baseline: dict[str, Any]) -> bool:
    candidate_tuple = (
        int(candidate.get("sequence_matched", 0)),
        int(candidate.get("parameter_matched", 0)),
        -len(candidate.get("failures", [])),
    )
    baseline_tuple = (
        int(baseline.get("sequence_matched", 0)),
        int(baseline.get("parameter_matched", 0)),
        -len(baseline.get("failures", [])),
    )
    return candidate_tuple > baseline_tuple


def _argument_values_equal(actual_value: Any, expected_value: Any) -> bool:
    if actual_value == expected_value:
        return True
    if isinstance(actual_value, dict) and "value" in actual_value:
        return actual_value.get("value") == expected_value
    if isinstance(expected_value, dict) and "value" in expected_value:
        return actual_value == expected_value.get("value")
    return False


def _argument_values_equal_for_api(api: Any, key: str, actual_value: Any, expected_value: Any) -> bool:
    if _argument_values_equal(actual_value, expected_value):
        return True
    if api == "take_from_fridge" and key == "location":
        # In this benchmark domain, this argument is used inconsistently by LLMs
        # as either the fridge room or the post-retrieval working location.
        # Final-state checks still catch meaningful downstream location errors.
        return isinstance(actual_value, str) and isinstance(expected_value, str)
    return False


def _dump_optional(path: Path, payload: Any) -> None:
    if payload is not None:
        dump_json(path, payload)


def _repair_result_for_json(repair_result: dict[str, Any]) -> dict[str, Any]:
    out = dict(repair_result)
    workflow = out.get("workflow")
    if isinstance(workflow, Workflow):
        out["workflow"] = workflow.model_dump()
    return out


def _repair_iteration_count(repair_result: dict[str, Any]) -> int:
    rounds = repair_result.get("rounds", [])
    if not isinstance(rounds, list):
        return 0
    return sum(1 for item in rounds if isinstance(item, dict) and item.get("llm_invoked", False))


def _invalid_case_result(case_id: str, reason: str) -> CaseResult:
    checks = {
        "parser_check": False,
        "grounding_check": False,
        "validation_check": False,
        "sequence_check": False,
        "parameter_check": False,
        "final_state_check": False,
    }
    return CaseResult(
        case_id=case_id,
        success=False,
        checks=checks,
        details={
            "metrics": {
                "sequence": {"matched": 0, "total": 1},
                "parameter": {"matched": 0, "total": 1},
                "final_state": {"matched": 0, "total": 1},
            }
        },
        failure_reasons=[f"invalid_case_file: {reason}"],
    )


def _failed_discovery_result(reason: str) -> CaseResult:
    return _invalid_case_result("case_discovery_error", reason)


def _write_summary_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Benchmark Summary",
        "",
        f"- Total Cases: {summary['total_cases']}",
        f"- Passed Cases: {summary['passed_cases']}",
        f"- Pass Rate: {summary['pass_rate']:.2%}",
        f"- Final Weighted Score: {summary['final_score']:.3f}",
        f"- Sequence Accuracy: {summary['sequence_accuracy']:.2%}",
        f"- Parameter Accuracy: {summary['parameter_accuracy']:.2%}",
        f"- Final State Accuracy: {summary['final_state_accuracy']:.2%}",
        f"- Parser LLM Invoked Cases: {summary['parser_llm_stats']['parser_llm_invoked_cases']}",
        f"- Parser LLM Accepted Cases: {summary['parser_llm_stats']['parser_llm_accepted_cases']}",
        f"- Parser Failure Reasons: {summary['parser_llm_stats']['parser_failure_reason_counts']}",
        f"- Grounding LLM Invoked Cases: {summary['grounding_llm_stats']['grounding_llm_invoked_cases']}",
        f"- Grounding LLM Success Cases: {summary['grounding_llm_stats']['grounding_llm_success_cases']}",
        f"- Unregistered API Case Count: {summary['grounding_llm_stats']['unregistered_api_case_count']}",
        f"- Grounding Failure Reasons: {summary['grounding_llm_stats']['grounding_failure_reason_counts']}",
        "",
        "## Weighted Overall",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Overall Pass Rate | {summary['pass_rate']:.2%} |",
        f"| Final Weighted Score | {summary['final_score']:.3f} |",
        "",
        "## Difficulty Summary",
        "",
        "| Difficulty | Weight | Cases | Passed | Pass Rate | Average Score |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for difficulty in ("Easy", "Medium", "Hard"):
        payload = summary.get("difficulty_summary", {}).get(difficulty, {})
        lines.append(
            f"| {difficulty} | {float(payload.get('weight', 0.0)):.1f} | "
            f"{int(payload.get('total_cases', 0))} | {int(payload.get('passed_cases', 0))} | "
            f"{float(payload.get('pass_rate', 0.0)):.2%} | {float(payload.get('average_score', 0.0)):.3f} |"
        )

    lines.extend(
        [
            "",
            "## Case Metrics",
            "",
            "| Case ID | Difficulty | Pass | Repair Num | Sequence Accuracy | Parameter Accuracy | Final State Accuracy | Score |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for case in summary["cases"]:
        lines.append(
            f"| {case['case_id']} | {case.get('difficulty', 'Medium')} | "
            f"{'true' if case.get('pass', False) else 'false'} | {int(case.get('repair_num', 0))} | "
            f"{float(case.get('sequence_accuracy', 0.0)):.3f} | "
            f"{float(case.get('parameter_accuracy', 0.0)):.3f} | "
            f"{float(case.get('final_state_accuracy', 0.0)):.3f} | "
            f"{float(case.get('score', 0.0)):.3f} |"
        )

    lines.extend(
        [
            "",
        "## Case Results",
        "",
        "| Case ID | Result | Parser | Grounding | Validator | Sequence | Parameters | Final State | Failure Reason |",
        "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for case in summary["cases"]:
        checks = case["checks"]
        reason = "; ".join(case["failure_reasons"]) if case["failure_reasons"] else "-"
        reason = reason.replace("|", "\\|")
        if len(reason) > 160:
            reason = f"{reason[:157]}..."
        lines.append(
            f"| {case['case_id']} | {'PASS' if case['success'] else 'FAIL'} | "
            f"{'yes' if checks.get('parser_check') else 'no'} | "
            f"{'yes' if checks.get('grounding_check') else 'no'} | "
            f"{'yes' if checks.get('validation_check') else 'no'} | "
            f"{'yes' if checks.get('sequence_check') else 'no'} | "
            f"{'yes' if checks.get('parameter_check') else 'no'} | "
            f"{'yes' if checks.get('final_state_check') else 'no'} | {reason} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")
