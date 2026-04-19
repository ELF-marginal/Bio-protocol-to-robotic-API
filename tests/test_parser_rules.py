from src.models.contracts import ProtocolInput
from src.pipeline.mock_parser import parse_protocol


def test_parse_add_with_ml_normalization() -> None:
    protocol = ProtocolInput(protocol_id="p1", raw_text="Add 0.1 mL buffer to the sample tube.")
    parsed = parse_protocol(protocol)
    step = parsed.steps[0]
    assert step.action == "add"
    assert step.parameters["volume_ul"] == 100


def test_parse_incubate_temperature_duration() -> None:
    protocol = ProtocolInput(protocol_id="p2", raw_text="Incubate at 37C for 10 minutes.")
    parsed = parse_protocol(protocol)
    step = parsed.steps[0]
    assert step.action == "incubate"
    assert step.parameters["temperature_c"] == 37
    assert step.parameters["duration_min"] == 10
