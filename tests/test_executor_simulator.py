from src.models.contracts import ApiCall, Workflow
from src.pipeline.executor import execute_workflow


def test_executor_fails_when_transfer_without_uncap_or_tip() -> None:
    workflow = Workflow(
        workflow_id="wf_fail",
        protocol_id="p_fail",
        api_calls=[
            ApiCall(
                call_id="c1",
                api="pipette.transfer",
                args={"source": "buffer", "target": "sample_tube", "volume_ul": 100},
            )
        ],
    )
    result = execute_workflow(workflow)
    assert result.success is False
    assert result.executed_calls == 1
    assert "PreconditionViolation" in result.events[0].message


def test_executor_updates_state_for_valid_transfer() -> None:
    workflow = Workflow(
        workflow_id="wf_ok",
        protocol_id="p_ok",
        api_calls=[
            ApiCall(call_id="c1", api="fridge.open"),
            ApiCall(call_id="c2", api="robot.pick", args={"item": "sample_tube", "from_location": "fridge"}),
            ApiCall(call_id="c3", api="robot.place", args={"item": "sample_tube", "to_location": "bench"}),
            ApiCall(call_id="c4", api="fridge.close"),
            ApiCall(call_id="c5", api="tube.uncap", args={"tube_id": "sample_tube"}),
            ApiCall(call_id="c6", api="pipette.attach_tip"),
            ApiCall(
                call_id="c7",
                api="pipette.transfer",
                args={"source": "buffer", "target": "sample_tube", "volume_ul": 100},
            ),
        ],
    )
    result = execute_workflow(workflow)
    assert result.success is True
    buffer_volume = result.final_state["reagents"]["buffer"]["volume_ul"]
    assert buffer_volume == 9900
