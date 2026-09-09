"""Unit tests for ExamineCoordinator service orchestration."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from workflow_clinic.exceptions import UnsupportedWorkflowError
from workflow_clinic.services.coordinator import (
    ExamineCallbacks,
    ExamineConfig,
    ExamineCoordinator,
    ExamineDependencies,
    ExamineResult,
)


def test_coordinator_clean_workflow(tmp_path: Path) -> None:
    """Verify coordinator processes a valid, clean workflow without findings."""
    dummy_path = Path(__file__).parent / "fixtures" / "dummy.nf"
    out_file = tmp_path / "custom_diagnosis.json"

    coordinator = ExamineCoordinator(
        config=ExamineConfig(
            target=str(dummy_path),
            output=out_file,
        )
    )

    result = coordinator.run()

    assert isinstance(result, ExamineResult)
    assert result.parser_name == "nextflow"
    assert result.bundle.metadata.name == "dummy"
    assert result.report.findings_count == 0
    assert out_file.exists()

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["findings_count"] == 0


def test_coordinator_flawed_workflow_generates_fingerprints(tmp_path: Path) -> None:
    """Verify coordinator runs static rules and computes finding fingerprints."""
    poor_path = Path(__file__).parent / "fixtures" / "poor_practices.nf"
    out_file = tmp_path / "diagnosis.json"

    coordinator = ExamineCoordinator(
        config=ExamineConfig(
            target=str(poor_path),
            output=out_file,
        )
    )

    result = coordinator.run()

    assert result.report.findings_count > 0
    for finding in result.report.findings:
        assert finding.id is not None
        assert finding.fingerprint is not None
        assert len(finding.fingerprint.hash) == 64

    assert out_file.exists()


def test_coordinator_nonexistent_target_raises_file_not_found(tmp_path: Path) -> None:
    """Verify coordinator raises FileNotFoundError when target path is missing."""
    missing_path = tmp_path / "nonexistent" / "workflow.nf"
    coordinator = ExamineCoordinator(config=ExamineConfig(target=str(missing_path)))

    with pytest.raises(FileNotFoundError, match="does not exist"):
        coordinator.run()


def test_coordinator_unsupported_workflow_raises_error(tmp_path: Path) -> None:
    """Verify coordinator raises UnsupportedWorkflowError on unrecognized file."""
    unsupported_file = tmp_path / "test.xyz"
    unsupported_file.write_text("random content", encoding="utf-8")

    coordinator = ExamineCoordinator(config=ExamineConfig(target=str(unsupported_file)))

    with pytest.raises(UnsupportedWorkflowError):
        coordinator.run()


def test_coordinator_callbacks_triggered(tmp_path: Path) -> None:
    """Verify coordinator triggers lifecycle hooks during examination."""
    dummy_path = Path(__file__).parent / "fixtures" / "dummy.nf"
    out_file = tmp_path / "diagnosis.json"

    on_scan_start = MagicMock()
    on_report_saved = MagicMock()

    coordinator = ExamineCoordinator(
        config=ExamineConfig(
            target=str(dummy_path),
            output=out_file,
        ),
        callbacks=ExamineCallbacks(
            on_scan_start=on_scan_start,
            on_report_saved=on_report_saved,
        ),
    )

    result = coordinator.run()

    assert result.report.findings_count == 0
    on_scan_start.assert_called_once_with("dummy.nf")
    on_report_saved.assert_called_once_with(out_file)


def test_coordinator_context_manager_cleanup_observable() -> None:
    """Verify coordinator cleans up cloned temp directory upon context exit."""
    recorded_temp_dir: Path | None = None

    def fake_clone(_url: str, dest: Path) -> Path:
        nonlocal recorded_temp_dir
        recorded_temp_dir = dest
        (dest / "workflow.nf").write_text("process A { }", encoding="utf-8")
        return dest

    config = ExamineConfig(target="https://github.com/fake/repo.git")
    dependencies = ExamineDependencies(clone_repo_fn=fake_clone)

    with ExamineCoordinator(config=config, dependencies=dependencies) as coordinator:
        scan_path = coordinator.resolve_scan_path()
        assert scan_path.exists()
        assert recorded_temp_dir is not None
        assert recorded_temp_dir.exists()

    assert not recorded_temp_dir.exists()


def test_coordinator_enhance_without_key_uses_knowledge_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify coordinator falls back to Knowledge Store when --enhance has no API key."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    monkeypatch.delenv("CLINIC_MODEL", raising=False)

    poor_path = Path(__file__).parent / "fixtures" / "poor_practices.nf"
    out_file = tmp_path / "diagnosis.json"

    config = ExamineConfig(
        target=str(poor_path),
        output=out_file,
        enhance=True,
        model="gemini/gemini-3.6-flash",
        api_key=None,
    )
    dependencies = ExamineDependencies(load_dotenv_fn=lambda **_kwargs: None)
    coordinator = ExamineCoordinator(config=config, dependencies=dependencies)

    result = coordinator.run()
    assert result.enhance_failed is False
    assert result.report.findings_count > 0
    assert result.fallback_count == result.report.findings_count
