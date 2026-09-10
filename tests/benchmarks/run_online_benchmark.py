"""Online evaluation and validation benchmark runner for AICriticAgent.

Runs N=10 iterations per golden finding to measure probabilistic output
correctness, schema adherence, and semantic concept inclusion.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from workflow_clinic.critic import (
    AICriticAgent,
    BenchmarkReport,
    EvaluationResult,
    RemediationEvaluator,
)
from workflow_clinic.utils.llm import resolve_model

BENCHMARK_DIR = Path(__file__).parent
GOLDEN_DIR = BENCHMARK_DIR / "golden"
RESULTS_DIR = BENCHMARK_DIR / "results"


def parse_args() -> argparse.Namespace:
    """Parse benchmark CLI options."""
    parser = argparse.ArgumentParser(
        description="Run AICriticAgent N=10 reproducibility and correctness benchmark suite."
    )
    parser.add_argument(
        "--model",
        "-m",
        type=str,
        default=None,
        help="Target LiteLLM model identifier (default: auto-detected from environment).",
    )
    parser.add_argument(
        "--api-key",
        "-k",
        type=str,
        default=None,
        help="Explicit API key override for the target model provider.",
    )
    parser.add_argument(
        "--iterations",
        "-n",
        type=int,
        default=10,
        help="Number of evaluation iterations per golden finding (default: 10).",
    )
    parser.add_argument(
        "--temperature",
        "-t",
        type=float,
        default=0.0,
        help="LLM sampling temperature for benchmarking (default: 0.0).",
    )
    parser.add_argument(
        "--sleep",
        "-s",
        type=float,
        default=1.0,
        help="Seconds to sleep between LLM API calls to avoid rate limits (default: 1.0).",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run benchmark against offline Knowledge Store fallback only.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output path for JSON report (default: tests/benchmarks/results/benchmark_<timestamp>.json).",
    )
    return parser.parse_args()


def _display_results(console: Console, report: BenchmarkReport) -> None:
    """Render Rich summary table and acceptance status."""
    table = Table(
        title="\nBenchmark Evaluation Summary Table",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Golden Finding ID", style="cyan")
    table.add_column("Runs", justify="right")
    table.add_column("Schema Pass %", justify="right")
    table.add_column("Concept Pass %", justify="right")
    table.add_column("Overall Pass %", justify="right")
    table.add_column("Mean Latency", justify="right")
    table.add_column("Status", justify="center")

    for golden_id, summary in report.findings_summary.items():
        status_label = (
            "[bold green]PASS[/]" if summary.passed_thresholds else "[bold red]FAIL[/]"
        )
        table.add_row(
            golden_id,
            str(summary.iterations),
            f"{summary.schema_pass_rate * 100:.1f}%",
            f"{summary.concept_pass_rate * 100:.1f}%",
            f"{summary.overall_pass_rate * 100:.1f}%",
            f"{summary.mean_latency_ms:.0f} ms",
            status_label,
        )

    console.print(table)

    pass_str = (
        "[bold green]PASSED (Acceptance Criteria Met)[/]"
        if report.acceptance_criteria_met
        else "[bold red]FAILED (Below Thresholds)[/]"
    )
    console.print(f"\nOverall Benchmark Status: {pass_str}")
    console.print(
        f"• Overall Schema Pass Rate : [bold]{report.overall_schema_pass_rate * 100:.1f}%[/] (target: >= 90.0%)"
    )
    console.print(
        f"• Overall Concept Pass Rate: [bold]{report.overall_concept_pass_rate * 100:.1f}%[/] (target: >= 80.0%)"
    )
    console.print(
        f"• Mean Evaluation Latency : [bold]{report.mean_latency_ms:.0f} ms[/]"
    )


def _save_report(
    report: BenchmarkReport, model_name: str, custom_output: Path | None
) -> Path:
    """Persist benchmark execution results to JSON file."""
    timestamp_slug = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    clean_model_slug = model_name.replace("/", "_").replace(":", "_")
    output_path = custom_output or (
        RESULTS_DIR / f"benchmark_{clean_model_slug}_{timestamp_slug}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return output_path


def run_benchmark() -> int:
    """Execute evaluation benchmark suite and display results."""
    console = Console()
    args = parse_args()

    model_name = args.model or resolve_model(explicit_model=None, api_key=args.api_key)
    mode_str = "OFFLINE (Knowledge Store)" if args.offline else f"ONLINE ({model_name})"

    console.print(
        "\n[bold blue]=== AICriticAgent Validation & Evaluation Benchmark Suite ===[/]"
    )
    console.print(f"Mode: [bold cyan]{mode_str}[/]")
    console.print(
        f"Iterations per finding: [bold]{args.iterations}[/] | Temperature: [bold]{args.temperature}[/]"
    )
    console.print(f"Rate limit delay: [bold]{args.sleep}s[/]\n")

    golden_cases = RemediationEvaluator.load_all_golden(GOLDEN_DIR)
    console.print(
        f"Loaded [bold green]{len(golden_cases)}[/] curated golden benchmark findings.\n"
    )

    agent = AICriticAgent(
        model_name=model_name,
        api_key=args.api_key,
        enable_llm=not args.offline,
    )

    all_results: list[EvaluationResult] = []

    for idx, golden in enumerate(golden_cases, 1):
        console.print(
            f"[{idx}/{len(golden_cases)}] Benchmarking [cyan]{golden.id}[/] ({golden.finding.rule_id} - {golden.finding.title})..."
        )
        for it in range(1, args.iterations + 1):
            t0 = time.perf_counter()
            error_msg: str | None = None
            remediation = None
            try:
                remediation, _ = agent.enhance_finding(golden.finding)
            except Exception as e:  # noqa: BLE001
                error_msg = str(e)

            latency_ms = (time.perf_counter() - t0) * 1000.0

            result = RemediationEvaluator.evaluate(
                golden,
                remediation,
                iteration=it,
                latency_ms=latency_ms,
                error_message=error_msg,
            )
            all_results.append(result)

            status_glyph = "[green]✓[/]" if result.is_pass else "[red]✗[/]"
            console.print(
                f"    Iteration {it:2d}/{args.iterations}: {status_glyph} "
                f"schema={result.schema_valid} concept={result.concept_valid} ({latency_ms:.0f}ms)"
            )

            if not args.offline and args.sleep > 0:
                time.sleep(args.sleep)

    report = RemediationEvaluator.aggregate_results(
        all_results, model_name=model_name if not args.offline else "offline-kb"
    )

    _display_results(console, report)
    saved_path = _save_report(report, model_name, args.output)
    console.print(
        f"\n[green]✓[/] Saved benchmark report JSON to: [bold]{saved_path}[/]\n"
    )

    return 0 if report.acceptance_criteria_met else 1


if __name__ == "__main__":
    raise SystemExit(run_benchmark())
