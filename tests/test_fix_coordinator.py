"""Unit tests for FixCoordinator service orchestration."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from workflow_clinic.models.diagnosis import DiagnosisReport, Finding
from workflow_clinic.models.fix import (
    AppliedProposal,
    FixProposal,
    FixSession,
    FixStrategyLayer,
)
from workflow_clinic.services.coordinator import ExamineConfig, ExamineCoordinator
from workflow_clinic.services.fix_coordinator import (
    FixCallbacks,
    FixConfig,
    FixCoordinator,
    FixDependencies,
    FixResult,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _create_sample_diagnosis(
    target_dir: Path, nf_file_name: str = "poor_practices.nf"
) -> tuple[Path, Path]:
    """Helper to examine poor_practices.nf and create diagnosis.json in tmp_path."""
    poor_fixture = FIXTURES_DIR / "poor_practices.nf"
    dest_nf = target_dir / nf_file_name
    shutil.copy(poor_fixture, dest_nf)

    diag_file = target_dir / "diagnosis.json"
    examine_coordinator = ExamineCoordinator(
        config=ExamineConfig(
            target=str(dest_nf),
            output=diag_file,
        )
    )
    examine_coordinator.run()
    return dest_nf, diag_file


def test_fix_coordinator_resolves_paths_3_ways(tmp_path: Path) -> None:
    """Verify 3-way path resolution: direct json, directory, and specific workflow file."""
    dest_nf, diag_file = _create_sample_diagnosis(tmp_path)

    # 1. Direct JSON file
    coord_json = FixCoordinator(config=FixConfig(target=str(diag_file)))
    diag_p, root_d, file_filter = coord_json.resolve_paths()
    assert diag_p == diag_file.resolve()
    assert root_d == tmp_path.resolve()
    assert file_filter is None

    # 2. Directory path
    coord_dir = FixCoordinator(config=FixConfig(target=str(tmp_path)))
    diag_p2, root_d2, file_filter2 = coord_dir.resolve_paths()
    assert diag_p2 == diag_file.resolve()
    assert root_d2 == tmp_path.resolve()
    assert file_filter2 is None

    # 3. Workflow file path (.nf)
    coord_file = FixCoordinator(config=FixConfig(target=str(dest_nf)))
    diag_p3, root_d3, file_filter3 = coord_file.resolve_paths()
    assert diag_p3 == diag_file.resolve()
    assert root_d3 == tmp_path.resolve()
    assert file_filter3 == dest_nf.resolve()


def test_fix_coordinator_missing_diagnosis_raises_file_not_found(
    tmp_path: Path,
) -> None:
    """Verify coordinator raises FileNotFoundError when diagnosis.json is missing."""
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()

    coordinator = FixCoordinator(config=FixConfig(target=str(empty_dir)))
    with pytest.raises(FileNotFoundError, match=r"Could not find 'diagnosis\.json'"):
        coordinator.resolve_paths()


def test_fix_coordinator_rule_filtering(tmp_path: Path) -> None:
    """Verify rule_ids filter retains only matching findings."""
    _dest_nf, diag_file = _create_sample_diagnosis(tmp_path)

    coordinator = FixCoordinator(
        config=FixConfig(
            target=str(diag_file),
            rules=["W001"],
            dry_run=True,
        )
    )
    diag_path, root_dir, target_filter = coordinator.resolve_paths()
    report = coordinator.load_diagnosis(diag_path)
    actionable = coordinator.get_actionable_findings(report, root_dir, target_filter)

    assert len(actionable) > 0
    for finding in actionable:
        assert finding.rule_id == "W001"


def test_fix_coordinator_rule_filter_unknown_rule_id_returns_empty(
    tmp_path: Path,
) -> None:
    """Verify unknown rule ID filter (e.g. W999) returns empty findings list, not an error."""
    _dest_nf, diag_file = _create_sample_diagnosis(tmp_path)

    coordinator = FixCoordinator(
        config=FixConfig(
            target=str(diag_file),
            rules=["W999"],
            dry_run=True,
        )
    )
    diag_path, root_dir, target_filter = coordinator.resolve_paths()
    report = coordinator.load_diagnosis(diag_path)
    actionable = coordinator.get_actionable_findings(report, root_dir, target_filter)

    assert actionable == []


def test_fix_coordinator_empty_actionable_findings_exits_clean(tmp_path: Path) -> None:
    """Verify coordinator handles empty findings gracefully."""
    empty_diag = tmp_path / "diagnosis.json"
    dummy_report = DiagnosisReport(
        workflow_name="empty",
        findings=[],
        findings_count=0,
    )
    empty_diag.write_text(
        json.dumps(dummy_report.model_dump(mode="json")), encoding="utf-8"
    )

    coordinator = FixCoordinator(config=FixConfig(target=str(empty_diag), dry_run=True))
    result = coordinator.execute()

    assert isinstance(result, FixResult)
    assert len(result.actionable_findings) == 0
    assert len(result.session.proposals) == 0


def test_fix_coordinator_enhance_without_key_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify enhance=True without key logs warning callback, and ai_only=True raises ValueError."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("CLINIC_MODEL", raising=False)

    _dest_nf, diag_file = _create_sample_diagnosis(tmp_path)
    warning_received: list[str] = []

    coordinator = FixCoordinator(
        config=FixConfig(
            target=str(diag_file),
            enhance=True,
            dry_run=True,
        ),
        callbacks=FixCallbacks(on_warning=warning_received.append),
    )
    diag_path, root_dir, target_filter = coordinator.resolve_paths()
    report = coordinator.load_diagnosis(diag_path)
    actionable = coordinator.get_actionable_findings(report, root_dir, target_filter)

    assert len(actionable) > 0
    assert len(warning_received) == 1
    assert "No API key found" in warning_received[0]

    # ai_only without key raises ValueError
    ai_coordinator = FixCoordinator(
        config=FixConfig(
            target=str(diag_file),
            ai_only=True,
            dry_run=True,
        )
    )
    with pytest.raises(ValueError, match="'--ai-only' requires an active LLM API key"):
        ai_coordinator.get_actionable_findings(report, root_dir, target_filter)


def test_fix_coordinator_dry_run_generates_proposals_without_modifying_files(
    tmp_path: Path,
) -> None:
    """Verify dry-run generates proposals but leaves source files on disk untouched."""
    dest_nf, diag_file = _create_sample_diagnosis(tmp_path)
    original_content = dest_nf.read_text(encoding="utf-8")

    coordinator = FixCoordinator(
        config=FixConfig(
            target=str(diag_file),
            dry_run=True,
        )
    )
    result = coordinator.execute()

    assert isinstance(result, FixResult)
    assert result.dry_run is True
    assert len(result.session.proposals) > 0
    # Content on disk MUST remain unchanged in dry_run mode
    assert dest_nf.read_text(encoding="utf-8") == original_content


def test_fix_coordinator_applies_fixes_when_not_dry_run(tmp_path: Path) -> None:
    """Verify non-dry-run applies doctor fixes directly to files in isolated tmp_path."""
    dest_nf, diag_file = _create_sample_diagnosis(tmp_path)
    original_content = dest_nf.read_text(encoding="utf-8")

    applied_calls: list[str] = []
    coordinator = FixCoordinator(
        config=FixConfig(
            target=str(diag_file),
            dry_run=False,
        ),
        callbacks=FixCallbacks(
            on_fix_applied=lambda applied: applied_calls.append(
                applied.proposal.rule_id
            ),
        ),
    )
    result = coordinator.execute()

    assert result.dry_run is False
    assert any(p.applied for p in result.session.applied_proposals)
    assert len(applied_calls) > 0
    # Content on disk MUST be modified
    modified_content = dest_nf.read_text(encoding="utf-8")
    assert modified_content != original_content
    assert "quay.io/biocontainers/" in modified_content


def test_fix_coordinator_group_findings_by_category() -> None:
    """Verify group_findings_by_category groups findings and falls back to _RULE_CATEGORIES."""
    f1 = Finding(
        rule_id="W001",
        category="containerization",
        severity="HIGH",
        message="Unpinned container",
    )
    f2 = Finding(
        rule_id="W002",
        category="resources",
        severity="WARNING",
        message="Missing cpu/memory",
    )
    f3 = Finding(
        rule_id="CUSTOM_RULE",
        category="",
        severity="INFO",
        message="Custom check",
    )

    grouped = FixCoordinator.group_findings_by_category([f1, f2, f3])
    assert "containerization" in grouped
    assert f1 in grouped["containerization"]
    assert "resources" in grouped
    assert f2 in grouped["resources"]
    assert "portability" in grouped
    assert f3 in grouped["portability"]


def test_fix_coordinator_dependency_injection(tmp_path: Path) -> None:
    """Verify custom doctor_runner can be injected into FixDependencies for testing."""
    dest_nf, diag_file = _create_sample_diagnosis(tmp_path)

    mock_runner = MagicMock()
    mock_session = FixSession(
        source=str(tmp_path),
        findings_input=[],
        proposals=[
            FixProposal(
                finding_id="finding_mock_1",
                rule_id="MOCK001",
                category="general",
                target_file=str(dest_nf),
                original_snippet="echo old",
                proposed_snippet="echo new",
                explanation="Mock fix",
                strategy_layer=FixStrategyLayer.LAYER1_AST,
            )
        ],
    )
    mock_runner.run.return_value = mock_session

    coordinator = FixCoordinator(
        config=FixConfig(target=str(diag_file), dry_run=True),
        dependencies=FixDependencies(doctor_runner=mock_runner),
    )
    result = coordinator.execute()

    assert mock_runner.run.called
    assert result.session == mock_session
    assert len(result.session.proposals) == 1
    assert result.session.proposals[0].rule_id == "MOCK001"


def test_fix_coordinator_lifecycle_callbacks_fire(tmp_path: Path) -> None:
    """Verify lifecycle callbacks fire correctly during diagnosis load and execution."""
    dest_nf, diag_file = _create_sample_diagnosis(tmp_path)

    events: list[str] = []
    callbacks = FixCallbacks(
        on_load_start=lambda p: events.append(f"load_start:{p.name}"),
        on_report_loaded=lambda _r, count: events.append(f"loaded:{count}"),
        on_proposals_ready=lambda props: events.append(f"proposals:{len(props)}"),
        on_fix_applied=lambda app: events.append(f"applied:{app.proposal.rule_id}"),
    )

    mock_runner = MagicMock()
    mock_proposal = FixProposal(
        finding_id="f1",
        rule_id="W001",
        category="containerization",
        target_file=str(dest_nf),
        original_snippet="",
        proposed_snippet="",
        explanation="",
        strategy_layer=FixStrategyLayer.LAYER1_AST,
    )
    mock_applied = AppliedProposal(proposal=mock_proposal, applied=True)
    mock_session = FixSession(
        source=str(tmp_path),
        findings_input=[],
        proposals=[mock_proposal],
        applied_proposals=[mock_applied],
    )
    mock_runner.run.return_value = mock_session

    coordinator = FixCoordinator(
        config=FixConfig(target=str(diag_file), dry_run=False),
        callbacks=callbacks,
        dependencies=FixDependencies(doctor_runner=mock_runner),
    )
    result = coordinator.execute()

    assert any(e == "load_start:diagnosis.json" for e in events)
    assert any(e.startswith("loaded:") for e in events)
    assert any(e == "proposals:1" for e in events)
    assert any(e == "applied:W001" for e in events)
    assert result.session == mock_session
