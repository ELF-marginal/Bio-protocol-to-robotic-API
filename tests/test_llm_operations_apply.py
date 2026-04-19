from src.models.contracts import ApiCall, Workflow
from src.pipeline.llm_repair import apply_llm_operations, load_api_registry
from src.pipeline.validator import validate_workflow


def _base_workflow() -> Workflow:
    return Workflow(
        workflow_id="wf_apply",
        protocol_id="p_apply",
        api_calls=[
            ApiCall(call_id="c1", api="tube.uncap", args={"tube_id": "sample_tube"}),
            ApiCall(call_id="c2", api="pipette.attach_tip", args={}),
            ApiCall(
                call_id="c3",
                api="pipette.transfer",
                args={"source": "buffer", "target": "sample_tube", "volume_ul": 100},
            ),
        ],
    )


def test_apply_insert_before_success() -> None:
    workflow = _base_workflow()
    registry = load_api_registry()
    patched, meta = apply_llm_operations(
        workflow=workflow,
        operations=[
            {
                "op": "insert_before",
                "target_call_id": "c3",
                "new_call": {"api": "pipette.mix", "args": {"container": "sample_tube", "volume_ul": 50, "times": 2}},
            }
        ],
        api_registry=registry,
    )
    assert patched is not None
    assert meta["patch_applied"] is True
    assert [c.api for c in patched.api_calls] == [
        "tube.uncap",
        "pipette.attach_tip",
        "pipette.mix",
        "pipette.transfer",
    ]
    assert [c.call_id for c in patched.api_calls] == ["c1", "c2", "c3", "c4"]


def test_apply_replace_call_success() -> None:
    workflow = _base_workflow()
    registry = load_api_registry()
    patched, meta = apply_llm_operations(
        workflow=workflow,
        operations=[
            {
                "op": "replace_call",
                "target_call_id": "c2",
                "new_call": {"api": "pipette.attach_tip", "args": {}},
            }
        ],
        api_registry=registry,
    )
    assert patched is not None
    assert meta["patch_applied"] is True
    assert patched.api_calls[1].api == "pipette.attach_tip"


def test_apply_invalid_target_call_id() -> None:
    workflow = _base_workflow()
    registry = load_api_registry()
    patched, meta = apply_llm_operations(
        workflow=workflow,
        operations=[
            {
                "op": "insert_before",
                "target_call_id": "c999",
                "new_call": {"api": "pipette.attach_tip", "args": {}},
            }
        ],
        api_registry=registry,
    )
    assert patched is None
    assert meta["patch_applied"] is False
    assert meta["error"] == "target_call_id_not_found"


def test_apply_unknown_api_rejected() -> None:
    workflow = _base_workflow()
    registry = load_api_registry()
    patched, meta = apply_llm_operations(
        workflow=workflow,
        operations=[
            {
                "op": "insert_after",
                "target_call_id": "c1",
                "new_call": {"api": "foo.bar", "args": {}},
            }
        ],
        api_registry=registry,
    )
    assert patched is None
    assert meta["patch_applied"] is False
    assert meta["error"] == "unknown_api"


def test_apply_then_revalidate_fallback() -> None:
    workflow = _base_workflow()
    registry = load_api_registry()
    patched, meta = apply_llm_operations(
        workflow=workflow,
        operations=[
            {
                "op": "replace_call",
                "target_call_id": "c2",
                "new_call": {"api": "fridge.open", "args": {}},
            }
        ],
        api_registry=registry,
    )
    assert patched is not None
    assert meta["patch_applied"] is True

    revalidated = validate_workflow(patched)
    assert revalidated["valid"] is False
    # Fallback should keep rule workflow
    fallback = workflow if not revalidated["valid"] else patched
    assert [c.api for c in fallback.api_calls] == [c.api for c in workflow.api_calls]
