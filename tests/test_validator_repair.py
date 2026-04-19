from src.models.contracts import ApiCall, Workflow
from src.pipeline.repair import repair_workflow
from src.pipeline.validator import validate_workflow


def test_validator_detects_missing_tip_and_uncap() -> None:
    workflow = Workflow(
        workflow_id="wf_v1",
        protocol_id="p_v1",
        api_calls=[
            ApiCall(
                call_id="c1",
                api="pipette.transfer",
                args={"source": "buffer", "target": "sample_tube", "volume_ul": 100},
            )
        ],
    )
    result = validate_workflow(workflow)
    assert result["valid"] is False
    issue_types = [issue["issue_type"] for issue in result["issues"]]
    assert "PreconditionViolation" in issue_types


def test_repair_inserts_calls_before_transfer() -> None:
    workflow = Workflow(
        workflow_id="wf_r1",
        protocol_id="p_r1",
        api_calls=[
            ApiCall(
                call_id="c1",
                api="pipette.transfer",
                args={"source": "buffer", "target": "sample_tube", "volume_ul": 100},
            )
        ],
    )
    validation_result = validate_workflow(workflow)
    repair_payload = repair_workflow(workflow, validation_result)
    assert repair_payload["repaired"] is True
    repaired_workflow = repair_payload["workflow"]
    apis = [call.api for call in repaired_workflow.api_calls]
    assert "tube.uncap" in apis
    assert "pipette.attach_tip" in apis
