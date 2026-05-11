from __future__ import annotations

from src.models.contracts import ApiCall, Workflow


def compose_workflow(base_workflow: Workflow) -> Workflow:
    """Keep the grounded plan intact and assign stable call ids."""
    return Workflow(
        workflow_id=base_workflow.workflow_id,
        protocol_id=base_workflow.protocol_id,
        api_calls=_renumber_calls(base_workflow.api_calls),
    )


def _renumber_calls(calls: list[ApiCall]) -> list[ApiCall]:
    renumbered: list[ApiCall] = []
    for idx, call in enumerate(calls, start=1):
        renumbered.append(
            ApiCall(
                call_id=f"c{idx}",
                api=call.api,
                args=call.args,
                source_step_id=call.source_step_id,
            )
        )
    return renumbered
