"""Unit and integration tests for the Workflow Clinic rule engine."""

import importlib.util
from pathlib import Path

import pytest

from workflow_clinic.models import Task, TaskResources, WorkflowBundle, WorkflowMetadata
from workflow_clinic.parsers import ParserRegistry
from workflow_clinic.rules import (
    BaseRule,
    Finding,
    PinnedContainerRule,
    ResourceLimitsRule,
    RuleRegistry,
    RuleRunner,
    Severity,
)


class DummyTestRule(BaseRule):
    """Simple test rule class."""

    id = "T001"
    name = "Dummy Test Rule"
    description = "Test rule for unit testing."

    def check(self, bundle: WorkflowBundle) -> list[Finding]:
        """Produce a single mock finding if there are any tasks."""
        if not bundle.tasks:
            return []
        return [
            Finding(
                rule_id=self.id,
                message="Mock finding",
                severity=Severity.INFO,
                task_id=bundle.tasks[0].id,
                location=bundle.tasks[0].name,
            )
        ]


def test_registry_registration_and_retrieval() -> None:
    """Verify that rules can be registered, listed, and retrieved."""
    # Record original rules to restore later
    original_rules = RuleRegistry._rules.copy()
    RuleRegistry.clear()

    try:
        assert len(RuleRegistry.list_rule_ids()) == 0

        # Register
        RuleRegistry.register(DummyTestRule)
        assert len(RuleRegistry.list_rule_ids()) == 1
        assert DummyTestRule.id in RuleRegistry.list_rule_ids()

        # Duplicate register should raise ValueError
        with pytest.raises(ValueError, match="already registered"):
            RuleRegistry.register(DummyTestRule)

        # Retrieve
        rule_instance = RuleRegistry.get_rule(DummyTestRule.id)
        assert isinstance(rule_instance, DummyTestRule)
        assert rule_instance.id == DummyTestRule.id

        # Missing rule retrieval should raise KeyError
        with pytest.raises(KeyError, match="No rule registered"):
            RuleRegistry.get_rule("NON_EXISTENT")

        # Get all
        all_rules = RuleRegistry.get_all_rules()
        assert len(all_rules) == 1
        assert isinstance(all_rules[0], DummyTestRule)

    finally:
        # Restore original rules
        RuleRegistry._rules = original_rules


def test_runner_run_filtering() -> None:
    """Verify that the RuleRunner can run all or a filtered subset of rules."""
    original_rules = RuleRegistry._rules.copy()
    RuleRegistry.clear()

    try:

        class AnotherDummyRule(BaseRule):
            id = "T002"
            name = "Another Dummy Rule"
            description = "Checks nothing."

            def check(self, _bundle: WorkflowBundle) -> list[Finding]:
                return [
                    Finding(
                        rule_id=self.id,
                        message="Another mock finding",
                        severity=Severity.WARNING,
                    )
                ]

        RuleRegistry.register(DummyTestRule)
        RuleRegistry.register(AnotherDummyRule)

        bundle = WorkflowBundle(
            metadata=WorkflowMetadata(name="test"),
            tasks=[Task(id="1", name="Task 1")],
        )
        runner = RuleRunner()

        # Run all
        findings = runner.run(bundle)
        assert len(findings) == 2
        rule_ids = {f.rule_id for f in findings}
        assert rule_ids == {"T001", "T002"}

        # Run filtered
        findings_filtered = runner.run(bundle, rule_ids=["T002"])
        assert len(findings_filtered) == 1
        assert findings_filtered[0].rule_id == "T002"

    finally:
        RuleRegistry._rules = original_rules


def test_pinned_container_rule() -> None:
    """Verify container pinning validations flag expected unpinned/missing targets."""
    rule = PinnedContainerRule()

    # Pinned case
    pinned_task = Task(
        id="t1",
        name="Task1",
        resources=TaskResources(
            container="quay.io/biocontainers/fastqc:0.12.1--hdfd78af_0"
        ),
    )
    bundle_pinned = WorkflowBundle(
        metadata=WorkflowMetadata(name="test"), tasks=[pinned_task]
    )
    assert len(rule.check(bundle_pinned)) == 0

    # Digest case
    digest_task = Task(
        id="t2",
        name="Task2",
        resources=TaskResources(
            container="ubuntu@sha256:7783a654d0085a6a68f000bb7ccba88d8b945d8b767"
        ),
    )
    bundle_digest = WorkflowBundle(
        metadata=WorkflowMetadata(name="test"), tasks=[digest_task]
    )
    assert len(rule.check(bundle_digest)) == 0

    # Missing case
    missing_task = Task(
        id="t3",
        name="Task3",
        resources=TaskResources(container=None),
    )
    bundle_missing = WorkflowBundle(
        metadata=WorkflowMetadata(name="test"), tasks=[missing_task]
    )
    findings_missing = rule.check(bundle_missing)
    assert len(findings_missing) == 1
    assert findings_missing[0].severity == Severity.ERROR
    assert "no container defined" in findings_missing[0].message

    # Tagless case
    tagless_task = Task(
        id="t4",
        name="Task4",
        resources=TaskResources(container="ubuntu"),
    )
    bundle_tagless = WorkflowBundle(
        metadata=WorkflowMetadata(name="test"), tasks=[tagless_task]
    )
    findings_tagless = rule.check(bundle_tagless)
    assert len(findings_tagless) == 1
    assert findings_tagless[0].severity == Severity.WARNING
    assert "unpinned container image" in findings_tagless[0].message

    # Latest tag case
    latest_task = Task(
        id="t5",
        name="Task5",
        resources=TaskResources(container="ubuntu:latest"),
    )
    bundle_latest = WorkflowBundle(
        metadata=WorkflowMetadata(name="test"), tasks=[latest_task]
    )
    findings_latest = rule.check(bundle_latest)
    assert len(findings_latest) == 1
    assert findings_latest[0].severity == Severity.WARNING
    assert "ubuntu:latest" in findings_latest[0].message


def test_resource_limits_rule() -> None:
    """Verify resource limits validations detect missing CPUs or memory."""
    rule = ResourceLimitsRule()

    # Valid case
    valid_task = Task(
        id="t1",
        name="Task1",
        resources=TaskResources(cpus=2, memory="4 GB"),
    )
    bundle_valid = WorkflowBundle(
        metadata=WorkflowMetadata(name="test"), tasks=[valid_task]
    )
    assert len(rule.check(bundle_valid)) == 0

    # Missing CPU case
    no_cpu_task = Task(
        id="t2",
        name="Task2",
        resources=TaskResources(cpus=None, memory="4 GB"),
    )
    bundle_no_cpu = WorkflowBundle(
        metadata=WorkflowMetadata(name="test"), tasks=[no_cpu_task]
    )
    findings_no_cpu = rule.check(bundle_no_cpu)
    assert len(findings_no_cpu) == 1
    assert findings_no_cpu[0].severity == Severity.WARNING
    assert "CPU resource limit" in findings_no_cpu[0].message

    # Missing Memory case
    no_mem_task = Task(
        id="t3",
        name="Task3",
        resources=TaskResources(cpus=2, memory=None),
    )
    bundle_no_mem = WorkflowBundle(
        metadata=WorkflowMetadata(name="test"), tasks=[no_mem_task]
    )
    findings_no_mem = rule.check(bundle_no_mem)
    assert len(findings_no_mem) == 1
    assert findings_no_mem[0].severity == Severity.WARNING
    assert "memory resource limit" in findings_no_mem[0].message


@pytest.mark.skipif(
    importlib.util.find_spec("groovy_parser") is None,
    reason="Nextflow support not installed",
)
def test_rules_end_to_end_with_fixtures() -> None:
    """Integration test: execute rules runner on realistic NF fixtures."""
    # Positive control: dummy.nf has zero findings
    p_dummy = Path(__file__).parent / "fixtures" / "dummy.nf"
    parser_dummy = ParserRegistry.get_parser(ParserRegistry.detect_parser(p_dummy))
    bundle_dummy = parser_dummy.parse(p_dummy)

    runner = RuleRunner()
    findings_dummy = runner.run(bundle_dummy)
    assert len(findings_dummy) == 0

    # Negative control: poor_practices.nf has exactly 6 findings
    p_poor = Path(__file__).parent / "fixtures" / "poor_practices.nf"
    parser_poor = ParserRegistry.get_parser(ParserRegistry.detect_parser(p_poor))
    bundle_poor = parser_poor.parse(p_poor)

    findings_poor = runner.run(bundle_poor)
    assert len(findings_poor) == 6

    # Validate specifics of the 6 poor_practices findings
    w001_findings = [f for f in findings_poor if f.rule_id == "W001"]
    w002_findings = [f for f in findings_poor if f.rule_id == "W002"]

    assert len(w001_findings) == 3
    assert len(w002_findings) == 3

    errors = [f for f in findings_poor if f.severity == Severity.ERROR]
    warnings = [f for f in findings_poor if f.severity == Severity.WARNING]
    infos = [f for f in findings_poor if f.severity == Severity.INFO]

    assert len(errors) == 1
    assert len(warnings) == 4
    assert len(infos) == 1

    # Processes check
    processes_with_issues = {f.location for f in findings_poor if f.location}
    assert processes_with_issues == {
        "NO_CONTAINER",
        "UNPINNED_TAG",
        "NO_RESOURCES",
        "TAGLESS_IMAGE",
    }
