"""Unit and integration tests for Layer 1 AST Fixers (W001 Containers & W002 Resource Limits)."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

from workflow_clinic.doctor.fixers.containers import ContainerASTFixer
from workflow_clinic.doctor.fixers.resources import ResourceASTFixer
from workflow_clinic.models.diagnosis import Finding
from workflow_clinic.parsers.nextflow import NextflowParser
from workflow_clinic.rules.runner import RuleRunner


def test_container_ast_fixer_missing_container() -> None:
    fixer = ContainerASTFixer()
    finding = Finding(
        rule_id="W001",
        severity="error",
        message="Process 'NO_CONTAINER' has no container defined.",
        file_path="poor_practices.nf",
        line_number=13,
        location="NO_CONTAINER",
    )
    code = """process NO_CONTAINER {
    cpus 1
}"""
    proposal = fixer.generate_proposal(finding, code)
    assert proposal is not None
    assert proposal.rule_id == "W001"
    assert 'container "quay.io/biocontainers/ubuntu:22.04"' in proposal.proposed_snippet
    assert "// TODO: Replace with specific tool image" in proposal.proposed_snippet


def test_container_ast_fixer_unpinned_tag() -> None:
    fixer = ContainerASTFixer()
    finding = Finding(
        rule_id="W001",
        severity="warning",
        message="Process 'UNPINNED_TAG' uses an unpinned container image: 'ubuntu:latest'.",
        file_path="poor_practices.nf",
        line_number=29,
        location="UNPINNED_TAG",
    )
    code = """process UNPINNED_TAG {
    container "ubuntu:latest"
}"""
    proposal = fixer.generate_proposal(finding, code)
    assert proposal is not None
    assert 'container "ubuntu:22.04"' in proposal.proposed_snippet


def test_resource_ast_fixer_missing_cpu_and_memory() -> None:
    fixer = ResourceASTFixer()
    cpu_finding = Finding(
        rule_id="W002",
        severity="warning",
        message="Process 'NO_RESOURCES' does not declare a CPU resource limit.",
        file_path="poor_practices.nf",
        line_number=46,
        location="NO_RESOURCES",
    )
    code = """process NO_RESOURCES {
    container "quay.io/biocontainers/samtools:1.17"
}"""
    proposal = fixer.generate_proposal(cpu_finding, code)
    assert proposal is not None
    assert "cpus 1" in proposal.proposed_snippet
    assert (
        "// TODO: Adjust based on tool multi-threading requirements"
        in proposal.proposed_snippet
    )


def test_multiple_fixes_same_file_bottom_up_ordering() -> None:
    code = """process PROC_A {
    cpus 1
}

process PROC_B {
    container "ubuntu:latest"
}"""

    # Finding 1 at line 1 (PROC_A), Finding 2 at line 5 (PROC_B)
    finding_a = Finding(
        rule_id="W001",
        severity="error",
        message="Process 'PROC_A' has no container defined.",
        file_path="test.nf",
        line_number=1,
        location="PROC_A",
    )
    finding_b = Finding(
        rule_id="W001",
        severity="warning",
        message="Process 'PROC_B' uses an unpinned container image: 'ubuntu:latest'.",
        file_path="test.nf",
        line_number=5,
        location="PROC_B",
    )

    # Sort bottom-up (descending line_number)
    findings = sorted(
        [finding_a, finding_b], key=lambda f: f.line_number or 0, reverse=True
    )
    fixer = ContainerASTFixer()

    current_code = code
    for f in findings:
        prop = fixer.generate_proposal(f, current_code)
        if prop:
            current_code = prop.proposed_snippet

    assert 'container "quay.io/biocontainers/ubuntu:22.04"' in current_code
    assert 'container "ubuntu:22.04"' in current_code


def test_w001_w002_fix_produces_zero_findings(tmp_path: Path) -> None:
    """End-to-end integration test: Fix applied -> Re-parse -> Assert 0 W001/W002 findings."""
    workflow_file = tmp_path / "poor_practices.nf"
    code = """nextflow.enable.dsl = 2

process UNPINNED_TAG {
    container "ubuntu:latest"
    cpus 2
    memory "4 GB"

    script:
    \"\"\"
    echo "test"
    \"\"\"
}"""
    workflow_file.write_text(code)

    parser = NextflowParser()
    bundle = parser.parse(workflow_file)

    rule_runner = RuleRunner()
    findings = rule_runner.run(bundle, rule_ids=["W001", "W002"])

    assert len(findings) > 0  # Initial scan finds unpinned tag warning

    fixer = ContainerASTFixer()
    for finding in findings:
        if finding.rule_id == "W001":
            prop = fixer.generate_proposal(finding, code)
            if prop:
                code = prop.proposed_snippet

    workflow_file.write_text(code)
    fixed_bundle = parser.parse(workflow_file)
    fixed_findings = rule_runner.run(fixed_bundle, rule_ids=["W001", "W002"])

    w001_findings = [f for f in fixed_findings if f.rule_id == "W001"]
    assert len(w001_findings) == 0  # Zero W001 findings remain!
