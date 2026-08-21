"""Unit tests for BaseFixer and FixerRegistry implementation and cascade ordering."""

from pathlib import Path

import pytest

from workflow_clinic.doctor import BaseFixer, DoctorRunner, FixerRegistry
from workflow_clinic.models.diagnosis import Finding, Fingerprint
from workflow_clinic.models.fix import (
    FixProposal,
    FixStrategyLayer,
)
from workflow_clinic.models.workflow_bundle import WorkflowBundle


class DummyASTFixer(BaseFixer):
    """Dummy Layer 1 AST fixer for testing."""

    rule_id = "W001"
    strategy_layer = FixStrategyLayer.LAYER1_AST

    def generate_proposal(
        self,
        finding: Finding,
        bundle: WorkflowBundle | None = None,  # noqa: ARG002
        source_code: str | None = None,  # noqa: ARG002
    ) -> FixProposal | None:
        return FixProposal(
            finding_id=finding.id,
            rule_id=self.rule_id,
            category=finding.category,
            target_file=finding.file_path,
            original_snippet="old_code",
            proposed_snippet="new_code",
            explanation="Dummy AST fix",
            strategy_layer=self.strategy_layer,
        )


class DummyRegexFixer(BaseFixer):
    """Dummy Layer 2 Regex fixer for testing."""

    rule_id = "W001"
    strategy_layer = FixStrategyLayer.LAYER2_REGEX

    def generate_proposal(
        self,
        finding: Finding,
        bundle: WorkflowBundle | None = None,  # noqa: ARG002
        source_code: str | None = None,  # noqa: ARG002
    ) -> FixProposal | None:
        return FixProposal(
            finding_id=finding.id,
            rule_id=self.rule_id,
            category=finding.category,
            target_file=finding.file_path,
            original_snippet="old_code",
            proposed_snippet="regex_fixed_code",
            explanation="Dummy Regex fix",
            strategy_layer=self.strategy_layer,
        )


class DummyPathFixer(BaseFixer):
    """Dummy Layer 2 Regex fixer for W003."""

    rule_id = "W003"
    strategy_layer = FixStrategyLayer.LAYER2_REGEX

    def generate_proposal(
        self,
        finding: Finding,
        bundle: WorkflowBundle | None = None,  # noqa: ARG002
        source_code: str | None = None,  # noqa: ARG002
    ) -> FixProposal | None:
        return FixProposal(
            finding_id=finding.id,
            rule_id=self.rule_id,
            category=finding.category,
            target_file=finding.file_path,
            original_snippet="/abs/path",
            proposed_snippet="params.input",
            explanation="Replace absolute path",
            strategy_layer=self.strategy_layer,
        )


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    """Clear registry during test and restore built-in fixers after test."""
    original_fixers = dict(FixerRegistry._fixers)
    FixerRegistry.clear()
    yield
    FixerRegistry.clear()
    FixerRegistry._fixers.update(original_fixers)


def test_fixer_registration_and_can_fix() -> None:
    """Verify fixer registration decorator and can_fix matching."""
    FixerRegistry.register(DummyASTFixer)
    fixers = FixerRegistry.get_all_fixers()

    assert len(fixers) == 1
    fixer = fixers[0]
    assert fixer.rule_id == "W001"
    assert fixer.strategy_layer == FixStrategyLayer.LAYER1_AST
    assert FixerRegistry.has_fixer("W001") is True
    assert FixerRegistry.has_fixer("W999") is False

    finding_w001 = Finding(
        id="h1",
        rule_id="W001",
        severity="HIGH",
        category="containerization",
        title="Unpinned container",
        file_path="main.nf",
        fingerprint=Fingerprint(hash="h1"),
    )
    finding_w002 = Finding(
        id="h2",
        rule_id="W002",
        severity="HIGH",
        category="resources",
        title="Missing cpus",
        file_path="main.nf",
        fingerprint=Fingerprint(hash="h2"),
    )

    assert fixer.can_fix(finding_w001) is True
    assert fixer.can_fix(finding_w002) is False


def test_has_fixer_returns_correct_bool() -> None:
    """Verify has_fixer returns True for registered rules and False for unknown rules."""
    FixerRegistry.register(DummyASTFixer)
    assert FixerRegistry.has_fixer("W001") is True
    assert FixerRegistry.has_fixer("W999") is False


def test_registry_cascade_ordering() -> None:
    """Verify get_fixer_chain returns fixers ordered by strategy layer (LAYER1_AST before LAYER2_REGEX)."""
    # Register Regex first, AST second to test sorting
    FixerRegistry.register(DummyRegexFixer)
    FixerRegistry.register(DummyASTFixer)

    chain = FixerRegistry.get_fixer_chain("W001")
    assert len(chain) == 2
    assert chain[0].strategy_layer == FixStrategyLayer.LAYER1_AST
    assert chain[1].strategy_layer == FixStrategyLayer.LAYER2_REGEX


def test_register_duplicate_fixer_raises_value_error() -> None:
    """Verify attempting to register duplicate fixer for same rule_id and layer raises ValueError."""
    FixerRegistry.register(DummyASTFixer)
    with pytest.raises(ValueError, match="Duplicate fixer registration"):
        FixerRegistry.register(DummyASTFixer)


def test_registry_unknown_rule_id_returns_empty_chain() -> None:
    """Verify get_fixer_chain for unregistered rule_id returns an empty list."""
    chain = FixerRegistry.get_fixer_chain("W999")
    assert chain == []


def test_base_fixer_apply_fix_success_and_failure(tmp_path: Path) -> None:
    """Verify apply_fix modifies file on disk when original snippet is present and fails cleanly otherwise."""
    FixerRegistry.register(DummyPathFixer)
    fixer = FixerRegistry.get_fixer_chain("W003")[0]

    target_file = tmp_path / "main.nf"
    target_file.write_text("input_file = '/abs/path'\n", encoding="utf-8")

    prop = FixProposal(
        finding_id="h1",
        rule_id="W003",
        category="portability",
        target_file="main.nf",
        original_snippet="/abs/path",
        proposed_snippet="params.input",
        explanation="Replace absolute path",
        strategy_layer=FixStrategyLayer.LAYER2_REGEX,
    )

    outcome = fixer.apply_fix(prop, root_dir=tmp_path)
    assert outcome.success is True
    assert outcome.verification_passed is True
    assert target_file.read_text(encoding="utf-8") == "input_file = 'params.input'\n"

    # Test failure when original snippet is missing
    outcome_fail = fixer.apply_fix(prop, root_dir=tmp_path)
    assert outcome_fail.success is False
    assert "Original snippet not found" in str(outcome_fail.failure_reason)


def test_doctor_runner_cascade(tmp_path: Path) -> None:
    """Verify DoctorRunner executes cascade in priority order and tracks session results."""
    FixerRegistry.register(DummyPathFixer)

    target_file = tmp_path / "main.nf"
    target_file.write_text("input_file = '/abs/path'\n", encoding="utf-8")

    finding = Finding(
        id="h1",
        rule_id="W003",
        severity="HIGH",
        category="portability",
        title="Absolute path",
        file_path="main.nf",
        fingerprint=Fingerprint(hash="h1"),
    )

    runner = DoctorRunner()
    session = runner.run([finding], root_dir=tmp_path)

    assert len(session.proposals) == 1
    assert session.applied_count == 1
    assert session.failed_count == 0
    assert len(session.modified_files) == 1
    assert target_file.read_text(encoding="utf-8") == "input_file = 'params.input'\n"


def test_apply_fix_line_targeted_multiple_occurrences(tmp_path: Path) -> None:
    """Verify apply_fix targets the occurrence closest to proposal.line_number when identical snippets exist."""
    content = "process FOO {\n    cpus 1\n}\n\nprocess BAR {\n    cpus 1\n}\n"
    target_file = tmp_path / "multi.nf"
    target_file.write_text(content, encoding="utf-8")

    fixer = DummyRegexFixer()
    # Target line 6 (inside process BAR)
    prop = FixProposal(
        finding_id="h2",
        rule_id="W001",
        category="containerization",
        target_file="multi.nf",
        original_snippet="cpus 1",
        proposed_snippet="cpus 4",
        explanation="Upgrade BAR cpus",
        strategy_layer=FixStrategyLayer.LAYER2_REGEX,
        line_number=6,
    )

    outcome = fixer.apply_fix(prop, root_dir=tmp_path)
    assert outcome.success is True
    updated_content = target_file.read_text(encoding="utf-8")

    # FOO at line 2 must be untouched
    assert "process FOO {\n    cpus 1\n}" in updated_content
    # BAR at line 6 must be patched
    assert "process BAR {\n    cpus 4\n}" in updated_content
