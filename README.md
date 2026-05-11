# Bio-Protocol to Robotic API

Version: v1.6

## Project Overview

Bio-Protocol to Robotic API is a prototype system for converting natural-language biology protocols into structured robotic laboratory API workflows. The system parses protocol text, grounds parsed steps into registered API calls, validates the resulting workflow against a symbolic lab state, optionally repairs invalid workflows, and writes detailed benchmark/debug artifacts for inspection.

The current v1.6 focus is benchmark-driven evaluation of protocol-to-API grounding, including fine-grained robotic API cases that explicitly model robot navigation, dual-arm manipulation, object grasp/place actions, cap handling, pipette tip lifecycle, liquid volume accounting, and final lab-state checks.

High-level pipeline:

```text
Protocol text
  -> Parser
  -> LLM/API grounding
  -> Workflow planner
  -> Validator and simulator
  -> Optional repair
  -> Benchmark comparison
  -> Run artifacts
```

## What Is New In v1.6

- Added benchmark cases through `case10`.
- Added hard fine-grained dual-arm robotic API benchmark design.
- Added `case9`, a long fine-grained colorimetric assay case used to stress-test output length and detailed state tracking.
- Added `case10`, a compact fine-grained case with about 50 expected API calls, designed to test detailed API planning without exceeding typical LLM output limits.
- Updated benchmark case layout under `tests/benchmark/`.
- Added case-specific API domain, lab state, safety rule, expected API sequence, and expected final state files.
- Added current-format safety rules using `metadata / bindings / conditions / effect_on_validation`.
- Benchmark outputs now include detailed API sequence diff, final-state comparison, LLM parser/grounder raw and parsed outputs, validation results, and per-case summary files.

## Project Structure

```text
project_root/
|-- main.py
|-- README.md
|-- requirements.txt
|-- configs/
|   |-- api_registry.yaml
|   |-- benchmark_config.yaml
|   |-- initial_lab_state.yaml
|   |-- llm_grounding_config.yaml
|   |-- llm_parser_config.yaml
|   |-- llm_repair_config.yaml
|   `-- validator_config.yaml
|-- schemas/
|   |-- llm_grounding_input.schema.json
|   |-- llm_grounding_output.schema.json
|   |-- llm_parser_input.schema.json
|   |-- llm_parser_output.schema.json
|   |-- llm_repair_input.schema.json
|   |-- llm_repair_output.schema.json
|   |-- parsed_protocol.schema.json
|   `-- workflow.schema.json
|-- src/
|   |-- models/
|   |   `-- contracts.py
|   |-- pipeline/
|   |   |-- benchmark_runner.py
|   |   |-- domain_simulator.py
|   |   |-- executor.py
|   |   |-- llm_grounder.py
|   |   |-- llm_parser.py
|   |   |-- llm_repair.py
|   |   |-- repair.py
|   |   |-- unit_normalizer.py
|   |   |-- validator.py
|   |   `-- workflow_planner.py
|   `-- utils/
|       `-- io.py
|-- tests/
|   `-- benchmark/
|       |-- case1/
|       |-- ...
|       |-- case9/
|       `-- case10/
`-- runs/
    `-- benchmark_YYYYMMDD_HHMMSS/
```

## Benchmark Cases

Each benchmark case lives in its own directory:

```text
tests/benchmark/caseN/
|-- benchmark_case.yaml
|-- api_domain.yaml
|-- lab_state.yaml
`-- safty_rule.yaml
```

`benchmark_case.yaml` contains:

- `case_id`
- `difficulty`
- `domain`
- `input_text`
- `expected_api_sequence`
- `expected_final_state`
- `expected_success`
- optional feature tags such as `hard_features`

`api_domain.yaml` declares available API actions, parameters, preconditions, and effects.

`lab_state.yaml` declares the initial symbolic lab state, including object locations, container states, cap relations, tip states, paths, and numeric fluents.

`safty_rule.yaml` contains optional safety rules. In v1.6 these rules should use the simulator-supported format:

```yaml
- rule_id: example_rule
  metadata:
    scope: pre_call
    trigger_api: some_api
  bindings:
    target:
      from: call.args.target
      required: true
  conditions:
    all:
      - kind: predicate_exists
        predicate: container_closed
        args:
          container: "$target"
  effect_on_validation:
    on_true:
      issue_type: safety_violation
      blocking: true
      message: "Example validation message."
```

## Fine-Grained API Benchmarking

v1.6 introduces harder fine-grained benchmark cases for robotic lab execution.

The fine-grained API style includes actions such as:

- `move_robot`
- `grasp_object`
- `place_object`
- `label_container`
- `unscrew_cap`
- `screw_cap`
- `attach_tip_to_pipette`
- `aspirate_liquid`
- `dispense_liquid`
- `eject_tip`
- `dispense_drops`
- `shake_container`
- `stand_at_room_temperature`

These cases test whether a model can preserve physical state across many small operations rather than relying on coarse actions such as a single `transfer`.

### Case9

`case9` is a long hard case for a dual-arm biuret/colorimetric workflow. It includes sample and blank tubes, multiple reagent containers, cap handling, fresh tips, drop addition, mixing, standing, and final reagent-container closeout.

It is useful as a stress test, but current runs show that very long fine-grained outputs can exceed LLM output limits and produce invalid or truncated JSON.

### Case10

`case10` is a compact hard case with 50 expected API calls. It keeps the fine-grained dual-arm style while reducing total output length. It is intended for checking whether the model can handle detailed state transitions such as:

- opening the target tube before dispensing,
- keeping pipette/tip state consistent,
- ejecting tips only after successful dispense,
- closing containers before mixing/standing,
- returning the robot and objects to expected final states.

Recent case10 runs show that the LLM can produce a valid fine-grained workflow with high API-name coverage, but it may still fail validation when it skips required opening/closing steps or breaks tip/liquid state consistency.

## CLI Usage

### Show Version

```bash
python main.py version
```

Expected project version for this README:

```text
bio-protocol v1.6
```

### Run A Single Protocol

```bash
python main.py run --text "Label sample_tube. Add 1000 uL sample solution. Mix for 5 seconds."
```

or:

```bash
python main.py run --file test.txt
```

Common options:

- `--text`: raw protocol text.
- `--file`: protocol file path, currently `.txt` and `.md`.
- `--title`: protocol title.
- `--enable-validator`: run validation before execution.
- `--enable-repair`: enable rule-based repair.
- `--enable-llm-repair`: enable LLM repair for unresolved issues.
- `--enable-llm-parser`: enable LLM-primary parsing.
- `--enable-llm-grounding`: enable LLM-primary grounding.

### Run Benchmark

```bash
python main.py benchmark
```

or explicitly:

```bash
python main.py benchmark --cases-dir tests/benchmark
```

Benchmark configuration defaults are loaded from:

```text
configs/benchmark_config.yaml
```

Current default benchmark directory:

```text
tests/benchmark
```

Benchmark outputs are written to:

```text
runs/benchmark_YYYYMMDD_HHMMSS/
```

## Output Artifacts

For each benchmark case, the runner writes a dedicated output directory containing files such as:

- `benchmark_case.json`
- `case_context.json`
- `protocol_input.json`
- `parser_preprocess.json`
- `parsed_protocol.json`
- `llm_parser_result.json`
- `llm_parser_input.json`
- `llm_parser_raw_output.json`
- `llm_parser_parsed_output.json`
- `grounding_result.json`
- `grounding_validation_result.json`
- `llm_grounding_input.json`
- `llm_grounding_raw_output.json`
- `llm_grounding_parsed_output.json`
- `workflow_before_repair.json`
- `workflow.json`
- `actual_api_sequence.json`
- `expected_api_sequence.json`
- `api_sequence_diff.json`
- `validation_before_repair.json`
- `validation_result.json`
- `repair_result.json`
- `simulated_final_state.json`
- `expected_final_state.json`
- `final_state_check.json`
- `case_result.json`

The most important per-case summary is:

```text
case_result.json
```

It records:

- pass/fail result,
- parser/grounding/validation/sequence/parameter/final-state checks,
- repair count,
- score,
- actual and expected API counts,
- failure reasons,
- detailed metrics.

The benchmark root also contains:

- `benchmark_summary.json`
- `summary_report.md`

## Metrics

The benchmark runner currently reports:

- `sequence_accuracy`: API-name sequence match rate.
- `parameter_accuracy`: expected argument match rate.
- `final_state_accuracy`: expected final predicates/functions match rate.
- `score`: average of sequence, parameter, and final-state accuracy.
- `pass_rate`: percentage of cases that fully pass.
- difficulty-weighted summary across Easy, Medium, and Hard cases.

A case passes only when all major checks pass:

- parser produced steps,
- grounding is valid,
- workflow validation is valid,
- API sequence matches,
- parameters match,
- final state matches.

## Current Known Limitations

- Very long fine-grained workflows can cause LLM grounding output to be truncated, leading to invalid JSON and an empty workflow.
- The benchmark currently relies on exact or near-exact API and parameter matching, so harmless parameter choices such as different tolerances or release heights can reduce parameter accuracy.
- Fine-grained state planning remains challenging: models may generate plausible API names while missing critical state transitions such as opening a tube before dispensing or keeping pipette-loaded volume consistent.
- Safety-rule support in the simulator is currently focused on pre-call checks using `metadata / bindings / conditions`; broader rule forms such as after-action uniqueness checks are not fully interpreted yet.
- Some internal code paths still carry historical naming such as `safty_rule.yaml`.

## Development Notes

The project currently has no guaranteed configured runtime environment in this workspace. Static inspection and file-based benchmark analysis are often possible without running the full pipeline, but complete execution requires installing the dependencies from `requirements.txt` and configuring any required LLM provider credentials.

## License

No license information has been specified yet.
