from pathlib import Path

import yaml

from src.pipeline.benchmark_runner import run_benchmark


def test_benchmark_summary_contains_llm_stats(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    out_dir = tmp_path / "out"
    cases_dir.mkdir(parents=True, exist_ok=True)

    case = {
        "case_id": "case_tmp_001",
        "input_protocol": "Add 100 uL buffer to the sample tube.",
        "expected_success": True,
    }
    (cases_dir / "case_tmp_001.yaml").write_text(yaml.safe_dump(case), encoding="utf-8")

    summary = run_benchmark(cases_dir=cases_dir, output_dir=out_dir, enable_llm_repair=False)
    assert "llm_stats" in summary
    assert "llm_invoked_cases" in summary["llm_stats"]
    assert "llm_repair_success_rate" in summary["llm_stats"]
    assert "parser_llm_stats" in summary
    assert "parser_llm_invoked_cases" in summary["parser_llm_stats"]
    assert "grounding_llm_stats" in summary
    assert "grounding_llm_invoked_cases" in summary["grounding_llm_stats"]

    report_text = (out_dir / "summary_report.md").read_text(encoding="utf-8")
    assert "| Case ID | Result |" in report_text
    assert "|---|---|---|" in report_text
    assert "## Parser LLM" in report_text
    assert "| Case ID | Backend Mode | Invoked | Accepted |" in report_text
