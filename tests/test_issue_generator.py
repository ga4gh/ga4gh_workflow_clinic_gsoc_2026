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
    f1 = Finding(
        id="hash_1111111111111111111111111111111111111111111111111111111111111111",
        rule_id="W001",
        severity="CRITICAL",
        category="containerization",
        title="Unpinned container in Task A",
        file_path="main.nf",
        line_number=10,
        fingerprint=Fingerprint(
            hash="hash_1111111111111111111111111111111111111111111111111111111111111111"
        ),
    )
    f2 = Finding(
        id="hash_2222222222222222222222222222222222222222222222222222222222222222",
        rule_id="W001",
        severity="MEDIUM",
        category="containerization",
        title="Unpinned container in Task B",
        file_path="main.nf",
        line_number=25,
        fingerprint=Fingerprint(
            hash="hash_2222222222222222222222222222222222222222222222222222222222222222"
        ),
    )
    f3_new = Finding(
        id="hash_3333333333333333333333333333333333333333333333333333333333333333",
        rule_id="W001",
        severity="CRITICAL",
        category="containerization",
        title="Unpinned container in Task C",
        file_path="main.nf",
        line_number=40,
        fingerprint=Fingerprint(
            hash="hash_3333333333333333333333333333333333333333333333333333333333333333"
        ),
    )

    report = DiagnosisReport(
        workflow_name="rnaseq",
        tasks_count=3,
        findings_count=3,
        findings=[f1, f2, f3_new],
    )

    # f1 and f2 are already tracked; only f3_new is untracked
    existing = {
        "hash_1111111111111111111111111111111111111111111111111111111111111111",
        "hash_2222222222222222222222222222222222222222222222222222222222222222",
    }

    issues = generate_issues(report, existing_fingerprints=existing)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.category == "containerization"
    assert issue.fingerprints == [
        "hash_3333333333333333333333333333333333333333333333333333333333333333"
    ]
    assert "Task C" in issue.body
    assert "Task A" not in issue.body
    assert "Task B" not in issue.body


def test_category_grouping_and_severity() -> None:
    """Verify findings are grouped by category domain with correct title and severity."""
    f1 = Finding(
        id="fp_w001",
        rule_id="W001",
        severity="MEDIUM",
        category="containerization",
        title="Unpinned tag",
        file_path="main.nf",
    )
    f2 = Finding(
        id="fp_w002",
        rule_id="W002",
        severity="CRITICAL",
        category="resources",
        title="Missing CPU limit",
        file_path="main.nf",
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
