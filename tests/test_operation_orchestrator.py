from src.models.contracts import ProtocolInput
from src.pipeline.llm_grounder import load_llm_grounder_config
from src.pipeline.llm_parser import load_llm_parser_config
from src.pipeline.operation_orchestrator import run_operation_grounder_pass, run_operation_parser_pass
from src.pipeline.operation_splitter import split_operations


def test_operation_parser_and_grounder_keep_group_boundaries() -> None:
    protocol = ProtocolInput(
        protocol_id="p_ops",
        title="ops",
        source="test",
        raw_text="Take sample from fridge\nAdd 100 uL lysis buffer to sample tube",
    )
    operations = split_operations(protocol.raw_text)
    parser_pass = run_operation_parser_pass(
        protocol=protocol,
        operations=operations,
        enable_llm_parser=False,
        parser_config=load_llm_parser_config(),
    )
    groups = parser_pass["operation_parser_groups"]
    assert len(groups) == 2
    assert groups[0]["operation_id"] == "op_001"
    assert groups[1]["operation_id"] == "op_002"
    assert isinstance(groups[0]["steps"], list)
    assert isinstance(groups[1]["steps"], list)

    grounder_pass = run_operation_grounder_pass(
        protocol=protocol,
        operation_parser_groups=groups,
        enable_llm_grounder=False,
        grounder_config=load_llm_grounder_config(),
    )
    api_groups = grounder_pass["operation_api_groups"]
    assert len(api_groups) == 2
    assert api_groups[0]["operation_id"] == "op_001"
    assert api_groups[1]["operation_id"] == "op_002"
    assert isinstance(api_groups[0]["api_calls"], list)
    assert isinstance(api_groups[1]["api_calls"], list)
