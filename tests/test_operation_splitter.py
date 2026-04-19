from src.pipeline.operation_splitter import split_operations


def test_split_operations_by_newline_and_keep_line_numbers() -> None:
    raw = "Procedure\n\n1. Open fridge\n2. Pick sample tube\n"
    operations = split_operations(raw)

    assert len(operations) == 3
    assert operations[0]["operation_id"] == "op_001"
    assert operations[0]["raw_text"] == "Procedure"
    assert operations[0]["line_no"] == 1
    assert operations[1]["raw_text"] == "1. Open fridge"
    assert operations[1]["line_no"] == 3
    assert operations[2]["raw_text"] == "2. Pick sample tube"
    assert operations[2]["line_no"] == 4
