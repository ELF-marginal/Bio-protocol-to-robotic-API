import os

from src.models.contracts import ApiCall, Workflow
from src.pipeline.llm_repair import (
    build_llm_input,
    invoke_llm_repair,
    load_llm_repair_config,
    should_invoke_llm_repair,
)


def test_should_invoke_llm_repair_only_for_allowed_issue_types() -> None:
    cfg = load_llm_repair_config()
    remaining = [{"issue_type": "PreconditionViolation", "api": "pipette.transfer"}]
    invoke, reason = should_invoke_llm_repair(True, cfg, remaining)
    assert invoke is True
    assert reason == "invoke"

    blocked = [{"issue_type": "UnknownIssueType", "api": "pipette.transfer"}]
    invoke_blocked, blocked_reason = should_invoke_llm_repair(True, cfg, blocked)
    assert invoke_blocked is False
    assert blocked_reason == "issue_type_not_allowed"


def test_llm_input_contains_minimal_api_descriptor_pack() -> None:
    workflow = Workflow(
        workflow_id="wf_l1",
        protocol_id="p_l1",
        api_calls=[
            ApiCall(call_id="c1", api="pipette.transfer", args={"source": "buffer", "target": "sample_tube", "volume_ul": 100})
        ],
    )
    payload = build_llm_input(
        protocol_text="Add 100 uL buffer to the sample tube.",
        parsed_steps=[{"step_id": "s1", "action": "add"}],
        workflow_before_llm_repair=workflow,
        validation_issues_before_llm=[],
        applied_rule_repairs=[],
        remaining_issues_after_rule_repair=[{"issue_type": "PreconditionViolation", "api": "pipette.transfer"}],
    )
    assert isinstance(payload["available_apis"], list)
    assert len(payload["available_apis"]) > 0
    first_api = payload["available_apis"][0]
    assert "name" in first_api
    assert "required_args" in first_api
    assert "arg_types" in first_api
    assert isinstance(payload["lab_state_initial"], dict)
    assert isinstance(payload["lab_state_expected"], dict)
    assert "notice" in payload


def test_llm_repair_config_contains_path_fields() -> None:
    cfg = load_llm_repair_config()
    assert "api_registry_path" in cfg
    assert "initial_lab_state_path" in cfg
    assert "expected_lab_state_path" in cfg
    assert "notice_path" in cfg


def test_invoke_llm_repair_deepseek_missing_key() -> None:
    previous = os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        result = invoke_llm_repair(
            llm_input={"task": "test"},
            config={"provider": "deepseek", "model": "deepseek-chat"},
        )
        assert result["llm_invoked"] is True
        assert result["llm_output_valid_json"] is False
        assert result["failure_reason"] == "missing_deepseek_api_key"
    finally:
        if previous is not None:
            os.environ["DEEPSEEK_API_KEY"] = previous
