from __future__ import annotations

from src.models.contracts import ApiCall, Workflow


PIPETTE_ACTIONS = {"pipette.transfer", "pipette.mix"}


def compose_workflow(base_workflow: Workflow) -> Workflow:
    planned: list[ApiCall] = []
    has_tip = False
    tube_is_open = False

    for call in base_workflow.api_calls:
        api = call.api

        if api in PIPETTE_ACTIONS:
            target_tube = _extract_target_tube(call)
            if target_tube and not tube_is_open:
                planned.append(
                    ApiCall(
                        call_id="",
                        api="tube.uncap",
                        args={"tube_id": target_tube},
                        source_step_id=call.source_step_id,
                    )
                )
                tube_is_open = True

            if not has_tip:
                planned.append(ApiCall(call_id="", api="pipette.attach_tip", source_step_id=call.source_step_id))
                has_tip = True

            planned.append(call)
            continue

        if api == "pipette.discard_tip":
            has_tip = False
            planned.append(call)
            continue

        if api == "tube.uncap":
            tube_is_open = True
            planned.append(call)
            continue

        if api == "tube.cap":
            tube_is_open = False
            planned.append(call)
            continue

        planned.append(call)

    if has_tip:
        planned.append(ApiCall(call_id="", api="pipette.discard_tip"))
    if tube_is_open:
        planned.append(ApiCall(call_id="", api="tube.cap", args={"tube_id": "sample_tube"}))

    return Workflow(
        workflow_id=base_workflow.workflow_id,
        protocol_id=base_workflow.protocol_id,
        api_calls=_renumber_calls(planned),
    )


def _extract_target_tube(call: ApiCall) -> str | None:
    if call.api == "pipette.transfer":
        target = call.args.get("target")
        return target if isinstance(target, str) and "tube" in target else None
    if call.api == "pipette.mix":
        container = call.args.get("container")
        return container if isinstance(container, str) and "tube" in container else None
    return None


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
