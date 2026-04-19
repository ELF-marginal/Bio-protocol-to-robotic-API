import os

from src.models.contracts import ParsedProtocol, ParsedStep, ProtocolInput
from src.pipeline.llm_parser import (
    build_llm_parser_input,
    build_parser_quality_report,
    invoke_llm_parser,
    load_llm_parser_config,
    preprocess_protocol_text,
    run_parser_backend,
    split_protocol_sentences,
)


def test_rule_only_mode_when_disabled() -> None:
    cfg = load_llm_parser_config()
    protocol = ProtocolInput(protocol_id="p1", raw_text="Add 100 uL buffer to sample tube.")
    result = run_parser_backend(protocol=protocol, enable_llm_parser=False, config=cfg)
    assert result["llm_parser_result"]["parser_backend_mode"] == "rule_only"


def test_build_llm_parser_input_has_required_sections() -> None:
    payload = build_llm_parser_input(protocol_text="Add buffer to sample tube.")
    assert "protocol_text" in payload
    assert "task" in payload


def test_preprocess_normalizes_units() -> None:
    pre = preprocess_protocol_text("Add 100 \u03bcL buffer to tube at 37\u2103.")
    assert "\u03bcL" not in pre["processed_text"]
    assert "\u2103" not in pre["processed_text"]
    assert "uL" in pre["processed_text"]
    assert "37C" in pre["processed_text"]


def test_quality_report_detects_unknown_action() -> None:
    parsed = ParsedProtocol(
        protocol_id="p_unknown",
        steps=[
            ParsedStep(
                step_id="s1",
                raw_text="Do something custom.",
                action="unknown",
                entities={},
                parameters={},
            )
        ],
    )
    report = build_parser_quality_report(parsed, split_protocol_sentences("Do something custom."))
    assert report["has_unknown_action"] is True


def test_invoke_llm_parser_deepseek_missing_key() -> None:
    previous = os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        result = invoke_llm_parser(
            llm_input={"task": "test"},
            config={"provider": "deepseek", "model": "deepseek-chat"},
        )
        assert result["llm_parser_invoked"] is True
        assert result["llm_parser_valid_json"] is False
        assert result["failure_reason"] == "missing_deepseek_api_key"
    finally:
        if previous is not None:
            os.environ["DEEPSEEK_API_KEY"] = previous
