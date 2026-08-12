"""Unit tests for issue_generator module."""

from workflow_clinic.models.diagnosis import (
    DiagnosisReport,
    Finding,
    Fingerprint,
    Remediation,
)
from workflow_clinic.reporting.issue_generator import (
    extract_fingerprints,
    filter_new_findings,
    generate_issues,
    group_findings,
)


def test_extract_fingerprints_regex() -> None:
    """Verify extract_fingerprints correctly parses hidden HTML comments from issue body."""
    sample_markdown = """
    ## Issue Title
    Some text here.
    <!-- workflow-clinic:fingerprint:a1b2c3d4e5f678901234567890abcdef1234567890abcdef1234567890abcdef -->
    More text.
    <!-- WORKFLOW-CLINIC:FINGERPRINT:9999999999999999999999999999999999999999999999999999999999999999 -->
    """
    fps = extract_fingerprints(sample_markdown)
    assert len(fps) == 2
    assert "a1b2c3d4e5f678901234567890abcdef1234567890abcdef1234567890abcdef" in fps
    assert "9999999999999999999999999999999999999999999999999999999999999999" in fps


def test_mixed_scenario_finding_level_deduplication() -> None:
    """Verify deduplication filters findings before grouping, preventing partial duplicate issues."""
    f1_hash = "1111111111111111111111111111111111111111111111111111111111111111"
    f2_hash = "2222222222222222222222222222222222222222222222222222222222222222"
    f3_hash = "3333333333333333333333333333333333333333333333333333333333333333"

    f1 = Finding(
        id=f1_hash,
        rule_id="W001",
        severity="CRITICAL",
        category="containerization",
        title="Unpinned container in Task A",
        file_path="main.nf",
        line_number=10,
        fingerprint=Fingerprint(hash=f1_hash),
    )
    f2 = Finding(
        id=f2_hash,
        rule_id="W001",
        severity="MEDIUM",
        category="containerization",
        title="Unpinned container in Task B",
        file_path="main.nf",
        line_number=25,
        fingerprint=Fingerprint(hash=f2_hash),
    )
    f3_new = Finding(
        id=f3_hash,
        rule_id="W001",
        severity="CRITICAL",
        category="containerization",
        title="Unpinned container in Task C",
        file_path="main.nf",
        line_number=40,
        fingerprint=Fingerprint(hash=f3_hash),
    )

    report = DiagnosisReport(
        workflow_name="rnaseq",
        tasks_count=3,
        findings_count=3,
        findings=[f1, f2, f3_new],
    )

    # f1 and f2 are already tracked; only f3_new is untracked
    existing = {f1_hash, f2_hash}

    issues = generate_issues(report, existing_fingerprints=existing)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.category == "containerization"
    assert issue.fingerprints == [f3_hash]
    assert "Task C" in issue.body
    assert "Task A" not in issue.body
    assert "Task B" not in issue.body


def test_category_grouping_and_severity() -> None:
    """Verify findings are grouped by category domain with correct title and severity."""
    f1_hash = "a000000000000000000000000000000000000000000000000000000000000001"
    f2_hash = "b000000000000000000000000000000000000000000000000000000000000002"

    f1 = Finding(
        id=f1_hash,
        rule_id="W001",
        severity="MEDIUM",
        category="containerization",
        title="Unpinned tag",
        file_path="main.nf",
        fingerprint=Fingerprint(hash=f1_hash),
    )
    f2 = Finding(
        id=f2_hash,
        rule_id="W002",
        severity="CRITICAL",
        category="resources",
        title="Missing CPU limit",
        file_path="main.nf",
        fingerprint=Fingerprint(hash=f2_hash),
        remediation=Remediation(
            summary="Add cpus 2 directive",
            explanation="Cloud runners require resource bounds",
            code_example="cpus = 2",
        ),
    )

    issues = group_findings([f1, f2])
    assert len(issues) == 2

    categories = {iss.category: iss for iss in issues}
    assert "containerization" in categories
    assert "resources" in categories

    res_issue = categories["resources"]
    assert res_issue.severity == "CRITICAL"
    assert "Add cpus 2 directive" in res_issue.body
    assert "```groovy\ncpus = 2\n```" in res_issue.body


def test_empty_report_handling() -> None:
    """Verify empty report returns empty list of issues."""
    report = DiagnosisReport(
        workflow_name="clean", tasks_count=5, findings_count=0, findings=[]
    )
    assert generate_issues(report) == []
    assert filter_new_findings([]) == []
    assert group_findings([]) == []


def test_invalid_fingerprint_warning_logging(caplog) -> None:
    """Verify warning log is emitted when finding has invalid or missing SHA-256 fingerprint."""
    f_invalid = Finding(
        id="non_hex_id",
        rule_id="W001",
        severity="HIGH",
        category="containerization",
        title="Invalid hash finding",
        file_path="workflow.wdl",
    )

    with caplog.at_level("WARNING"):
        issues = group_findings([f_invalid])

    assert len(issues) == 1
    assert "contains invalid non-SHA256 fingerprint hash" in caplog.text


def test_parameterized_code_language_detection() -> None:
    """Verify code block syntax highlighting infers language from file extensions."""
    f_wdl = Finding(
        id="1111111111111111111111111111111111111111111111111111111111111111",
        rule_id="W002",
        severity="HIGH",
        category="resources",
        title="Missing runtime memory",
        file_path="task.wdl",
        remediation=Remediation(
            summary="Add memory directive",
            explanation="Cloud runners require memory bounds",
            code_example="runtime { memory: '4 GB' }",
        ),
    )

    f_cwl = Finding(
        id="2222222222222222222222222222222222222222222222222222222222222222",
        rule_id="W002",
        severity="MEDIUM",
        category="resources",
        title="Missing CWL requirement",
        file_path="tool.cwl",
        remediation=Remediation(
            summary="Add ResourceRequirement",
            explanation="Cloud runners require memory bounds",
            code_example="ResourceRequirement: ramMin: 4096",
        ),
    )

    issues = group_findings([f_wdl, f_cwl])
    wdl_body = next(iss.body for iss in issues if "task.wdl" in iss.body)
    cwl_body = next(iss.body for iss in issues if "tool.cwl" in iss.body)

    assert "```wdl\nruntime { memory: '4 GB' }\n```" in wdl_body
    assert "```yaml\nResourceRequirement: ramMin: 4096\n```" in cwl_body
