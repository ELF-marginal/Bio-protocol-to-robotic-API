from __future__ import annotations

from src.models.contracts import ApiCall, ParsedProtocol, Workflow


def ground_to_workflow(parsed: ParsedProtocol) -> Workflow:
    calls: list[ApiCall] = []
    call_no = 1

    for step in parsed.steps:
        if step.action == "take":
            calls.extend(
                [
                    ApiCall(call_id=f"c{call_no}", api="fridge.open", source_step_id=step.step_id),
                    ApiCall(
                        call_id=f"c{call_no + 1}",
                        api="robot.pick",
                        args={"item": "sample_tube", "from_location": "fridge"},
                        source_step_id=step.step_id,
                    ),
                    ApiCall(
                        call_id=f"c{call_no + 2}",
                        api="robot.place",
                        args={"item": "sample_tube", "to_location": "bench"},
                        source_step_id=step.step_id,
                    ),
                    ApiCall(call_id=f"c{call_no + 3}", api="fridge.close", source_step_id=step.step_id),
                ]
            )
            call_no += 4
        elif step.action == "add":
            calls.append(
                ApiCall(
                    call_id=f"c{call_no}",
                    api="pipette.transfer",
                    args={
                        "source": step.entities.get("source", "buffer"),
                        "target": step.entities.get("target", "sample_tube"),
                        "volume_ul": step.parameters.get("volume_ul", 100),
                    },
                    source_step_id=step.step_id,
                )
            )
            call_no += 1
        elif step.action == "incubate":
            calls.extend(
                [
                    ApiCall(
                        call_id=f"c{call_no}",
                        api="heater.set_temperature",
                        args={"temperature_c": step.parameters.get("temperature_c", 37)},
                        source_step_id=step.step_id,
                    ),
                    ApiCall(
                        call_id=f"c{call_no + 1}",
                        api="heater.place",
                        args={"item": step.entities.get("target", "sample_tube")},
                        source_step_id=step.step_id,
                    ),
                    ApiCall(
                        call_id=f"c{call_no + 2}",
                        api="timer.wait",
                        args={"minutes": step.parameters.get("duration_min", 10)},
                        source_step_id=step.step_id,
                    ),
                    ApiCall(
                        call_id=f"c{call_no + 3}",
                        api="heater.remove",
                        args={"item": step.entities.get("target", "sample_tube")},
                        source_step_id=step.step_id,
                    ),
                ]
            )
            call_no += 4
        elif step.action == "mix":
            calls.append(
                ApiCall(
                    call_id=f"c{call_no}",
                    api="pipette.mix",
                    args={
                        "container": step.entities.get("target", "sample_tube"),
                        "volume_ul": step.parameters.get("volume_ul", 100),
                        "times": step.parameters.get("times", 5),
                    },
                    source_step_id=step.step_id,
                )
            )
            call_no += 1

    return Workflow(workflow_id=f"wf_{parsed.protocol_id}", protocol_id=parsed.protocol_id, api_calls=calls)
