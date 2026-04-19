import os

from src.models.contracts import ParsedProtocol, ParsedStep
from src.pipeline.llm_grounder import (
    build_llm_grounder_input,
    invoke_llm_grounder,
    normalize_grounder_output,
    parse_llm_grounder_output,
    validate_grounder_output,
)


def _parsed_protocol() -> ParsedProtocol:
    return ParsedProtocol(
        protocol_id="p_ground",
        steps=[
            ParsedStep(
                step_id="s1",
                raw_text="Add 100 uL buffer to sample tube.",
                action="add",
                entities={"source": "buffer", "target": "sample_tube"},
                parameters={"volume_ul": 100},
            )
        ],
    )


def test_build_llm_grounder_input_contains_available_apis() -> None:
    payload = build_llm_grounder_input(_parsed_protocol())
    assert payload["protocol_id"] == "p_ground"
    assert isinstance(payload["available_apis"], list)
    assert len(payload["available_apis"]) > 0


def test_parse_llm_grounder_output_accepts_direct_api_calls() -> None:
    raw = """```json
{
  "api_calls": [
    {"api": "pipette.transfer", "args": {"source": "buffer", "target": "sample_tube", "volume_ul": 100}}
  ],
  "contains_unregistered_api": false,
  "unregistered_apis": []
}
```"""
    parsed, err = parse_llm_grounder_output(raw)
    assert err is None
    assert parsed is not None
    normalized = normalize_grounder_output(parsed)
    assert normalized["workflow"]["api_calls"][0]["api"] == "pipette.transfer"


def test_invoke_llm_grounder_missing_key() -> None:
    previous = os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        result = invoke_llm_grounder(
            llm_input={"protocol_id": "p", "parsed_protocol": {}, "available_apis": [], "grounder_task_instruction": "x"},
            config={"provider": "deepseek", "model": "deepseek-chat"},
        )
        assert result["llm_grounder_invoked"] is True
        assert result["failure_reason"] == "missing_deepseek_api_key"
    finally:
        if previous is not None:
            os.environ["DEEPSEEK_API_KEY"] = previous


def test_validate_grounder_output_respects_custom_registry_path() -> None:
    normalized = {
        "workflow": {
            "api_calls": [
                {
                    "api": "centrifuge.run",
                    "args": {
                        "centrifuge_id": "benchtop_centrifuge",
                        "speed_xg": 12000,
                        "duration_min": 10,
                        "temperature_c": 4,
                    },
                }
            ]
        },
        "contains_unregistered_api": False,
        "unregistered_apis": [],
    }
    result = validate_grounder_output(normalized_output=normalized, api_registry_path="configs/api_real.yaml")
    assert result["grounding_valid"] is True
    assert result["contains_unregistered_api"] is False



