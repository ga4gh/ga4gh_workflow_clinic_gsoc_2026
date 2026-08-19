"""Unit and integration tests for examine command CLI critic integration."""

import importlib.util
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from workflow_clinic.cli import PROVIDER_MODEL_MAP, _resolve_model, app
from workflow_clinic.critic.agent import AICriticAgent, check_model_api_key

runner = CliRunner()
HAS_NEXTFLOW = importlib.util.find_spec("groovy_parser") is not None


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from a string."""
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


def test_examine_help_shows_enhance_options() -> None:
    """Verify examine help text lists new enhance options."""
    result = runner.invoke(app, ["examine", "--help"])
    assert result.exit_code == 0
    clean_output = strip_ansi(result.output)
    assert "--enhance" in clean_output
    assert "--model" in clean_output
    assert "--api-key" in clean_output


@pytest.mark.skipif(not HAS_NEXTFLOW, reason="Nextflow support not installed")
def test_examine_without_enhance_no_remediation(tmp_path: Path) -> None:
    """Verify examine runs rules without adding any remediation fields by default."""
    diag_file = tmp_path / "diagnosis_test.json"
    result = runner.invoke(
        app, ["examine", "tests/fixtures/poor_practices.nf", "-o", str(diag_file)]
    )
    assert result.exit_code != 0  # poor_practices has errors

    # Load file and assert no remediation is populated
    data = json.loads(diag_file.read_text(encoding="utf-8"))
    assert "findings" in data
    assert len(data["findings"]) > 0
    for finding in data["findings"]:
        assert finding.get("remediation") is None


@pytest.mark.skipif(not HAS_NEXTFLOW, reason="Nextflow support not installed")
@patch("workflow_clinic.cli.load_dotenv")
@patch("workflow_clinic.critic.agent.litellm.completion")
def test_examine_with_enhance_offline_fallback(
    mock_completion: MagicMock,
    mock_load_dotenv: MagicMock,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify examine --enhance falls back to local knowledge store if no API keys are present."""
    # Ensure no API keys are present in env
    for key in [
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MISTRAL_API_KEY",
        "COHERE_API_KEY",
        "GROQ_API_KEY",
        "CLINIC_MODEL",
    ]:
        monkeypatch.delenv(key, raising=False)

    diag_file = tmp_path / "diagnosis_test.json"
    result = runner.invoke(
        app,
        [
            "examine",
            "tests/fixtures/poor_practices.nf",
            "-o",
            str(diag_file),
            "--enhance",
        ],
    )
    assert result.exit_code != 0

    # Ensure LiteLLM was NOT called
    mock_completion.assert_not_called()

    # Verify fallback summary line in terminal output
    assert "Offline remediation guidance added" in result.output
    assert "Knowledge Store fallback" in result.output

    # Verify JSON has remediation populated from TOML
    data = json.loads(diag_file.read_text(encoding="utf-8"))
    assert len(data["findings"]) > 0
    for finding in data["findings"]:
        # advisory W002 INFO findings might not have remediations, but W001 has one
        if finding["rule_id"] == "W001":
            assert finding["remediation"] is not None
            assert "Remediation guidance" in finding["remediation"]["summary"]


@pytest.mark.skipif(not HAS_NEXTFLOW, reason="Nextflow support not installed")
@patch("workflow_clinic.critic.agent.litellm.completion")
def test_examine_with_enhance_mocked_llm(
    mock_completion: MagicMock, tmp_path: Path
) -> None:
    """Verify examine --enhance calls LiteLLM completion and parses the returned JSON."""
    # Mock LLM choice response
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='```json\n{"summary": "Mock LLM Summary", "explanation": "Mock LLM Explanation", "code_example": "Mock Code"}\n```'
            )
        )
    ]
    mock_completion.return_value = mock_response

    diag_file = tmp_path / "diagnosis_test.json"
    result = runner.invoke(
        app,
        [
            "examine",
            "tests/fixtures/poor_practices.nf",
            "-o",
            str(diag_file),
            "--enhance",
            "--api-key",
            "sk-test-key-12345",
            "--model",
            "gpt-4o",
        ],
    )
    assert result.exit_code != 0

    # Verify litellm.completion was called
    mock_completion.assert_called()

    # Verify CLI terminal summary shows AI mode
    assert "AI remediation guidance added" in result.output
    assert "model: gpt-4o" in result.output

    # Verify JSON has LLM-enhanced remediation
    data = json.loads(diag_file.read_text(encoding="utf-8"))
    w001_findings = [f for f in data["findings"] if f["rule_id"] == "W001"]
    assert len(w001_findings) > 0
    assert w001_findings[0]["remediation"]["summary"] == "Mock LLM Summary"
    assert w001_findings[0]["remediation"]["explanation"] == "Mock LLM Explanation"
    assert w001_findings[0]["remediation"]["code_example"] == "Mock Code"


@pytest.mark.skipif(not HAS_NEXTFLOW, reason="Nextflow support not installed")
@patch("workflow_clinic.critic.agent.litellm.completion")
def test_examine_with_enhance_partial_failure(
    mock_completion: MagicMock, tmp_path: Path
) -> None:
    """Verify partial LLM failures fall back gracefully per-finding."""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='```json\n{"summary": "Mock LLM Summary", "explanation": "Mock LLM Explanation", "code_example": "Mock Code"}\n```'
            )
        )
    ]

    mock_completion.side_effect = [
        mock_response,
        RuntimeError("API error on this finding"),
    ] + [mock_response] * 20

    diag_file = tmp_path / "diagnosis_test.json"
    result = runner.invoke(
        app,
        [
            "examine",
            "tests/fixtures/poor_practices.nf",
            "-o",
            str(diag_file),
            "--enhance",
            "--api-key",
            "sk-key",
        ],
    )
    assert result.exit_code != 0

    # Ensure JSON has some findings with LLM values and the failed one with TOML fallback
    data = json.loads(diag_file.read_text(encoding="utf-8"))
    assert len(data["findings"]) > 0

    succeeded_count = sum(
        1
        for f in data["findings"]
        if f.get("remediation") and f["remediation"]["summary"] == "Mock LLM Summary"
    )
    assert succeeded_count > 0

    fallback_count = sum(
        1
        for f in data["findings"]
        if f.get("remediation") and f["remediation"]["summary"] != "Mock LLM Summary"
    )
    assert fallback_count > 0


@pytest.mark.skipif(not HAS_NEXTFLOW, reason="Nextflow support not installed")
@patch.object(AICriticAgent, "enhance_report")
def test_examine_with_enhance_total_failure_falls_back_gracefully(
    mock_enhance_report: MagicMock, tmp_path: Path
) -> None:
    """Verify total critic failure falls back gracefully to unenhanced report."""
    mock_enhance_report.side_effect = RuntimeError("Service Unavailable")

    diag_file = tmp_path / "diagnosis_test.json"
    result = runner.invoke(
        app,
        [
            "examine",
            "tests/fixtures/poor_practices.nf",
            "-o",
            str(diag_file),
            "--enhance",
            "--api-key",
            "sk-key",
        ],
    )
    assert (
        result.exit_code != 0
    )  # poor_practices.nf has ERROR severity findings → exit 1
    assert "AI Critic enhancement failed: Service Unavailable" in result.output

    # Verify JSON was still generated and contains findings without remediations
    data = json.loads(diag_file.read_text(encoding="utf-8"))
    assert len(data["findings"]) > 0
    for finding in data["findings"]:
        assert finding.get("remediation") is None


@pytest.mark.skipif(not HAS_NEXTFLOW, reason="Nextflow support not installed")
@patch("workflow_clinic.cli.logger.info")
@patch("workflow_clinic.critic.agent.litellm.completion")
def test_examine_with_enhance_api_key_not_logged(
    mock_completion: MagicMock, mock_logger_info: MagicMock, tmp_path: Path
) -> None:
    """Verify raw API key value never appears in log output."""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='```json\n{"summary": "Mock LLM Summary", "explanation": "Mock LLM Explanation", "code_example": "Mock Code"}\n```'
            )
        )
    ]
    mock_completion.return_value = mock_response

    diag_file = tmp_path / "diagnosis_test.json"

    runner.invoke(
        app,
        [
            "-v",
            "examine",
            "tests/fixtures/poor_practices.nf",
            "-o",
            str(diag_file),
            "--enhance",
            "--api-key",
            "sk-supersecret-key-12345",
        ],
    )

    assert mock_logger_info.called
    all_log_args = [
        str(arg) for call in mock_logger_info.call_args_list for arg in call[0]
    ]
    assert not any("sk-supersecret-key-12345" in msg for msg in all_log_args)
    assert any("[MASKED]" in msg for msg in all_log_args)


def test_check_model_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify check_model_api_key strictly validates provider-specific keys."""
    # Clear all keys first
    for k in [
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MISTRAL_API_KEY",
        "COHERE_API_KEY",
        "GROQ_API_KEY",
    ]:
        monkeypatch.delenv(k, raising=False)

    # Explicit key always passes
    assert check_model_api_key("anthropic/claude-3", explicit_key="sk-test") is True

    # With only OPENAI_API_KEY set
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    assert check_model_api_key("gpt-4o") is True
    assert check_model_api_key("openai/gpt-4o") is True
    assert check_model_api_key("anthropic/claude-3") is False
    assert check_model_api_key("gemini/gemini-2.5-flash") is False

    # With ANTHROPIC_API_KEY set
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    assert check_model_api_key("anthropic/claude-3") is True
    assert check_model_api_key("claude-3-5-sonnet") is True


@pytest.mark.skipif(not HAS_NEXTFLOW, reason="Nextflow support not installed")
@patch("workflow_clinic.critic.agent.litellm.completion")
def test_examine_with_mismatched_api_key_falls_back(
    mock_completion: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that setting an unmatching key (e.g. OPENAI for anthropic) triggers fallback notice."""
    for key in [
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY",
        "MISTRAL_API_KEY",
        "COHERE_API_KEY",
        "GROQ_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-key")

    diag_file = tmp_path / "diagnosis_test.json"
    result = runner.invoke(
        app,
        [
            "examine",
            "tests/fixtures/poor_practices.nf",
            "-o",
            str(diag_file),
            "--enhance",
            "--model",
            "anthropic/claude-3",
        ],
    )
    assert result.exit_code != 0
    mock_completion.assert_not_called()
    assert "No LLM API key found for model 'anthropic/claude-3'" in result.output
    assert "Knowledge Store fallback" in result.output


@pytest.mark.parametrize(
    ("env_var", "expected_model"),
    [
        ("GEMINI_API_KEY", "gemini/gemini-2.5-flash"),
        ("OPENAI_API_KEY", "gpt-4o-mini"),
        ("ANTHROPIC_API_KEY", "claude-3-5-sonnet-20240620"),
        ("MISTRAL_API_KEY", "mistral/mistral-large-latest"),
        ("GROQ_API_KEY", "groq/llama-3.1-8b-instant"),
        ("COHERE_API_KEY", "cohere/command-r"),
    ],
)
def test_resolve_model_auto_detects_from_env(
    env_var: str, expected_model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify model auto-selection picks correct model for each provider key."""
    for key, _ in PROVIDER_MODEL_MAP:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(env_var, "fake-key")
    assert _resolve_model(None, None) == expected_model


def test_resolve_model_cli_flag_takes_priority_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify --model flag overrides env var and auto-detection."""
    for key, _ in PROVIDER_MODEL_MAP:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setenv("CLINIC_MODEL", "gpt-3.5-turbo")
    assert _resolve_model("gpt-4o", None) == "gpt-4o"


def test_resolve_model_clinic_env_var_takes_priority_over_auto_detect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify CLINIC_MODEL env var overrides auto-detection."""
    for key, _ in PROVIDER_MODEL_MAP:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setenv("CLINIC_MODEL", "gpt-3.5-turbo")
    assert _resolve_model(None, None) == "gpt-3.5-turbo"


@patch("workflow_clinic.cli.logger.warning")
def test_resolve_model_raw_api_key_without_model_uses_default(
    mock_logger_warning: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify raw --api-key without --model logs warning and uses default."""
    for key, _ in PROVIDER_MODEL_MAP:
        monkeypatch.delenv(key, raising=False)
    assert _resolve_model(None, "raw-key") == "gemini/gemini-2.5-flash"
    mock_logger_warning.assert_called_once()
