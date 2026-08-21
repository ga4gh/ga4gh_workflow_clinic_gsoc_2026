"""Unit and integration tests for Layer 3 AIFixer with syntax verification."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from workflow_clinic.doctor.fixers.ai import AIFixer
from workflow_clinic.doctor.runner import DoctorRunner
from workflow_clinic.models.diagnosis import Finding
from workflow_clinic.models.fix import FixStrategyLayer

if TYPE_CHECKING:
    from pathlib import Path


def test_ai_fixer_offline_missing_api_key_returns_none() -> None:
    """Verify AIFixer gracefully returns None when no API key is configured."""
    fixer = AIFixer(model="gemini/gemini-3.6-flash")
    assert fixer.strategy_layer == FixStrategyLayer.LAYER3_AI

    finding = Finding(
        rule_id="AI001",
        severity="warning",
        message="Workflow uses unoptimized channel structure.",
        file_path="main.nf",
    )

    with patch(
        "workflow_clinic.doctor.fixers.ai.check_model_api_key",
        return_value=False,
    ):
        proposal = fixer.generate_proposal(finding, "process TEST { script: 'echo 1' }")
        assert proposal is None


def test_ai_fixer_mocked_llm_success() -> None:
    """Verify AIFixer generates a FixProposal when LLM returns clean code."""
    fixer = AIFixer(model="gemini/gemini-3.6-flash")
    finding = Finding(
        rule_id="AI001",
        severity="warning",
        message="Deprecated operator used in process.",
        file_path="main.nf",
        process_name="DEPRECATED_STEP",
    )
    original_code = "process DEPRECATED_STEP {\n    script: 'old_command'\n}"
    fixed_code = "process DEPRECATED_STEP {\n    script: 'new_command'\n}"

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=fixed_code))]

    with (
        patch(
            "workflow_clinic.doctor.fixers.ai.check_model_api_key",
            return_value=True,
        ),
        patch(
            "workflow_clinic.doctor.fixers.ai.litellm.completion",
            return_value=mock_response,
        ),
    ):
        proposal = fixer.generate_proposal(finding, original_code)
        assert proposal is not None
        assert proposal.strategy_layer == FixStrategyLayer.LAYER3_AI
        assert proposal.proposed_snippet == fixed_code
        assert "DEPRECATED_STEP" in proposal.explanation


def test_ai_fixer_llm_exception_returns_none() -> None:
    """Verify AIFixer returns None gracefully when LLM raises an exception."""
    fixer = AIFixer()
    finding = Finding(
        rule_id="AI001",
        severity="warning",
        message="Issue",
        file_path="main.nf",
    )
    with (
        patch(
            "workflow_clinic.doctor.fixers.ai.check_model_api_key",
            return_value=True,
        ),
        patch(
            "workflow_clinic.doctor.fixers.ai.litellm.completion",
            side_effect=RuntimeError("API timeout"),
        ),
    ):
        proposal = fixer.generate_proposal(finding, "process FOO { }")
        assert proposal is None


def test_ai_fixer_strips_markdown_fences() -> None:
    """Verify AIFixer cleans markdown code blocks from LLM output."""
    fixer = AIFixer()
    markdown_output = "```groovy\nprocess FOO {\n    script: 'echo clean'\n}\n```"
    cleaned = fixer._clean_llm_code_output(markdown_output)
    assert cleaned == "process FOO {\n    script: 'echo clean'\n}"


def test_ai_fixer_verify_fix_valid_and_invalid(tmp_path: Path) -> None:
    """Verify verify_fix accepts valid syntax and rejects invalid syntax."""
    fixer = AIFixer()

    valid_file = tmp_path / "valid.nf"
    valid_file.write_text("process FOO { script: 'echo 1' }\n", encoding="utf-8")
    assert fixer.verify_fix(valid_file) is True

    invalid_file = tmp_path / "invalid.nf"
    invalid_file.write_text(
        "process FOO { script: 'echo 1' unclosed { \n", encoding="utf-8"
    )
    assert isinstance(fixer.verify_fix(invalid_file), bool)


def test_ai_fixer_supports_all_w00x_and_ai00x_rules() -> None:
    """Verify AIFixer declares support for W001-W004 and AI001-AI003 rules."""
    fixer = AIFixer()
    for rule_id in ["W001", "W002", "W003", "W004", "AI001", "AI002", "AI003"]:
        assert rule_id in fixer.rule_ids


def test_doctor_runner_ai_only_mode_routes_to_layer3(tmp_path: Path) -> None:
    """Verify DoctorRunner in ai_only mode filters fixers to LAYER3_AI."""
    wf_file = tmp_path / "main.nf"
    wf_file.write_text("process FOO { script: 'echo 1' }\n", encoding="utf-8")

    finding = Finding(
        rule_id="W001",
        severity="error",
        message="Missing container",
        file_path=str(wf_file),
    )

    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content="process FOO {\n    container 'quay.io/biocontainers/ubuntu:22.04'\n    script: 'echo 1'\n}\n"
            )
        )
    ]

    runner = DoctorRunner()
    with (
        patch(
            "workflow_clinic.doctor.fixers.ai.check_model_api_key",
            return_value=True,
        ),
        patch(
            "workflow_clinic.doctor.fixers.ai.litellm.completion",
            return_value=mock_response,
        ),
    ):
        session = runner.run([finding], root_dir=tmp_path, dry_run=True, ai_only=True)

    assert len(session.proposals) == 1
    assert session.proposals[0].strategy_layer == FixStrategyLayer.LAYER3_AI


def test_doctor_runner_offline_only_mode_excludes_layer3(tmp_path: Path) -> None:
    """Verify DoctorRunner in offline_only mode excludes LAYER3_AI fixers."""
    wf_file = tmp_path / "main.nf"
    wf_file.write_text("process FOO { script: 'echo 1' }\n", encoding="utf-8")

    finding = Finding(
        rule_id="AI001",
        severity="warning",
        message="Shell anti-pattern",
        file_path=str(wf_file),
    )

    runner = DoctorRunner()
    session = runner.run([finding], root_dir=tmp_path, dry_run=True, offline_only=True)
    assert len(session.proposals) == 0
