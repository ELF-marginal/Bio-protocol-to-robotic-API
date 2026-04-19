from src.pipeline.llm_planner import build_fallback_workflow, run_planner_backend


def test_fallback_planner_merges_adjacent_fridge_sessions() -> None:
    operation_api_groups = [
        {
            "operation_id": "op_001",
            "operation_raw_text": "take tube",
            "api_calls": [
                {"api": "fridge.open", "args": {}},
                {"api": "robot.pick", "args": {"item": "sample_tube", "from_location": "fridge"}},
                {"api": "fridge.close", "args": {}},
            ],
        },
        {
            "operation_id": "op_002",
            "operation_raw_text": "take buffer",
            "api_calls": [
                {"api": "fridge.open", "args": {}},
                {"api": "robot.pick", "args": {"item": "buffer", "from_location": "fridge"}},
                {"api": "fridge.close", "args": {}},
            ],
        },
    ]

    workflow = build_fallback_workflow("p_merge", operation_api_groups)
    apis = [call.api for call in workflow.api_calls]

    assert apis.count("fridge.open") == 1
    assert apis.count("fridge.close") == 1
    assert apis.count("robot.pick") == 2


def test_run_planner_backend_disabled_returns_fallback() -> None:
    operation_api_groups = [
        {
            "operation_id": "op_001",
            "operation_raw_text": "add buffer",
            "api_calls": [
                {"api": "pipette.transfer", "args": {"source": "buffer", "target": "sample_tube", "volume_ul": 100}}
            ],
        }
    ]
    backend = run_planner_backend(
        protocol_id="p_planner",
        operations=[{"operation_id": "op_001", "raw_text": "add buffer"}],
        operation_api_groups=operation_api_groups,
        enable_llm_planner=False,
        config=None,
    )
    workflow = backend["workflow"]
    assert workflow.workflow_id == "wf_p_planner"
    assert backend["planner_result"]["planner_backend_mode"] == "fallback_rule_planner"
