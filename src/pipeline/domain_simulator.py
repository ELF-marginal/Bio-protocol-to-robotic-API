from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.models.contracts import Workflow


PredicateKey = tuple[str, tuple[tuple[str, str], ...]]
FunctionKey = tuple[str, tuple[tuple[str, str], ...]]


def simulate_workflow(
    workflow: Workflow,
    api_domain: dict[str, Any],
    lab_state: dict[str, Any],
    safety_rules: list[dict[str, Any]] | None = None,
    stop_on_error: bool = False,
) -> dict[str, Any]:
    state = domain_state_from_lab_state(lab_state)
    state["domain_function_units"] = _function_units_from_domain(api_domain)
    issues: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    actions = api_domain.get("actions", {})
    actions = actions if isinstance(actions, dict) else {}

    for index, call in enumerate(workflow.api_calls):
        safety_issues = _check_safety_rules(
            safety_rules=safety_rules or [],
            state=state,
            call_api=call.api,
            call_args=call.args,
            call_id=call.call_id,
            call_index=index,
        )
        issues.extend(safety_issues)
        if any(issue.get("severity") == "error" for issue in safety_issues):
            snapshots.append(_snapshot(call.call_id, call.api, state, issues))
            if stop_on_error:
                break
            continue

        action = actions.get(call.api)
        if not isinstance(action, dict):
            issues.append(
                _issue(
                    issue_type="UnknownAPI",
                    call_id=call.call_id,
                    api=call.api,
                    message=f"API {call.api} is not defined in api_domain.actions.",
                    call_index=index,
                )
            )
            snapshots.append(_snapshot(call.call_id, call.api, state, issues))
            if stop_on_error:
                break
            continue

        parameter_issues = _validate_parameters(call.args, action.get("parameters", {}))
        for parameter_issue in parameter_issues:
            issues.append(
                _issue(
                    issue_type=parameter_issue["issue_type"],
                    call_id=call.call_id,
                    api=call.api,
                    message=parameter_issue["message"],
                    call_index=index,
                )
            )
        if parameter_issues:
            snapshots.append(_snapshot(call.call_id, call.api, state, issues))
            if stop_on_error:
                break
            continue

        precondition_issues = _check_preconditions(
            preconditions=action.get("preconditions", {}),
            state=state,
            call_args=call.args,
        )
        for precondition_issue in precondition_issues:
            issues.append(
                _issue(
                    issue_type="PreconditionViolation",
                    call_id=call.call_id,
                    api=call.api,
                    message=precondition_issue,
                    call_index=index,
                )
            )
        if precondition_issues:
            snapshots.append(_snapshot(call.call_id, call.api, state, issues))
            if stop_on_error:
                break
            continue

        _apply_effects(
            effects=action.get("effects", {}),
            state=state,
            call_args=call.args,
        )
        snapshots.append(_snapshot(call.call_id, call.api, state, issues))

    return {
        "valid": not any(issue.get("severity") == "error" for issue in issues),
        "issue_count": len(issues),
        "issues": issues,
        "final_state": serialize_domain_state(state),
        "state_snapshots": snapshots,
    }


def domain_state_from_lab_state(lab_state: dict[str, Any]) -> dict[str, Any]:
    predicates: set[PredicateKey] = set()
    functions: dict[FunctionKey, float] = {}
    function_units: dict[FunctionKey, str] = {}
    init = lab_state.get("init", {})
    if not isinstance(init, dict):
        init = {}
    for pred in init.get("predicates", []) or []:
        key = predicate_key(pred, {})
        if key:
            predicates.add(key)
    for func in init.get("functions", []) or []:
        key = function_key(func, {})
        value = func.get("value") if isinstance(func, dict) else None
        if key and isinstance(value, (int, float)):
            functions[key] = float(value)
            unit = func.get("unit")
            if isinstance(unit, str):
                function_units[key] = unit
    return {"predicates": predicates, "functions": functions, "function_units": function_units}


def evaluate_expected_final_state(state: dict[str, Any], expected_state: Any) -> dict[str, Any]:
    if not isinstance(expected_state, dict):
        return {"matched": 0, "total": 1, "failures": ["expected_final_state must be a mapping"], "items": []}

    raw_state = _deserialize_domain_state(state) if "predicates" in state and isinstance(state.get("predicates"), list) else state
    matched = 0
    total = 0
    failures: list[str] = []
    items: list[dict[str, Any]] = []

    for pred in expected_state.get("predicates", []) or []:
        total += 1
        key = predicate_key(pred, {})
        ok = key in raw_state["predicates"] if key else False
        if ok:
            matched += 1
        else:
            failures.append(f"missing predicate: {pred}")
        items.append({"kind": "predicate", "expected": pred, "matched": ok})

    for func in expected_state.get("functions", []) or []:
        total += 1
        key = function_key(func, {})
        actual = raw_state["functions"].get(key) if key else None
        expected_value = func.get("value") if isinstance(func, dict) else None
        op = func.get("op", "==") if isinstance(func, dict) else "=="
        ok = compare_numeric(actual, op, expected_value)
        if ok:
            matched += 1
        else:
            failures.append(f"function mismatch: expected={func}, actual={actual}")
        items.append({"kind": "function", "expected": func, "actual": actual, "matched": ok})

    return {"matched": matched, "total": total, "failures": failures, "items": items}


def serialize_domain_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "predicates": [
            {"predicate": name, "args": dict(args)}
            for name, args in sorted(state.get("predicates", set()))
        ],
        "functions": [
            {
                "function": name,
                "args": dict(args),
                "value": value,
                **({"unit": state.get("function_units", {}).get((name, args))} if state.get("function_units", {}).get((name, args)) else {}),
            }
            for (name, args), value in sorted(state.get("functions", {}).items())
        ],
    }


def predicate_key(item: Any, call_args: dict[str, Any]) -> PredicateKey | None:
    if not isinstance(item, dict) or not isinstance(item.get("predicate"), str):
        return None
    args = item.get("args", {})
    return (item["predicate"], _resolved_arg_tuple(args if isinstance(args, dict) else {}, call_args))


def function_key(item: Any, call_args: dict[str, Any]) -> FunctionKey | None:
    if not isinstance(item, dict) or not isinstance(item.get("function"), str):
        return None
    args = item.get("args", {})
    return (item["function"], _resolved_arg_tuple(args if isinstance(args, dict) else {}, call_args))


def resolve_numeric(value_spec: Any, call_args: dict[str, Any], state: dict[str, Any] | None = None) -> float | None:
    if isinstance(value_spec, (int, float)):
        return float(value_spec)
    if not isinstance(value_spec, dict):
        return None
    if "parameter" in value_spec:
        value = call_args.get(value_spec["parameter"])
        return coerce_numeric_value(value)
    if "value" in value_spec:
        value = value_spec.get("value")
        return float(value) if isinstance(value, (int, float)) else None
    if "function" in value_spec and state is not None:
        key = function_key(value_spec, call_args)
        value = state["functions"].get(key) if key else None
        return float(value) if isinstance(value, (int, float)) else None
    if value_spec.get("expression") == "subtract":
        values = [resolve_numeric(item, call_args, state) for item in value_spec.get("operands", [])]
        if len(values) != 2 or values[0] is None or values[1] is None:
            return None
        return values[0] - values[1]
    return None


def coerce_numeric_value(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        inner = value.get("value")
        return float(inner) if isinstance(inner, (int, float)) else None
    return None


def compare_numeric(actual: Any, op: str, expected: Any) -> bool:
    if not isinstance(actual, (int, float)) or not isinstance(expected, (int, float)):
        return False
    if op in {"=", "=="}:
        return abs(float(actual) - float(expected)) < 1e-9
    if op == ">=":
        return float(actual) >= float(expected)
    if op == ">":
        return float(actual) > float(expected)
    if op == "<=":
        return float(actual) <= float(expected)
    if op == "<":
        return float(actual) < float(expected)
    return False


def _validate_parameters(args: dict[str, Any], parameters: Any) -> list[dict[str, str]]:
    if not isinstance(parameters, dict):
        return []
    issues: list[dict[str, str]] = []
    for name, spec in parameters.items():
        if name not in args:
            issues.append({"issue_type": "MissingParameter", "message": f"Missing required parameter: {name}"})
            continue
        if isinstance(spec, dict):
            value = args[name]
            numeric_value = coerce_numeric_value(value)
            expected_type = spec.get("type")
            if expected_type in {"number", "integer"} and numeric_value is None:
                issues.append({"issue_type": "ParameterTypeError", "message": f"Parameter {name} must be numeric."})
            if expected_type == "integer" and numeric_value is not None and not numeric_value.is_integer():
                issues.append({"issue_type": "ParameterTypeError", "message": f"Parameter {name} must be integer."})
            if "min" in spec and numeric_value is not None and numeric_value < spec["min"]:
                issues.append({"issue_type": "ParameterRangeError", "message": f"Parameter {name} must be >= {spec['min']}."})
            if isinstance(value, dict) and isinstance(spec.get("unit"), str):
                unit = value.get("unit")
                if unit != spec["unit"]:
                    issues.append({"issue_type": "ParameterUnitError", "message": f"Parameter {name} unit must be {spec['unit']}, got {unit}."})
    return issues


def _check_preconditions(preconditions: Any, state: dict[str, Any], call_args: dict[str, Any]) -> list[str]:
    if not isinstance(preconditions, dict):
        return []
    return _check_condition(preconditions, state, call_args)


def _check_safety_rules(
    safety_rules: list[dict[str, Any]],
    state: dict[str, Any],
    call_api: str,
    call_args: dict[str, Any],
    call_id: str,
    call_index: int,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for rule in safety_rules:
        if not isinstance(rule, dict):
            continue
        metadata = rule.get("metadata", {})
        if not isinstance(metadata, dict) or metadata.get("scope", "pre_call") != "pre_call":
            continue
        trigger_api = metadata.get("trigger_api")
        if isinstance(trigger_api, str) and trigger_api != call_api:
            continue
        bindings, binding_errors = _resolve_safety_bindings(rule.get("bindings", {}), state, call_args)
        for error in binding_errors:
            issues.append(
                _issue(
                    issue_type="SafetyRuleBindingError",
                    call_id=call_id,
                    api=call_api,
                    message=f"{rule.get('rule_id', 'unknown_rule')}: {error}",
                    call_index=call_index,
                )
            )
        if binding_errors:
            continue
        condition_results = _evaluate_safety_conditions(rule.get("conditions", {}), state, bindings)
        if condition_results["matched"]:
            effect = rule.get("effect_on_validation", {})
            on_true = effect.get("on_true", {}) if isinstance(effect, dict) else {}
            message = on_true.get("message") if isinstance(on_true, dict) else None
            issue = _issue(
                issue_type=str(on_true.get("issue_type", "safety_violation")) if isinstance(on_true, dict) else "safety_violation",
                call_id=call_id,
                api=call_api,
                message=str(message or f"Safety rule violated: {rule.get('rule_id', 'unknown_rule')}"),
                call_index=call_index,
            )
            issue["rule_id"] = rule.get("rule_id")
            issue["safety_category"] = metadata.get("category")
            issue["evidence"] = {
                "bindings": bindings,
                "conditions": condition_results["conditions"],
            }
            issues.append(issue)
    return issues


def _resolve_safety_bindings(
    binding_specs: Any,
    state: dict[str, Any],
    call_args: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(binding_specs, dict):
        return {}, []
    bindings: dict[str, Any] = {}
    errors: list[str] = []
    for name, spec in binding_specs.items():
        if not isinstance(spec, dict):
            continue
        value: Any = None
        source = spec.get("from")
        if isinstance(source, str) and source.startswith("call.args."):
            value = call_args.get(source.removeprefix("call.args."))
        elif isinstance(source, dict) and isinstance(source.get("function"), str):
            resolved_args = {}
            for key, arg_value in (source.get("args", {}) or {}).items():
                resolved_args[key] = _resolve_binding_reference(arg_value, bindings)
            key = function_key({"function": source["function"], "args": resolved_args}, {})
            value = state["functions"].get(key) if key else None
        elif "value" in spec:
            value = spec.get("value")
        if value is None and spec.get("required", False):
            errors.append(f"required binding {name} could not be resolved")
        bindings[str(name)] = value
    return bindings, errors


def _evaluate_safety_conditions(
    conditions: Any,
    state: dict[str, Any],
    bindings: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(conditions, dict):
        return {"matched": False, "conditions": []}
    if "all" in conditions:
        results = [_evaluate_safety_condition(item, state, bindings) for item in conditions.get("all", []) or []]
        return {"matched": all(item["matched"] for item in results), "conditions": results}
    result = _evaluate_safety_condition(conditions, state, bindings)
    return {"matched": result["matched"], "conditions": [result]}


def _evaluate_safety_condition(condition: Any, state: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(condition, dict):
        return {"id": None, "matched": False, "condition": condition}
    kind = condition.get("kind")
    if kind == "predicate_exists":
        args = {
            key: _resolve_binding_reference(value, bindings)
            for key, value in (condition.get("args", {}) or {}).items()
        }
        key = predicate_key({"predicate": condition.get("predicate"), "args": args}, {})
        matched = key in state["predicates"] if key else False
        return {"id": condition.get("id"), "kind": kind, "matched": matched, "resolved_args": args}
    if kind == "numeric_compare":
        left = _resolve_numeric_reference(condition.get("left"), bindings)
        right = _resolve_numeric_reference(condition.get("right"), bindings)
        op = str(condition.get("op", "=="))
        return {
            "id": condition.get("id"),
            "kind": kind,
            "matched": compare_numeric(left, op, right),
            "left": left,
            "op": op,
            "right": right,
        }
    return {"id": condition.get("id"), "kind": kind, "matched": False, "condition": condition}


def _resolve_binding_reference(value: Any, bindings: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return bindings.get(value[1:])
    return value


def _resolve_numeric_reference(value: Any, bindings: dict[str, Any]) -> float | None:
    resolved = _resolve_binding_reference(value, bindings)
    return coerce_numeric_value(resolved)


def _check_condition(condition: Any, state: dict[str, Any], call_args: dict[str, Any]) -> list[str]:
    if not isinstance(condition, dict):
        return []
    if "all" in condition:
        issues: list[str] = []
        for item in condition.get("all", []) or []:
            issues.extend(_check_condition(item, state, call_args))
        return issues
    if "not" in condition:
        inner = condition.get("not")
        inner_issues = _check_condition(inner, state, call_args)
        if inner_issues:
            return []
        return [f"Negated precondition unexpectedly satisfied: {inner}"]
    if "numeric" in condition:
        numeric = condition.get("numeric", {})
        left = resolve_numeric(numeric.get("left"), call_args, state)
        right = resolve_numeric(numeric.get("right"), call_args, state)
        op = str(numeric.get("op", "=="))
        if compare_numeric(left, op, right):
            return []
        return [f"Numeric precondition failed: left={left} op={op} right={right} spec={numeric}"]
    if "predicate" in condition:
        key = predicate_key(condition, call_args)
        if key in state["predicates"]:
            return []
        return [f"Predicate precondition failed: {condition}"]
    return []


def _apply_effects(effects: Any, state: dict[str, Any], call_args: dict[str, Any]) -> None:
    if not isinstance(effects, dict):
        return
    for pred in effects.get("delete", []) or []:
        key = predicate_key(pred, call_args)
        if key:
            state["predicates"].discard(key)
    for pred in effects.get("add", []) or []:
        key = predicate_key(pred, call_args)
        if key:
            state["predicates"].add(key)
    for effect in effects.get("numeric", []) or []:
        key = function_key(effect, call_args)
        if not key:
            continue
        value = resolve_numeric(effect.get("value"), call_args, state)
        if value is None:
            continue
        current = state["functions"].get(key, 0)
        _remember_function_unit(effect, key, state)
        op = effect.get("op")
        if op == "+=":
            state["functions"][key] = current + value
        elif op == "-=":
            state["functions"][key] = current - value
        elif op == "=":
            state["functions"][key] = value


def _resolved_arg_tuple(args: dict[str, Any], call_args: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    resolved: list[tuple[str, str]] = []
    for key, value in sorted(args.items()):
        if isinstance(value, str) and value in call_args:
            resolved.append((str(key), str(call_args[value])))
        else:
            resolved.append((str(key), str(value)))
    return tuple(resolved)


def _function_units_from_domain(api_domain: dict[str, Any]) -> dict[str, str]:
    units: dict[str, str] = {}
    functions = api_domain.get("functions", {})
    if not isinstance(functions, dict):
        return units
    for name, spec in functions.items():
        if isinstance(name, str) and isinstance(spec, dict) and isinstance(spec.get("unit"), str):
            units[name] = spec["unit"]
    return units


def _remember_function_unit(effect: dict[str, Any], key: FunctionKey, state: dict[str, Any]) -> None:
    if key in state.get("function_units", {}):
        return
    unit = effect.get("unit")
    if not isinstance(unit, str):
        unit = state.get("domain_function_units", {}).get(key[0])
    if isinstance(unit, str):
        state.setdefault("function_units", {})[key] = unit


def _snapshot(call_id: str, api: str, state: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "after_call_id": call_id,
        "api": api,
        "state": serialize_domain_state(state),
        "issue_count": len(issues),
    }


def _issue(issue_type: str, call_id: str | None, api: str | None, message: str, call_index: int) -> dict[str, Any]:
    return {
        "issue_type": issue_type,
        "severity": "error",
        "call_id": call_id,
        "api": api,
        "call_index": call_index,
        "message": message,
        "suggestion": None,
    }


def _deserialize_domain_state(state: dict[str, Any]) -> dict[str, Any]:
    predicates: set[PredicateKey] = set()
    functions: dict[FunctionKey, float] = {}
    for pred in state.get("predicates", []) or []:
        key = predicate_key(pred, {})
        if key:
            predicates.add(key)
    for func in state.get("functions", []) or []:
        key = function_key(func, {})
        value = func.get("value") if isinstance(func, dict) else None
        if key and isinstance(value, (int, float)):
            functions[key] = float(value)
    return {"predicates": predicates, "functions": functions}
