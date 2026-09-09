"""Coordinator service for workflow examination and diagnosis."""

import json
import logging
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from dotenv import load_dotenv

from workflow_clinic.critic import AICriticAgent
from workflow_clinic.critic.agent import check_model_api_key
from workflow_clinic.models.diagnosis import DiagnosisReport
from workflow_clinic.models.diagnosis import Finding as DiagnosisFinding
from workflow_clinic.models.workflow_bundle import WorkflowBundle
from workflow_clinic.parsers import ParserRegistry
from workflow_clinic.reporting import compute_fingerprint
from workflow_clinic.rules import RuleRunner
from workflow_clinic.utils import clone_remote_repo, is_remote_url

logger = logging.getLogger(__name__)

PROVIDER_MODEL_MAP: list[tuple[str, str]] = [
    ("GEMINI_API_KEY", "gemini/gemini-3.6-flash"),
    ("OPENAI_API_KEY", "gpt-4o-mini"),
    ("ANTHROPIC_API_KEY", "claude-3-5-sonnet-20240620"),
    ("MISTRAL_API_KEY", "mistral/mistral-large-latest"),
    ("GROQ_API_KEY", "groq/llama-3.1-8b-instant"),
    ("COHERE_API_KEY", "cohere/command-r"),
]


def resolve_model(
    explicit_model: str | None,
    api_key: str | None,
    custom_logger: logging.Logger | None = None,
) -> str:
    """Resolve the LiteLLM model using CLI flags, env vars, or auto-detection."""
    log = custom_logger or logger
    if explicit_model:
        return explicit_model
    if clinic_model := os.getenv("CLINIC_MODEL"):
        return clinic_model
    for env_var, model_name in PROVIDER_MODEL_MAP:
        if os.getenv(env_var):
            return model_name
    if api_key:
        log.warning(
            "--api-key provided without --model. Defaulting to gemini/gemini-3.6-flash."
        )
    return "gemini/gemini-3.6-flash"


@dataclass
class ExamineConfig:
    """Configuration settings for workflow examination."""

    target: str = "."
    parser_type: str | None = None
    output: Path = Path("diagnosis.json")
    enhance: bool = False
    model: str | None = None
    api_key: str | None = None


@dataclass
class ExamineCallbacks:
    """Lifecycle event callbacks for user interfaces and logging."""

    on_clone_start: Callable[[str], None] | None = None
    on_scan_start: Callable[[str], None] | None = None
    on_audit_start: Callable[[], None] | None = None
    on_report_saved: Callable[[Path], None] | None = None


@dataclass
class ExamineDependencies:
    """Pluggable dependencies for testing and custom runtime overrides."""

    clone_repo_fn: Callable[[str, Path], Path] | None = None
    detect_parser_fn: Callable[[Path], str] | None = None
    get_parser_fn: Callable[[str], Any] | None = None
    load_dotenv_fn: Callable[..., Any] | None = None
    custom_logger: logging.Logger | None = None


@dataclass
class ExamineResult:
    """Structured result from a workflow examination session."""

    report: DiagnosisReport
    bundle: WorkflowBundle
    scan_path: Path
    parser_name: str
    output_path: Path
    resolved_model: str | None = None
    has_key: bool = False
    enhance_failed: bool = False
    enhance_error: str | None = None
    fallback_count: int = 0


class ExamineCoordinator:
    """Orchestrates scanning, parsing, rule evaluation, and diagnosis generation."""

    def __init__(
        self,
        config: ExamineConfig | None = None,
        *,
        callbacks: ExamineCallbacks | None = None,
        dependencies: ExamineDependencies | None = None,
    ) -> None:
        self.config = config or ExamineConfig()
        self.callbacks = callbacks or ExamineCallbacks()
        self.dependencies = dependencies or ExamineDependencies()

        self.clone_repo_fn = self.dependencies.clone_repo_fn or clone_remote_repo
        self.detect_parser_fn = (
            self.dependencies.detect_parser_fn or ParserRegistry.detect_parser
        )
        self.get_parser_fn = (
            self.dependencies.get_parser_fn or ParserRegistry.get_parser
        )
        self.load_dotenv_fn = self.dependencies.load_dotenv_fn
        self.logger = self.dependencies.custom_logger or logger
        self._temp_dir_obj: tempfile.TemporaryDirectory[str] | None = None

    @property
    def target(self) -> str:
        return self.config.target

    @property
    def parser_type(self) -> str | None:
        return self.config.parser_type

    @property
    def output(self) -> Path:
        return self.config.output

    @property
    def enhance(self) -> bool:
        return self.config.enhance

    @property
    def model(self) -> str | None:
        return self.config.model

    @property
    def api_key(self) -> str | None:
        return self.config.api_key

    @property
    def on_clone_start(self) -> Callable[[str], None] | None:
        return self.callbacks.on_clone_start

    @property
    def on_scan_start(self) -> Callable[[str], None] | None:
        return self.callbacks.on_scan_start

    @property
    def on_audit_start(self) -> Callable[[], None] | None:
        return self.callbacks.on_audit_start

    @property
    def on_report_saved(self) -> Callable[[Path], None] | None:
        return self.callbacks.on_report_saved

    def resolve_scan_path(self) -> Path:
        """Resolve target to local path or shallow-clone remote repository."""
        if is_remote_url(self.target):
            if self.on_clone_start:
                self.on_clone_start(self.target)
            self._temp_dir_obj = tempfile.TemporaryDirectory()
            return self.clone_repo_fn(self.target, Path(self._temp_dir_obj.name))

        scan_path = Path(self.target).resolve()
        if not scan_path.exists():
            msg = f"Path '{self.target}' does not exist."
            raise FileNotFoundError(msg)
        return scan_path

    def detect_and_parse(self, scan_path: Path) -> tuple[str, WorkflowBundle]:
        """Detect compatible workflow parser and parse workflow into WorkflowBundle."""
        parser_name = self.parser_type or self.detect_parser_fn(scan_path)
        self.logger.info("Detected parser: %s", parser_name)

        if self.on_scan_start:
            self.on_scan_start(scan_path.name)

        parser = self.get_parser_fn(parser_name)
        bundle = parser.parse(scan_path)
        self.logger.info(
            "Parsed workflow '%s' with %d task(s)",
            bundle.metadata.name,
            len(bundle.tasks),
        )
        return parser_name, bundle

    def run_rules_and_audit(
        self,
        bundle: WorkflowBundle,
        resolved_model: str | None,
        *,
        has_key: bool,
    ) -> list[Any]:
        """Execute static rules and optional AI Critic audit on workflow bundle."""
        runner = RuleRunner()
        raw_findings: list[Any] = list(runner.run(bundle))

        if self.enhance and has_key:
            if self.on_audit_start:
                self.on_audit_start()
            agent = AICriticAgent(
                model_name=resolved_model or "gemini/gemini-3.6-flash",
                api_key=self.api_key,
            )
            audit_findings = agent.audit_workflow(
                bundle,
                static_findings=list(raw_findings),  # type: ignore[arg-type]
            )
            raw_findings.extend(audit_findings)

        return raw_findings

    def build_report(
        self,
        bundle: WorkflowBundle,
        raw_findings: list[Any],
    ) -> tuple[list[DiagnosisFinding], DiagnosisReport]:
        """Wrap raw rule findings with SHA-256 fingerprints into DiagnosisReport."""
        findings: list[DiagnosisFinding] = []
        for f in raw_findings:
            file_p = getattr(f, "file_path", None) or self.target
            task_id_val = getattr(f, "task_id", None) or None
            fp = compute_fingerprint(
                file_path=file_p,
                rule_id=f.rule_id,
                task_id=task_id_val,
                target_token=f.message,
            )
            f_dict = f.model_dump()
            f_dict["file_path"] = f.file_path or self.target
            f_dict["fingerprint"] = fp.model_dump()
            f_dict["id"] = fp.hash
            findings.append(DiagnosisFinding.model_validate(f_dict))

        report = DiagnosisReport(
            workflow_name=bundle.metadata.name,
            tasks_count=len(bundle.tasks),
            findings_count=len(findings),
            findings=findings,
        )
        return findings, report

    def enhance_report(
        self,
        report: DiagnosisReport,
        resolved_model: str | None,
    ) -> tuple[DiagnosisReport, int, bool, str | None]:
        """Enhance diagnosis report with AI Critic remediation recommendations."""
        masked_key = "[MASKED]" if self.api_key else "None"
        self.logger.info(
            "Enhancing report with AI Critic using model %s and API key %s",
            resolved_model,
            masked_key,
        )
        try:
            critic_agent = AICriticAgent(
                model_name=resolved_model or "gemini/gemini-3.6-flash",
                api_key=self.api_key,
            )
            result = critic_agent.enhance_report(report)
        except Exception as e:
            self.logger.exception("AI Critic enhancement failed")
            return report, 0, True, str(e)
        else:
            return result.report, result.fallback_count, False, None

    def export_report(self, report: DiagnosisReport) -> None:
        """Write diagnosis report JSON to the designated output path."""
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )
        if self.on_report_saved:
            self.on_report_saved(self.output)

    def run(self) -> ExamineResult:
        """Execute end-to-end examination pipeline and return ExamineResult."""
        scan_path = self.resolve_scan_path()
        parser_name, bundle = self.detect_and_parse(scan_path)

        resolved_model: str | None = None
        has_key = False
        enhance_failed = False
        enhance_error: str | None = None
        fallback_count = 0

        if self.enhance:
            load_fn = self.load_dotenv_fn or load_dotenv
            load_fn(override=False)
            resolved_model = resolve_model(
                self.model, self.api_key, custom_logger=self.logger
            )
            has_key = check_model_api_key(resolved_model, self.api_key)

        raw_findings = self.run_rules_and_audit(bundle, resolved_model, has_key=has_key)
        _, report = self.build_report(bundle, raw_findings)

        if self.enhance:
            report, fallback_count, enhance_failed, enhance_error = self.enhance_report(
                report, resolved_model
            )

        self.export_report(report)

        return ExamineResult(
            report=report,
            bundle=bundle,
            scan_path=scan_path,
            parser_name=parser_name,
            output_path=self.output,
            resolved_model=resolved_model,
            has_key=has_key,
            enhance_failed=enhance_failed,
            enhance_error=enhance_error,
            fallback_count=fallback_count,
        )

    def cleanup(self) -> None:
        """Clean up temporary directory resources if created."""
        if self._temp_dir_obj is not None:
            self._temp_dir_obj.cleanup()
            self._temp_dir_obj = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.cleanup()
