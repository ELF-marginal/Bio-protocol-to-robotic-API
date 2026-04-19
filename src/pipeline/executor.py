from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.models.contracts import ApiCall, ExecutionEvent, ExecutionResult, Workflow


def execute_workflow(workflow: Workflow, initial_state_path: str = "configs/initial_lab_state.yaml") -> ExecutionResult:
    state = _load_initial_state(initial_state_path)
    events: list[ExecutionEvent] = []
    snapshots: list[dict[str, Any]] = []

    for call in workflow.api_calls:
        success, message = _apply_call(state, call)
        events.append(
            ExecutionEvent(
                timestamp=datetime.now(timezone.utc),
                call_id=call.call_id,
                api=call.api,
                args=call.args,
                success=success,
                message=message,
            )
        )

        snapshots.append({"after_call_id": call.call_id, "api": call.api, "state": deepcopy(state)})
        if not success:
            return ExecutionResult(
                workflow_id=workflow.workflow_id,
                success=False,
                executed_calls=len(events),
                events=events,
                final_state=state,
                state_snapshots=snapshots,
            )

    return ExecutionResult(
        workflow_id=workflow.workflow_id,
        success=True,
        executed_calls=len(events),
        events=events,
        final_state=state,
        state_snapshots=snapshots,
    )


def _load_initial_state(path: str) -> dict[str, Any]:
    payload = Path(path).read_text(encoding="utf-8")
    loaded = yaml.safe_load(payload)
    return loaded if isinstance(loaded, dict) else {}


def _apply_call(state: dict[str, Any], call: ApiCall) -> tuple[bool, str]:
    api = call.api
    args = call.args

    if api == "fridge.open":
        if state["fridge"]["is_open"]:
            return False, "PreconditionViolation: fridge already open."
        state["fridge"]["is_open"] = True
        return True, "ok"

    if api == "fridge.close":
        if not state["fridge"]["is_open"]:
            return False, "PreconditionViolation: fridge already closed."
        state["fridge"]["is_open"] = False
        return True, "ok"

    if api == "robot.pick":
        item = _get_str(args, "item")
        from_location = _get_str(args, "from_location")
        if not item or not from_location:
            return False, "ParameterError: item and from_location are required."
        if from_location == "fridge" and not state["fridge"]["is_open"]:
            return False, "PreconditionViolation: open fridge before picking items."
        items = state.get(from_location, {}).get("items", [])
        if item not in items:
            return False, f"PreconditionViolation: {item} not found in {from_location}."
        items.remove(item)
        state.setdefault("robot", {})["holding"] = item
        _sync_object_location(state, item, "robot_hand")
        return True, "ok"

    if api == "robot.place":
        item = _get_str(args, "item")
        to_location = _get_str(args, "to_location")
        if not item or not to_location:
            return False, "ParameterError: item and to_location are required."
        if state.get("robot", {}).get("holding") != item:
            return False, f"PreconditionViolation: robot is not holding {item}."
        state.setdefault(to_location, {}).setdefault("items", []).append(item)
        state.setdefault("robot", {})["holding"] = None
        _sync_object_location(state, item, to_location)
        return True, "ok"

    if api == "tube.uncap":
        tube_id = _get_str(args, "tube_id")
        tube = state.get("tubes", {}).get(tube_id)
        if not tube:
            return False, f"PreconditionViolation: tube {tube_id} does not exist."
        if tube.get("is_capped") is False:
            return False, f"PreconditionViolation: tube {tube_id} already uncapped."
        tube["is_capped"] = False
        return True, "ok"

    if api == "tube.cap":
        tube_id = _get_str(args, "tube_id")
        tube = state.get("tubes", {}).get(tube_id)
        if not tube:
            return False, f"PreconditionViolation: tube {tube_id} does not exist."
        if tube.get("is_capped") is True:
            return False, f"PreconditionViolation: tube {tube_id} already capped."
        tube["is_capped"] = True
        return True, "ok"

    if api == "pipette.attach_tip":
        if state.get("pipette", {}).get("has_tip"):
            return False, "PreconditionViolation: pipette already has tip."
        state.setdefault("pipette", {})["has_tip"] = True
        return True, "ok"

    if api == "pipette.discard_tip":
        if not state.get("pipette", {}).get("has_tip"):
            return False, "PreconditionViolation: pipette has no tip to discard."
        state.setdefault("pipette", {})["has_tip"] = False
        return True, "ok"

    if api == "pipette.transfer":
        source = _get_str(args, "source")
        target = _get_str(args, "target")
        volume_ul = _get_num(args, "volume_ul")
        if not source or not target or volume_ul is None:
            return False, "ParameterError: source/target/volume_ul are required."
        if not state.get("pipette", {}).get("has_tip"):
            return False, "PreconditionViolation: attach tip before transfer."
        tube = state.get("tubes", {}).get(target)
        if not tube:
            return False, f"PreconditionViolation: target tube {target} does not exist."
        if tube.get("is_capped"):
            return False, f"PreconditionViolation: cannot transfer, {target} is capped."
        reagent = state.get("reagents", {}).get(source)
        if not reagent:
            return False, f"PreconditionViolation: source reagent {source} does not exist."
        current = float(reagent.get("volume_ul", 0))
        if current < volume_ul:
            return False, f"PreconditionViolation: insufficient volume in {source}."
        reagent["volume_ul"] = current - volume_ul
        tube.setdefault("contents", []).append({"name": source, "volume_ul": volume_ul})
        return True, "ok"

    if api == "pipette.mix":
        container = _get_str(args, "container")
        times = _get_num(args, "times")
        if not container:
            return False, "ParameterError: container is required."
        if not state.get("pipette", {}).get("has_tip"):
            return False, "PreconditionViolation: attach tip before mix."
        tube = state.get("tubes", {}).get(container)
        if not tube:
            return False, f"PreconditionViolation: tube {container} does not exist."
        if tube.get("is_capped"):
            return False, f"PreconditionViolation: cannot mix, {container} is capped."
        tube["mixed_times"] = int(times) if times is not None else 0
        return True, "ok"

    if api == "heater.set_temperature":
        temperature = _get_num(args, "temperature_c")
        if temperature is None:
            return False, "ParameterError: temperature_c is required."
        state.setdefault("heater", {})["temperature_c"] = temperature
        return True, "ok"

    if api == "heater.place":
        item = _get_str(args, "item")
        if not item:
            return False, "ParameterError: item is required."
        if state.get("heater", {}).get("temperature_c") is None:
            return False, "PreconditionViolation: set heater temperature before placing sample."
        current_location = _object_location(state, item)
        if current_location is None:
            return False, f"PreconditionViolation: cannot find item {item}."
        _remove_item_from_location(state, item, current_location)
        state.setdefault("heater", {}).setdefault("items", []).append(item)
        _sync_object_location(state, item, "heater")
        return True, "ok"

    if api == "heater.remove":
        item = _get_str(args, "item")
        heater_items = state.setdefault("heater", {}).setdefault("items", [])
        if item not in heater_items:
            return False, f"PreconditionViolation: {item} is not in heater."
        heater_items.remove(item)
        state.setdefault("bench", {}).setdefault("items", []).append(item)
        _sync_object_location(state, item, "bench")
        return True, "ok"

    if api == "timer.wait":
        minutes = _get_num(args, "minutes")
        if minutes is None:
            return False, "ParameterError: minutes is required."
        state.setdefault("timer", {})["last_wait_min"] = minutes
        return True, "ok"

    return False, f"UnknownAPI: {api}"


def _sync_object_location(state: dict[str, Any], item: str, location: str) -> None:
    if item in state.get("tubes", {}):
        state["tubes"][item]["location"] = location
    if item in state.get("reagents", {}):
        state["reagents"][item]["location"] = location


def _object_location(state: dict[str, Any], item: str) -> str | None:
    for location in ("fridge", "bench"):
        if item in state.get(location, {}).get("items", []):
            return location
    if item in state.get("heater", {}).get("items", []):
        return "heater"
    if state.get("robot", {}).get("holding") == item:
        return "robot_hand"
    return None


def _remove_item_from_location(state: dict[str, Any], item: str, location: str) -> None:
    if location in ("fridge", "bench"):
        items = state.get(location, {}).get("items", [])
        if item in items:
            items.remove(item)
    elif location == "heater":
        items = state.get("heater", {}).get("items", [])
        if item in items:
            items.remove(item)
    elif location == "robot_hand":
        if state.get("robot", {}).get("holding") == item:
            state["robot"]["holding"] = None


def _get_str(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    return value if isinstance(value, str) else None


def _get_num(args: dict[str, Any], key: str) -> float | None:
    value = args.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return None
