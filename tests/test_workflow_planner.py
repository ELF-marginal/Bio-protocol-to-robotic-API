from src.models.contracts import ApiCall, Workflow
from src.pipeline.workflow_planner import compose_workflow


def test_compose_inserts_implicit_pipette_and_tube_steps() -> None:
    base = Workflow(
        workflow_id="wf_1",
        protocol_id="p1",
        api_calls=[
            ApiCall(
                call_id="c1",
                api="pipette.transfer",
                args={"source": "buffer", "target": "sample_tube", "volume_ul": 100},
                source_step_id="s1",
            )
        ],
    )

    planned = compose_workflow(base)
    apis = [c.api for c in planned.api_calls]
    assert apis == [
        "tube.uncap",
        "pipette.attach_tip",
        "pipette.transfer",
        "pipette.discard_tip",
        "tube.cap",
    ]
