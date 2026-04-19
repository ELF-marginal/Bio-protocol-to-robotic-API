# Benchmark Summary

- Total Cases: 11
- Passed Cases: 0
- Parsing Accuracy: 37.50%
- Grounding Accuracy: 93.55%
- Parameter Accuracy: 92.17%
- Sequence Accuracy: 33.33%
- Executability Rate: 0.00%
- Pass Rate: 0.00%
- Precondition Violations: 7
- Repaired Cases: 1
- Unrepaired Cases: 4
- Unrepairable Cases: 0
- Repaired IDs: ['case_006_complex_single_sentence']
- Unrepaired IDs: ['case_004_ignore_unsupported_centrifuge', 'case_007_synonym_actions', 'case_009_unsupported_plus_valid', 'case_011_rule_only_incubation']
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
- Grounding LLM Success Rate: 63.64%
- Unregistered API Case Count: 4
- Grounding Failure Reasons: {'unregistered_api_detected': 4}

## Case Results

| Case ID | Result | Repaired | LLM Invoked | LLM Accepted | Before Issues | After Issues | LLM Failure | Failure Reason |
|---|---|---|---|---|---:|---:|---|---|
| case_001_simple_transfer | FAIL | no | no | no | 0 | 0 | llm_repair_flag_disabled | sequence mismatch at index 5: expected=pipette.transfer, actual=pipette.attach_tip; sequence mism... |
| case_002_incubation | FAIL | no | no | no | 0 | 0 | llm_repair_flag_disabled | sequence mismatch at index 1: expected=heater.place, actual=heater.set_temperature; sequence mism... |
| case_003_complex_combo | FAIL | no | no | no | 0 | 0 | llm_repair_flag_disabled | sequence mismatch at index 2: expected=robot.place, actual=fridge.close; sequence mismatch at ind... |
| case_004_ignore_unsupported_centrifuge | FAIL | no | no | no | 0 | 0 | grounding_invalid_skip_llm_repair | workflow invalid and no repair applied.; found forbidden action: centrifuge.run |
| case_005_unit_normalization | FAIL | no | no | no | 0 | 0 | llm_repair_flag_disabled | sequence mismatch at index 5: expected=fridge.close, actual=tube.uncap; sequence mismatch at inde... |
| case_006_complex_single_sentence | FAIL | yes | no | no | 0 | 0 | llm_repair_flag_disabled | sequence mismatch at index 2: expected=fridge.close, actual=robot.place; sequence mismatch at ind... |
| case_007_synonym_actions | FAIL | no | no | no | 0 | 0 | grounding_invalid_skip_llm_repair | workflow invalid and no repair applied.; sequence mismatch at index 1: expected=fridge.take_out, ... |
| case_008_implicit_target | FAIL | no | no | no | 0 | 0 | llm_repair_flag_disabled | sequence mismatch at index 3: expected=tube.uncap, actual=robot.place; sequence mismatch at index... |
| case_009_unsupported_plus_valid | FAIL | no | no | no | 0 | 0 | grounding_invalid_skip_llm_repair | workflow invalid and no repair applied.; sequence mismatch at index 1: expected=fridge.take_out, ... |
| case_010_rule_only_simple | FAIL | no | no | no | 0 | 0 | llm_repair_flag_disabled | sequence mismatch at index 2: expected=fridge.close, actual=pipette.attach_tip; sequence mismatch... |
| case_011_rule_only_incubation | FAIL | no | no | no | 0 | 0 | grounding_invalid_skip_llm_repair | workflow invalid and no repair applied.; missing expected action: tube.uncap; missing expected ac... |

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
| case_011_rule_only_incubation | llm_primary | no | yes | unregistered_api_detected |