# Case Schema

Each benchmark case is a YAML file under `tests/cases/`.

## Required Fields

```yaml
case_id: "case_001_simple_transfer"
input_protocol: "Add 100 uL buffer to the sample tube."
expected_success: true
```

- `case_id`: unique identifier.
- `input_protocol`: raw protocol text used as pipeline input.
- `expected_success`: expected execution success/failure.

## Optional Fields

```yaml
expected_parsed_steps:
  - action: "add"
    source: "buffer"
    target: "sample_tube"
    parameters:
      volume_ul: 100

expected_actions:
  - "pipette.transfer"
must_not_include:
  - "centrifuge.run"

expected_workflow_sequence:
  - "tube.uncap"
  - "pipette.attach_tip"
  - "pipette.transfer"
  - "pipette.discard_tip"
  - "tube.cap"

expected_parameters:
  - api: "pipette.transfer"
    occurrence: 1
    args:
      source: "buffer"
      target: "sample_tube"
      volume_ul: 100

expected_final_state:
  tubes:
    sample_tube:
      is_capped: true
  reagents:
    buffer:
      volume_ul: 9900
```

- `expected_parsed_steps`: parser-level checks by step index.
  - Supported keys per step: `action`, `source`, `target`, `item`, `parameters`.
- `expected_actions`: APIs that must appear in final workflow.
- `must_not_include`: APIs that must not appear in final workflow.
- `expected_workflow_sequence`: exact order check for first N API calls.
- `expected_parameters`: API argument checks.
  - `occurrence` is optional, default `1`.
- `expected_final_state`: nested final state assertions.

## `expected_parameters` Alternative Format

You can also use a dictionary form:

```yaml
expected_parameters:
  pipette.transfer:
    volume_ul: 100
```

## Notes

- If a field is absent, it is not scored in that dimension.
- Sequence checks are strict by position.
- Final state checks are path/value equality checks.
