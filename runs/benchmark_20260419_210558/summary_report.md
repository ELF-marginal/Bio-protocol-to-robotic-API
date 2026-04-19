# Benchmark Summary

- Total Cases: 11
- Passed Cases: 2
- Parsing Accuracy: 53.85%
- Grounding Accuracy: 95.24%
- Parameter Accuracy: 87.80%
- Sequence Accuracy: 27.12%
- Executability Rate: 0.00%
- Pass Rate: 18.18%
- Precondition Violations: 8
- Repaired Cases: 1
- Unrepaired Cases: 3
- Unrepairable Cases: 0
- Repaired IDs: ['case_002_incubation']
- Unrepaired IDs: ['case_004_ignore_unsupported_centrifuge', 'case_007_synonym_actions', 'case_009_unsupported_plus_valid']
- Unrepairable IDs: []
- LLM Invoked Cases: 0
- LLM Repaired Cases: 0
- LLM Repair Success Rate: 0.00%
- Post LLM Validation Pass Cases: 0
- Avg Remaining Issues Before LLM: 0.00
- Avg Remaining Issues After LLM: 0.00
- Parser LLM Invoked Cases: 11
- Parser LLM Accept Rate: 100.00%
- Parser LLM Fallback Rate: 0.00%
- Parser LLM Schema Fail Cases: 0
- Parser Failure Reasons: {}
- Grounding LLM Invoked Cases: 11
- Grounding LLM Success Rate: 72.73%
- Unregistered API Case Count: 3
- Grounding Failure Reasons: {'unregistered_api_detected': 3}

## Case Results

| Case ID | Result | Repaired | LLM Invoked | LLM Accepted | Before Issues | After Issues | LLM Failure | Failure Reason |
|---|---|---|---|---|---:|---:|---|---|
| case_001_simple_transfer | FAIL | no | no | no | 0 | 0 | llm_repair_flag_disabled | sequence mismatch at index 0: expected=tube.uncap, actual=fridge.open; sequence mismatch at index... |
| case_002_incubation | FAIL | yes | no | no | 0 | 0 | llm_repair_flag_disabled | sequence mismatch at index 0: expected=heater.set_temperature, actual=robot.pick; sequence mismat... |
| case_003_complex_combo | PASS | no | no | no | 0 | 0 | llm_repair_flag_disabled | - |
| case_004_ignore_unsupported_centrifuge | FAIL | no | no | no | 0 | 0 | grounding_invalid_skip_llm_repair | workflow invalid and no repair applied.; found forbidden action: centrifuge.run |
| case_005_unit_normalization | PASS | no | no | no | 0 | 0 | llm_repair_flag_disabled | - |
| case_006_complex_single_sentence | FAIL | no | no | no | 0 | 0 | llm_repair_flag_disabled | sequence mismatch at index 1: expected=fridge.take_out, actual=robot.pick; sequence mismatch at i... |
| case_007_synonym_actions | FAIL | no | no | no | 0 | 0 | grounding_invalid_skip_llm_repair | workflow invalid and no repair applied.; sequence mismatch at index 1: expected=fridge.take_out, ... |
| case_008_implicit_target | FAIL | no | no | no | 0 | 0 | llm_repair_flag_disabled | sequence mismatch at index 1: expected=fridge.take_out, actual=robot.pick; sequence mismatch at i... |
| case_009_unsupported_plus_valid | FAIL | no | no | no | 0 | 0 | grounding_invalid_skip_llm_repair | workflow invalid and no repair applied.; sequence mismatch at index 0: expected=fridge.open, actu... |
| case_010_rule_only_simple | FAIL | no | no | no | 0 | 0 | llm_repair_flag_disabled | sequence mismatch at index 1: expected=fridge.take_out, actual=robot.pick; sequence mismatch at i... |
| case_011_rule_only_incubation | FAIL | no | no | no | 0 | 0 | llm_repair_flag_disabled | sequence mismatch at index 1: expected=fridge.take_out, actual=robot.pick; sequence mismatch at i... |

## Parser LLM

| Case ID | Backend Mode | Invoked | Accepted | Fallback | Valid JSON | Schema Valid | Failure Reason |
|---|---|---|---|---|---|---|---|
| case_001_simple_transfer | llm_primary | yes | yes | no | yes | yes | None |
| case_002_incubation | llm_primary | yes | yes | no | yes | yes | None |
| case_003_complex_combo | llm_primary | yes | yes | no | yes | yes | None |
| case_004_ignore_unsupported_centrifuge | llm_primary | yes | yes | no | yes | yes | None |
| case_005_unit_normalization | llm_primary | yes | yes | no | yes | yes | None |
| case_006_complex_single_sentence | llm_primary | yes | yes | no | yes | yes | None |
| case_007_synonym_actions | llm_primary | yes | yes | no | yes | yes | None |
| case_008_implicit_target | llm_primary | yes | yes | no | yes | yes | None |
| case_009_unsupported_plus_valid | llm_primary | yes | yes | no | yes | yes | None |
| case_010_rule_only_simple | llm_primary | yes | yes | no | yes | yes | None |
| case_011_rule_only_incubation | llm_primary | yes | yes | no | yes | yes | None |

## Grounding LLM

| Case ID | Backend Mode | Grounding Valid | Unregistered API | Failure Reason |
|---|---|---|---|---|
| case_001_simple_transfer | llm_primary | yes | no | None |
| case_002_incubation | llm_primary | yes | no | None |
| case_003_complex_combo | llm_primary | yes | no | None |
| case_004_ignore_unsupported_centrifuge | llm_primary | no | yes | unregistered_api_detected |
| case_005_unit_normalization | llm_primary | yes | no | None |
| case_006_complex_single_sentence | llm_primary | yes | no | None |
| case_007_synonym_actions | llm_primary | no | yes | unregistered_api_detected |
| case_008_implicit_target | llm_primary | yes | no | None |
| case_009_unsupported_plus_valid | llm_primary | no | yes | unregistered_api_detected |
| case_010_rule_only_simple | llm_primary | yes | no | None |
| case_011_rule_only_incubation | llm_primary | yes | no | None |