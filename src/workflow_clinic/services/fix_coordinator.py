"""Coordinator service for Workflow Doctor automated remediation pipeline."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from workflow_clinic.critic import AICriticAgent
from workflow_clinic.critic.agent import check_model_api_key
from workflow_clinic.doctor.runner import DoctorRunner
from workflow_clinic.models.diagnosis import DiagnosisReport, Finding
from workflow_clinic.parsers import ParserRegistry
from workflow_clinic.reporting.issue_generator import _RULE_CATEGORIES
from workflow_clinic.services.coordinator import resolve_model

if TYPE_CHECKING:
    from collections.abc import Callable

    from workflow_clinic.models.fix import AppliedProposal, FixProposal, FixSession

logger = logging.getLogger(__name__)


@dataclass
class FixConfig:
    """Headless configuration for workflow doctor fix orchestration."""

    target: str = "."
    rules: list[str] | None = None
    dry_run: bool = False
    all_findings: bool = False
    enhance: bool = False
    ai_only: bool = False
    model: str | None = None
    api_key: str | None = None
    token: str | None = None
    repo: str | None = None


@dataclass
class FixCallbacks:
    """UI lifecycle event callbacks decoupling doctor execution from terminal rendering."""

    on_load_start: Callable[[Path], None] | None = None
    on_report_loaded: Callable[[DiagnosisReport, int], None] | None = None
    on_ai_audit_start: Callable[[], None] | None = None
    on_ai_findings_discovered: Callable[[int], None] | None = None
    on_proposals_ready: Callable[[list[FixProposal]], None] | None = None
    on_fix_applied: Callable[[AppliedProposal], None] | None = None
    on_warning: Callable[[str], None] | None = None


@dataclass
class FixDependencies:
    """Pluggable dependencies for unit testing and runtime injection."""

    doctor_runner: DoctorRunner | None = None
    runner_factory: Callable[..., DoctorRunner] | None = None
    critic_factory: Callable[..., AICriticAgent] | None = None
    parser_registry: type[ParserRegistry] | None = None
    check_model_api_key_fn: Callable[..., bool] | None = None
    custom_logger: logging.Logger | None = None


@dataclass
class FixResult:
    """Structured result returned by FixCoordinator.execute()."""

    session: FixSession
    report: DiagnosisReport
    root_dir: Path
    diag_path: Path
    dry_run: bool
    actionable_findings: list[Finding]


class FixCoordinator:
    """Headless orchestrator for diagnostic loading, rule filtering, and Doctor cascades."""

    def __init__(
        self,
        config: FixConfig,
        callbacks: FixCallbacks | None = None,
        dependencies: FixDependencies | None = None,
    ) -> None:
        self.config = config
        self.callbacks = callbacks or FixCallbacks()
        self.deps = dependencies or FixDependencies()
        self.logger = self.deps.custom_logger or logger

    def resolve_paths(self) -> tuple[Path, Path, Path | None]:
        """Resolve diagnosis file path, root directory, and optional target file filter.

        Returns:
            Tuple of (diagnosis_json_path, workspace_root_dir, target_file_filter).

        Raises:
            FileNotFoundError: If the resolved diagnosis report does not exist.
        """
        target_path = Path(self.config.target).resolve()

        if target_path.is_file() and target_path.suffix == ".json":
            diag_path = target_path
            root_dir = target_path.parent
            target_file_filter: Path | None = None
        elif target_path.is_file():
            diag_path = target_path.parent / "diagnosis.json"
            root_dir = target_path.parent
            target_file_filter = target_path
        else:
            diag_path = target_path / "diagnosis.json"
            root_dir = target_path
            target_file_filter = None

        if not diag_path.exists():
            msg = f"Could not find '{diag_path.name}' at '{diag_path.parent}'."
            raise FileNotFoundError(msg)

        return diag_path, root_dir, target_file_filter

    def load_diagnosis(self, diag_path: Path | None = None) -> DiagnosisReport:
        """Load and validate the diagnosis JSON report into a DiagnosisReport model."""
        path = diag_path or self.resolve_paths()[0]
        if self.callbacks.on_load_start:
            self.callbacks.on_load_start(path)

        data = json.loads(path.read_text(encoding="utf-8"))
        report = DiagnosisReport.model_validate(data)

        if self.callbacks.on_report_loaded:
            self.callbacks.on_report_loaded(report, len(report.findings))

        return report

    def get_actionable_findings(
        self,
        report: DiagnosisReport,
        root_dir: Path,
        target_file_filter: Path | None = None,
    ) -> list[Finding]:
        """Filter and augment report findings strictly in pipeline order.

        Filter Order:
            1. Rule IDs filter (cheapest O(1) set lookup).
            2. Target file filter (if a specific workflow file was targeted).
            3. AI Critic audit (if --enhance or --ai-only enabled).
        """
        findings = list(report.findings)

        # 1. Rule ID Filter (Cheapest)
        if self.config.rules:
            allowed_rules = {r.upper() for r in self.config.rules}
            findings = [f for f in findings if f.rule_id.upper() in allowed_rules]

        # 2. Target File Scope Filter (if targeting single workflow file)
        if target_file_filter is not None:
            target_name = target_file_filter.name
            findings = [
                f
                for f in findings
                if f.file_path
                and (
                    Path(f.file_path).name == target_name
                    or (root_dir / f.file_path).resolve() == target_file_filter
                )
            ]

        # 3. AI Critic Audit Filter (if enhance or ai_only)
        if self.config.enhance or self.config.ai_only:
            findings = self._run_ai_audit(
                findings, report, root_dir, target_file_filter
            )

        return findings

    def _find_workflow_file(
        self,
        report: DiagnosisReport,
        root_dir: Path,
        target_file_filter: Path | None,
    ) -> Path | None:
        """Locate target workflow file for AI Critic parsing."""
        if target_file_filter is not None:
            return target_file_filter

        for f in report.findings:
            if f.file_path:
                candidate = (root_dir / f.file_path).resolve()
                if candidate.is_file():
                    return candidate
        return None

    def _run_ai_audit(
        self,
        current_findings: list[Finding],
        report: DiagnosisReport,
        root_dir: Path,
        target_file_filter: Path | None,
    ) -> list[Finding]:
        """Execute optional AI Critic audit and return augmented findings."""
        model_name = resolve_model(
            self.config.model, self.config.api_key, custom_logger=self.logger
        )

        check_api_key = self.deps.check_model_api_key_fn or check_model_api_key
        if not check_api_key(model_name, self.config.api_key):
            if self.config.ai_only:
                msg = (
                    f"'--ai-only' requires an active LLM API key for '{model_name}'. "
                    "Please set GEMINI_API_KEY, GROQ_API_KEY, OPENAI_API_KEY, or specify model environment variables."
                )
                raise ValueError(msg)
            warn_msg = f"No API key found for '{model_name}' - running offline fixes only (W001-W004)."
            if self.callbacks.on_warning:
                self.callbacks.on_warning(warn_msg)
            else:
                self.logger.warning("%s", warn_msg)
            return current_findings

        workflow_file = self._find_workflow_file(report, root_dir, target_file_filter)
        if not workflow_file or not workflow_file.is_file():
            return current_findings

        if self.callbacks.on_ai_audit_start:
            self.callbacks.on_ai_audit_start()

        try:
            registry = self.deps.parser_registry or ParserRegistry
            parser_name = registry.detect_parser(workflow_file)
            parser = registry.get_parser(parser_name)
            bundle = parser.parse(workflow_file)

            critic_cls = self.deps.critic_factory or AICriticAgent
            critic = critic_cls(model_name=model_name, api_key=self.config.api_key)
            ai_findings = critic.audit_workflow(bundle)

            if ai_findings:
                if self.callbacks.on_ai_findings_discovered:
                    self.callbacks.on_ai_findings_discovered(len(ai_findings))
                current_findings.extend(ai_findings)
        except Exception as e:  # noqa: BLE001
            self.logger.warning(
                "AI Critic audit failed during fix orchestration: %s", e
            )

        return current_findings

    @staticmethod
    def group_findings_by_category(findings: list[Finding]) -> dict[str, list[Finding]]:
        """Group findings by category domain, falling back to _RULE_CATEGORIES."""
        grouped: dict[str, list[Finding]] = {}
        for f in findings:
            cat = f.category or _RULE_CATEGORIES.get(f.rule_id, "general")
            grouped.setdefault(cat, []).append(f)
        return grouped

    def execute(self, selected_findings: list[Finding] | None = None) -> FixResult:
        """Run the Workflow Doctor cascade and return a structured FixResult."""
        diag_path, root_dir, target_file_filter = self.resolve_paths()
        report = self.load_diagnosis(diag_path)

        if selected_findings is None:
            actionable = self.get_actionable_findings(
                report, root_dir, target_file_filter
            )
        else:
            actionable = selected_findings

        if self.deps.runner_factory:
            runner = self.deps.runner_factory()
        else:
            runner = self.deps.doctor_runner or DoctorRunner()
        session = runner.run(
            actionable,
            root_dir=root_dir,
            dry_run=self.config.dry_run,
            ai_only=self.config.ai_only,
            offline_only=(not self.config.enhance and not self.config.ai_only),
        )

        if self.callbacks.on_proposals_ready:
            self.callbacks.on_proposals_ready(session.proposals)

        if self.callbacks.on_fix_applied:
            for applied in session.applied_proposals:
                self.callbacks.on_fix_applied(applied)

        return FixResult(
            session=session,
            report=report,
            root_dir=root_dir,
            diag_path=diag_path,
            dry_run=self.config.dry_run,
            actionable_findings=actionable,
        )
