import os

from src.models.contracts import ParsedProtocol, ParsedStep
from src.pipeline.llm_grounder import (
    build_llm_grounding_input,
    invoke_llm_grounding,
    normalize_grounding_output,
    parse_llm_grounding_output,
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


def test_build_llm_grounding_input_contains_available_apis() -> None:
    payload = build_llm_grounding_input(_parsed_protocol())
    assert payload["protocol_id"] == "p_ground"
    assert isinstance(payload["available_apis"], list)
    assert len(payload["available_apis"]) > 0


def test_parse_llm_grounding_output_accepts_direct_api_calls() -> None:
    raw = """```json
{
  "api_calls": [
    {"api": "pipette.transfer", "args": {"source": "buffer", "target": "sample_tube", "volume_ul": 100}}
  ],
  "contains_unregistered_api": false,
  "unregistered_apis": []
}
```"""
    parsed, err = parse_llm_grounding_output(raw)
    assert err is None
    assert parsed is not None
    normalized = normalize_grounding_output(parsed)
    assert normalized["workflow"]["api_calls"][0]["api"] == "pipette.transfer"


def test_invoke_llm_grounding_missing_key() -> None:
    previous = os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        result = invoke_llm_grounding(
            llm_input={"protocol_id": "p", "parsed_protocol": {}, "available_apis": [], "grounding_task_instruction": "x"},
            config={"provider": "deepseek", "model": "deepseek-chat"},
        )
        assert result["llm_grounding_invoked"] is True
        assert result["failure_reason"] == "missing_deepseek_api_key"
    finally:
        if previous is not None:
            os.environ["DEEPSEEK_API_KEY"] = previous
