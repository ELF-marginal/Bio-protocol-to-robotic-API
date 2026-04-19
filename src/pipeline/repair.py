from __future__ import annotations

from typing import Any

from src.models.contracts import ApiCall, Workflow


def repair_workflow(workflow: Workflow, validation_result: dict[str, Any]) -> dict[str, Any]:
    issues = validation_result.get("issues", [])
    if not issues:
        return {
            "repaired": False,
            "applied_repairs": [],
            "workflow": workflow,
        }

    insert_before: dict[str, list[ApiCall]] = {}
    applied_repairs: list[dict[str, Any]] = []

    for issue in issues:
        call_id = issue.get("call_id")
        api = issue.get("api")
        message = str(issue.get("message", ""))
        if not isinstance(call_id, str):
            continue

        repair_call = _build_repair_call(api=api, message=message)
        if repair_call is None:
            continue

        insert_before.setdefault(call_id, [])
        # avoid duplicates for same insertion point + api
        if not any(existing.api == repair_call.api and existing.args == repair_call.args for existing in insert_before[call_id]):
            insert_before[call_id].append(repair_call)
            applied_repairs.append(
                {
                    "insert_before_call_id": call_id,
                    "insert_api": repair_call.api,
                    "insert_args": repair_call.args,
                    "reason": message,
                }
            )

    if not applied_repairs:
        return {
            "repaired": False,
            "applied_repairs": [],
            "workflow": workflow,
        }

    repaired_calls: list[ApiCall] = []
    for call in workflow.api_calls:
        for patch_call in insert_before.get(call.call_id, []):
            repaired_calls.append(patch_call)
        repaired_calls.append(call)

    repaired = Workflow(
        workflow_id=workflow.workflow_id,
        protocol_id=workflow.protocol_id,
        api_calls=_renumber(repaired_calls),
    )

    return {
        "repaired": True,
        "applied_repairs": applied_repairs,
        "workflow": repaired,
    }


def _build_repair_call(api: Any, message: str) -> ApiCall | None:
    if api in {"pipette.transfer", "pipette.mix"} and "without tip attached" in message:
        return ApiCall(call_id="", api="pipette.attach_tip")
    if api == "pipette.transfer" and "is not uncapped" in message:
        tube = _extract_tube_from_message(message)
        return ApiCall(call_id="", api="tube.uncap", args={"tube_id": tube or "sample_tube"})
    if api == "pipette.mix" and "is not uncapped" in message:
        tube = _extract_tube_from_message(message)
        return ApiCall(call_id="", api="tube.uncap", args={"tube_id": tube or "sample_tube"})
    if api == "fridge.close" and "before fridge.open" in message:
        return ApiCall(call_id="", api="fridge.open")
    if api == "heater.place" and "before heater.set_temperature" in message:
        return ApiCall(call_id="", api="heater.set_temperature", args={"temperature_c": 37})
    return None


def _extract_tube_from_message(message: str) -> str | None:
    marker = "target "
    if marker in message:
        start = message.find(marker) + len(marker)
        end = message.find(" ", start)
        if end == -1:
            end = len(message)
        value = message[start:end].strip(".,")
        return value if value else None
    marker = "container "
    if marker in message:
        start = message.find(marker) + len(marker)
        end = message.find(" ", start)
        if end == -1:
            end = len(message)
        value = message[start:end].strip(".,")
        return value if value else None
    return None


def _renumber(calls: list[ApiCall]) -> list[ApiCall]:
    out: list[ApiCall] = []
    for idx, call in enumerate(calls, start=1):
        out.append(
            ApiCall(
                call_id=f"c{idx}",
                api=call.api,
                args=call.args,
                source_step_id=call.source_step_id,
            )
        )
    return out
