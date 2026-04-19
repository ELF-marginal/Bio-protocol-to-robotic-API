from __future__ import annotations

from pathlib import Path
from datetime import datetime
from uuid import uuid4

import typer
import yaml

from src.models.contracts import ExecutionResult, ProtocolInput
from src.pipeline.executor import execute_workflow
from src.pipeline.benchmark_runner import run_benchmark
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
from src.pipeline.llm_grounder import (
    load_llm_grounder_config,
    run_grounder_backend,
)
from src.pipeline.llm_planner import (
    load_llm_planner_config,
    run_planner_backend,
)
from src.pipeline.operation_orchestrator import (
    run_operation_grounder_pass,
    run_operation_parser_pass,
)
from src.pipeline.operation_splitter import split_operations
from src.pipeline.repair import repair_workflow
from src.pipeline.validator import validate_workflow
from src.pipeline.workflow_planner import compose_workflow
from src.utils.io import dump_json, ensure_dir

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def run(
    text: str | None = typer.Option(None, help="Raw protocol text."),
    file: Path | None = typer.Option(None, help="Protocol file path (.txt/.md)."),
    title: str = typer.Option("MVP Demo Protocol", help="Protocol title."),
    enable_validator: bool = typer.Option(True, help="Run rule-based validator before execution."),
    enable_repair: bool = typer.Option(True, help="Attempt one-pass auto repair when validation fails."),
    enable_llm_repair: bool = typer.Option(False, help="Enable LLM repair for unresolved issues after rule repair."),
    enable_llm_parser: bool = typer.Option(False, help="Enable LLM-primary parser (fallback to rule parser on failure)."),
    enable_llm_grounder: bool = typer.Option(
        False,
        "--enable-llm-grounder",
        "--enable-llm-grounding",
        help="Enable LLM-primary grounder (with unregistered API checks).",
    ),
    enable_llm_planner: bool = typer.Option(False, help="Enable LLM planner after operation-level grounding."),
    enable_operation_mode: bool = typer.Option(
        True,
        help="Split large protocol text by newline into operation groups and run parser/grounder per operation.",
    ),
) -> None:
    if bool(text) == bool(file):
        raise typer.BadParameter("Provide exactly one of --text or --file.")

    if file:
        if not file.exists():
            raise typer.BadParameter(f"File not found: {file}")
        if file.suffix.lower() not in {".txt", ".md"}:
            raise typer.BadParameter("Only .txt and .md are supported for --file.")
        raw_text = file.read_text(encoding="utf-8")
        source = "file_input"
    else:
        raw_text = text or ""
        source = "manual_input"

    protocol = ProtocolInput(
        protocol_id=f"demo_{uuid4().hex[:8]}",
        title=title,
        raw_text=raw_text,
        source=source,
    )
    operation_split_payload: list[dict] | None = None
    operation_parser_groups_payload: list[dict] | None = None
    operation_grounder_groups_payload: list[dict] | None = None
    operation_api_groups_payload: list[dict] | None = None

    parsed = None
    preprocess_payload: dict = {}
    llm_parser_result: dict = {"parser_backend_mode": "rule_only"}
    validation_result: dict = {"valid": True, "issue_count": 0, "issues": []}
    validation_before_rule_repair: dict = {"valid": True, "issue_count": 0, "issues": []}
    repair_result: dict = {"repaired": False, "applied_repairs": []}
    llm_repair_result: dict = {
        "llm_invoked": False,
        "llm_output_valid_json": False,
        "llm_patch_applied": False,
        "llm_patch_accepted": False,
        "llm_failure_reason": None,
    }
    llm_validation_result: dict = {
        "validation_before_llm_issue_count": 0,
        "validation_after_llm_issue_count": 0,
        "remaining_issues_after_llm": [],
    }
    llm_input_payload: dict | None = None
    llm_raw_output_payload: dict | None = None
    llm_parsed_output_payload: dict | None = None
    llm_parser_input_payload: dict | None = None
    llm_parser_raw_output_payload: dict | None = None
    llm_parser_parsed_output_payload: dict | None = None
    llm_grounder_input_payload: dict | None = None
    llm_grounder_raw_output_payload: dict | None = None
    llm_grounder_parsed_output_payload: dict | None = None
    llm_planner_input_payload: dict | None = None
    llm_planner_raw_output_payload: dict | None = None
    llm_planner_parsed_output_payload: dict | None = None
    grounding_result: dict = {
        "grounding_backend_mode": "rule_only",
        "grounding_valid": True,
        "contains_unregistered_api": False,
        "unregistered_apis": [],
        "grounding_failure_reason": None,
    }
    grounding_validation_result: dict = {
        "grounding_valid": True,
        "failure_reason": None,
        "contains_unregistered_api": False,
        "unregistered_apis": [],
        "issues": [],
    }
    planner_result: dict = {
        "planner_backend_mode": "disabled",
        "llm_planner_invoked": False,
        "llm_planner_accepted": False,
        "planner_valid": True,
        "contains_unregistered_api": False,
        "unregistered_apis": [],
        "planner_failure_reason": None,
    }
    planner_validation_result: dict = {
        "planner_valid": True,
        "failure_reason": None,
        "contains_unregistered_api": False,
        "unregistered_apis": [],
        "issues": [],
    }
    workflow_before_llm_patch_payload: dict | None = None
    workflow_after_llm_patch_payload: dict | None = None

    if enable_operation_mode:
        operation_split_payload = split_operations(protocol.raw_text)

        llm_parser_cfg = load_llm_parser_config()
        operation_parser_pass = run_operation_parser_pass(
            protocol=protocol,
            operations=operation_split_payload,
            enable_llm_parser=enable_llm_parser,
            parser_config=llm_parser_cfg,
        )
        operation_parser_groups_payload = operation_parser_pass["operation_parser_groups"]
        parsed = operation_parser_pass["flattened_parsed_protocol"]
        llm_parser_result = operation_parser_pass["aggregate_parser_result"]
        preprocess_payload = {
            "mode": "operation_splitter",
            "operation_count": len(operation_split_payload),
            "raw_length": len(protocol.raw_text),
        }

        llm_grounder_cfg = load_llm_grounder_config()
        operation_grounder_pass = run_operation_grounder_pass(
            protocol=protocol,
            operation_parser_groups=operation_parser_groups_payload,
            enable_llm_grounder=enable_llm_grounder,
            grounder_config=llm_grounder_cfg,
        )
        operation_grounder_groups_payload = operation_grounder_pass["operation_grounder_groups"]
        operation_api_groups_payload = operation_grounder_pass["operation_api_groups"]
        grounding_result = operation_grounder_pass["aggregate_grounding_result"]
        grounding_validation_result = operation_grounder_pass["aggregate_grounding_validation_result"]

        llm_planner_cfg = load_llm_planner_config()
        planner_backend = run_planner_backend(
            protocol_id=protocol.protocol_id,
            operations=operation_split_payload,
            operation_api_groups=operation_api_groups_payload,
            enable_llm_planner=enable_llm_planner,
            config=llm_planner_cfg,
        )
        grounded = planner_backend["workflow"]
        planner_result = planner_backend["planner_result"]
        planner_validation_result = planner_backend["planner_validation_result"]
        llm_planner_input_payload = planner_backend.get("llm_planner_input")
        llm_planner_raw_output_payload = planner_backend.get("llm_planner_raw_output")
        llm_planner_parsed_output_payload = planner_backend.get("llm_planner_parsed_output")

        grounding_result["grounding_backend_mode"] = "operation_grouped_planner"
        grounding_result["grounding_valid"] = bool(planner_result.get("planner_valid", True))
        grounding_result["contains_unregistered_api"] = bool(planner_result.get("contains_unregistered_api", False))
        grounding_result["unregistered_apis"] = list(planner_result.get("unregistered_apis", []))
        grounding_result["grounding_failure_reason"] = planner_result.get("planner_failure_reason")

        grounding_validation_result = {
            "grounding_valid": bool(planner_result.get("planner_valid", True)),
            "failure_reason": planner_result.get("planner_failure_reason"),
            "contains_unregistered_api": bool(planner_result.get("contains_unregistered_api", False)),
            "unregistered_apis": list(planner_result.get("unregistered_apis", [])),
            "issues": planner_validation_result.get("issues", []),
        }
        workflow = grounded
    else:
        llm_parser_cfg = load_llm_parser_config()
        parser_backend = run_parser_backend(
            protocol=protocol,
            enable_llm_parser=enable_llm_parser,
            config=llm_parser_cfg,
        )
        protocol = parser_backend["protocol"]
        parsed = parser_backend["parsed"]
        preprocess_payload = parser_backend["parser_preprocess"]
        llm_parser_result = parser_backend["llm_parser_result"]
        llm_parser_input_payload = parser_backend.get("llm_parser_input")
        llm_parser_raw_output_payload = parser_backend.get("llm_parser_raw_output")
        llm_parser_parsed_output_payload = parser_backend.get("llm_parser_parsed_output")

        llm_grounder_cfg = load_llm_grounder_config()
        grounder_backend = run_grounder_backend(
            parsed_protocol=parsed,
            enable_llm_grounder=enable_llm_grounder,
            config=llm_grounder_cfg,
            include_lab_state=True,
        )
        grounded = grounder_backend["workflow"]
        grounding_result = grounder_backend["grounding_result"]
        grounding_validation_result = grounder_backend["grounding_validation_result"]
        llm_grounder_input_payload = grounder_backend.get("llm_grounder_input")
        llm_grounder_raw_output_payload = grounder_backend.get("llm_grounder_raw_output")
        llm_grounder_parsed_output_payload = grounder_backend.get("llm_grounder_parsed_output")
        workflow = compose_workflow(grounded) if grounding_result.get("grounding_valid", True) else grounded

    if grounding_result.get("grounding_valid", True) and enable_validator:
        validation_before_rule_repair = validate_workflow(workflow)
        validation_result = validation_before_rule_repair
        if not validation_result.get("valid", True) and enable_repair:
            repair_payload = repair_workflow(workflow, validation_result)
            repair_result = {
                "repaired": repair_payload.get("repaired", False),
                "applied_repairs": repair_payload.get("applied_repairs", []),
            }
            if repair_payload.get("repaired") and repair_payload.get("workflow") is not None:
                workflow = repair_payload["workflow"]
                validation_result = validate_workflow(workflow)

        llm_cfg = load_llm_repair_config()
        remaining_issues = validation_result.get("issues", [])
        invoke, invoke_reason = should_invoke_llm_repair(
            enable_llm_repair=enable_llm_repair,
            config=llm_cfg,
            remaining_issues=remaining_issues,
        )
        if invoke:
            repair_api_registry_path = str(llm_cfg.get("api_registry_path", "configs/api_registry.yaml"))
            repair_initial_lab_state_path = str(llm_cfg.get("initial_lab_state_path", "configs/initial_lab_state.yaml"))
            repair_expected_lab_state_path = str(llm_cfg.get("expected_lab_state_path", "configs/initial_lab_state.yaml"))
            repair_notice_path = str(llm_cfg.get("notice_path", "configs/llm_repair_notice.txt"))
            llm_input_payload = build_llm_input(
                protocol_text=protocol.raw_text,
                parsed_steps=[step.model_dump() for step in parsed.steps],
                workflow_before_llm_repair=workflow,
                validation_issues_before_llm=validation_before_rule_repair.get("issues", []),
                applied_rule_repairs=repair_result.get("applied_repairs", []),
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

            llm_repair_result["llm_invoked"] = True
            raw_output = llm_invocation.get("raw_output", "")
            if llm_invocation.get("parsed_output") is not None:
                llm_parsed_output_payload = llm_invocation["parsed_output"]
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
                llm_repair_result["llm_output_valid_json"] = True
                api_registry = load_api_registry(path=repair_api_registry_path)
                ok_ops, ops_error = validate_operations_before_apply(
                    operations=llm_parsed_output_payload.get("operations", []),
                    workflow=workflow,
                    api_registry=api_registry,
                )
                if ok_ops:
                    workflow_before_llm_patch_payload = workflow.model_dump()
                    patched_workflow, patch_meta = apply_llm_operations(
                        workflow=workflow,
                        operations=llm_parsed_output_payload.get("operations", []),
                        api_registry=api_registry,
                    )
                    llm_repair_result["llm_patch_applied"] = bool(patch_meta.get("patch_applied", False))
                    llm_repair_result["operations_count"] = patch_meta.get("operations_count", 0)
                    llm_repair_result["applied_operations"] = patch_meta.get("applied_operations", [])

                    if patched_workflow is not None:
                        workflow_after_llm_patch_payload = patched_workflow.model_dump()
                        llm_validation_after = validate_workflow(patched_workflow)
                        llm_validation_result = {
                            "validation_before_llm_issue_count": len(remaining_issues),
                            "validation_after_llm_issue_count": llm_validation_after.get("issue_count", 0),
                            "remaining_issues_after_llm": llm_validation_after.get("issues", []),
                        }
                        if llm_validation_after.get("valid", False):
                            workflow = patched_workflow
                            validation_result = llm_validation_after
                            llm_repair_result["llm_patch_accepted"] = True
                            llm_repair_result["llm_failure_reason"] = None
                        else:
                            llm_repair_result["llm_patch_accepted"] = False
                            llm_repair_result["llm_failure_reason"] = "revalidate_failed_after_patch"
                    else:
                        llm_repair_result["llm_patch_accepted"] = False
                        llm_repair_result["llm_failure_reason"] = patch_meta.get("error", "patch_apply_failed")
                else:
                    llm_repair_result["llm_failure_reason"] = ops_error

            if llm_validation_result["validation_before_llm_issue_count"] == 0:
                llm_validation_result = {
                    "validation_before_llm_issue_count": len(remaining_issues),
                    "validation_after_llm_issue_count": validation_result.get("issue_count", 0),
                    "remaining_issues_after_llm": validation_result.get("issues", []),
                }
        else:
            llm_repair_result["llm_failure_reason"] = invoke_reason

    if grounding_result.get("grounding_valid", True):
        result = execute_workflow(workflow)
    else:
        result = ExecutionResult(
            workflow_id=workflow.workflow_id,
            success=False,
            executed_calls=0,
            events=[],
            final_state={},
            state_snapshots=[],
        )

    run_dir = Path("runs") / workflow.workflow_id
    ensure_dir(run_dir)

    dump_json(run_dir / "protocol_input.json", protocol.model_dump())
    if operation_split_payload is not None:
        dump_json(run_dir / "operations.json", operation_split_payload)
    if operation_parser_groups_payload is not None:
        dump_json(run_dir / "operation_parser_groups.json", operation_parser_groups_payload)
    if operation_grounder_groups_payload is not None:
        dump_json(run_dir / "operation_grounder_groups.json", operation_grounder_groups_payload)
    if operation_api_groups_payload is not None:
        dump_json(run_dir / "operation_api_groups.json", operation_api_groups_payload)
    dump_json(run_dir / "parser_preprocess.json", preprocess_payload)
    dump_json(run_dir / "llm_parser_result.json", llm_parser_result)
    if llm_parser_input_payload is not None:
        dump_json(run_dir / "llm_parser_input.json", llm_parser_input_payload)
    if llm_parser_raw_output_payload is not None:
        dump_json(run_dir / "llm_parser_raw_output.json", llm_parser_raw_output_payload)
    if llm_parser_parsed_output_payload is not None:
        dump_json(run_dir / "llm_parser_parsed_output.json", llm_parser_parsed_output_payload)
    dump_json(run_dir / "grounding_result.json", grounding_result)
    dump_json(run_dir / "grounding_validation_result.json", grounding_validation_result)
    if llm_grounder_input_payload is not None:
        dump_json(run_dir / "llm_grounder_input.json", llm_grounder_input_payload)
    if llm_grounder_raw_output_payload is not None:
        dump_json(run_dir / "llm_grounder_raw_output.json", llm_grounder_raw_output_payload)
    if llm_grounder_parsed_output_payload is not None:
        dump_json(run_dir / "llm_grounder_parsed_output.json", llm_grounder_parsed_output_payload)
    dump_json(run_dir / "planner_result.json", planner_result)
    dump_json(run_dir / "planner_validation_result.json", planner_validation_result)
    if llm_planner_input_payload is not None:
        dump_json(run_dir / "llm_planner_input.json", llm_planner_input_payload)
    if llm_planner_raw_output_payload is not None:
        dump_json(run_dir / "llm_planner_raw_output.json", llm_planner_raw_output_payload)
    if llm_planner_parsed_output_payload is not None:
        dump_json(run_dir / "llm_planner_parsed_output.json", llm_planner_parsed_output_payload)
    dump_json(run_dir / "parsed_protocol.json", parsed.model_dump())
    dump_json(run_dir / "grounded_workflow.json", grounded.model_dump())
    dump_json(run_dir / "workflow.json", workflow.model_dump())
    dump_json(run_dir / "validation_before_rule_repair.json", validation_before_rule_repair)
    dump_json(run_dir / "validation_result.json", validation_result)
    dump_json(run_dir / "repair_result.json", repair_result)
    if llm_input_payload is not None:
        dump_json(run_dir / "llm_input.json", llm_input_payload)
    if llm_raw_output_payload is not None:
        dump_json(run_dir / "llm_raw_output.json", llm_raw_output_payload)
    if llm_parsed_output_payload is not None:
        dump_json(run_dir / "llm_parsed_output.json", llm_parsed_output_payload)
    if workflow_before_llm_patch_payload is not None:
        dump_json(run_dir / "workflow_before_llm_patch.json", workflow_before_llm_patch_payload)
    if workflow_after_llm_patch_payload is not None:
        dump_json(run_dir / "workflow_after_llm_patch.json", workflow_after_llm_patch_payload)
    dump_json(run_dir / "llm_patch_result.json", llm_repair_result)
    dump_json(run_dir / "llm_validation_result.json", llm_validation_result)
    dump_json(run_dir / "execution_result.json", result.model_dump(mode="json"))
    dump_json(run_dir / "final_state.json", result.final_state)
    dump_json(run_dir / "state_snapshots.json", result.state_snapshots)

    report = "\n".join(
        [
            f"Protocol ID: {protocol.protocol_id}",
            f"Workflow ID: {workflow.workflow_id}",
            f"Executed Calls: {result.executed_calls}",
            f"Success: {result.success}",
            f"State Snapshots: {len(result.state_snapshots)}",
            f"Validation Valid: {validation_result.get('valid', True)}",
            f"Validation Issues: {validation_result.get('issue_count', 0)}",
            f"Repaired: {repair_result.get('repaired', False)}",
            f"Parser Backend: {llm_parser_result.get('parser_backend_mode')}",
            f"Parser Fallback Used: {llm_parser_result.get('llm_parser_fallback_used')}",
            f"Parser Failure Reason: {llm_parser_result.get('llm_parser_failure_reason')}",
            f"Operation Mode: {enable_operation_mode}",
            f"Operation Count: {len(operation_split_payload) if operation_split_payload is not None else 0}",
            f"Grounding Backend: {grounding_result.get('grounding_backend_mode')}",
            f"Grounding Valid: {grounding_result.get('grounding_valid')}",
            f"Grounding Failure Reason: {grounding_result.get('grounding_failure_reason')}",
            f"Contains Unregistered API: {grounding_result.get('contains_unregistered_api')}",
            f"Unregistered APIs: {grounding_result.get('unregistered_apis')}",
            f"Planner Backend: {planner_result.get('planner_backend_mode')}",
            f"Planner Valid: {planner_result.get('planner_valid')}",
            f"Planner Failure Reason: {planner_result.get('planner_failure_reason')}",
            f"LLM Repair Invoked: {llm_repair_result.get('llm_invoked', False)}",
            f"LLM Patch Accepted: {llm_repair_result.get('llm_patch_accepted', False)}",
            f"LLM Failure Reason: {llm_repair_result.get('llm_failure_reason')}",
            "",
            "APIs:",
            *[f"- {call.api}" for call in workflow.api_calls],
        ]
    )
    (run_dir / "summary_report.md").write_text(report, encoding="utf-8")

    typer.echo(f"Run completed. Outputs saved to: {run_dir}")


@app.command()
def version() -> None:
    typer.echo("bio-protocol v1.5")


@app.command()
def benchmark(
    cases_dir: Path | None = typer.Option(None, help="Directory containing benchmark case yaml files."),
    config: Path = typer.Option(Path("configs/benchmark_config.yaml"), help="Benchmark config file path."),
    enable_llm_repair: bool = typer.Option(False, help="Enable LLM repair in benchmark flow."),
    enable_llm_parser: bool = typer.Option(False, help="Enable LLM-primary parser in benchmark flow."),
    enable_llm_grounder: bool = typer.Option(
        False,
        "--enable-llm-grounder",
        "--enable-llm-grounding",
        help="Enable LLM-primary grounder in benchmark flow.",
    ),
) -> None:
    config_payload: dict = {}
    if config.exists():
        loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            config_payload = loaded.get("benchmark", {}) if isinstance(loaded.get("benchmark"), dict) else {}

    resolved_cases_dir = cases_dir or Path(config_payload.get("cases_dir", "tests/cases"))
    output_root = Path(config_payload.get("output_root", "runs"))

    if not resolved_cases_dir.exists():
        raise typer.BadParameter(f"Benchmark cases directory not found: {resolved_cases_dir}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = output_root / f"benchmark_{ts}"
    summary = run_benchmark(
        cases_dir=resolved_cases_dir,
        output_dir=out_dir,
        enable_llm_repair=enable_llm_repair,
        enable_llm_parser=enable_llm_parser,
        enable_llm_grounder=enable_llm_grounder,
    )
    typer.echo(
        f"Benchmark finished. pass_rate={summary['pass_rate']:.2%}, "
        f"executability_rate={summary['executability_rate']:.2%}, output={out_dir}"
    )


if __name__ == "__main__":
    app()


