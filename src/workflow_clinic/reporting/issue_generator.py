"""Issue generator module for Workflow Clinic diagnostic findings.

Converts diagnostic findings into grouped GitHub issue payloads with embedded
hidden SHA-256 fingerprint comments for issue deduplication.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workflow_clinic.models.diagnosis import DiagnosisReport, Finding

logger = logging.getLogger(__name__)

# Explicit mapping of rule IDs to issue category domains
_RULE_CATEGORIES: dict[str, str] = {
    "W001": "containerization",
    "W002": "resources",
    "W003": "portability",
    "W004": "security",
}

# Regex pattern for matching embedded fingerprint comments in issue markdown
FINGERPRINT_REGEX = re.compile(
    r"<!--\s*workflow-clinic:fingerprint:([a-f0-9]{64})\s*-->", re.IGNORECASE
)
SHA256_HEX_REGEX = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)

# Severity ordering to compute highest severity in grouped issues
_SEVERITY_ORDER: dict[str, int] = {
    "CRITICAL": 4,
    "HIGH": 3,
    "ERROR": 3,
    "MEDIUM": 2,
    "WARNING": 2,
    "LOW": 1,
    "INFO": 1,
}


@dataclass
class GeneratedIssue:
    """Represents a grouped issue payload ready for GitHub or local export."""

    title: str
    category: str
    severity: str
    body: str
    fingerprints: list[str] = field(default_factory=list)


def extract_fingerprints(markdown_text: str) -> set[str]:
    """Extract all SHA-256 fingerprint hashes embedded in markdown text.

    Args:
        markdown_text: Raw markdown content of existing issue(s)

    Returns:
        Set of unique 64-character hex fingerprint strings.
    """
    if not markdown_text:
        return set()
    return {
        match.group(1).lower() for match in FINGERPRINT_REGEX.finditer(markdown_text)
    }


def _get_valid_fingerprint(f: Finding) -> str | None:
    """Extract a valid 64-hex SHA-256 fingerprint from finding.fingerprint.hash or finding.id.

    Logs a warning if candidate is missing or does not match SHA-256 hex format.
    """
    candidate = ""
    if f.fingerprint and f.fingerprint.hash:
        candidate = f.fingerprint.hash.lower().strip()
    elif f.id:
        candidate = f.id.lower().strip()

    if not candidate:
        logger.warning(
            "Finding '%s' (rule %s) lacks a fingerprint hash. Deduplication tracking is disabled for this finding.",
            getattr(f, "id", None) or getattr(f, "title", "unnamed"),
            getattr(f, "rule_id", "unknown"),
        )
        return None

    if not SHA256_HEX_REGEX.match(candidate):
        logger.warning(
            "Finding '%s' (rule %s) contains invalid non-SHA256 fingerprint hash '%s'. Skipping deduplication hash for this finding.",
            getattr(f, "id", None) or getattr(f, "title", "unnamed"),
            getattr(f, "rule_id", "unknown"),
            candidate,
        )
        return None

    return candidate


def _detect_code_language(file_path: str | None, default_lang: str = "groovy") -> str:
    """Infer markdown code block syntax highlighting language from file extension."""
    if not file_path:
        return default_lang

    path_str = file_path.lower()
    if path_str.endswith(".wdl"):
        return "wdl"
    if path_str.endswith((".cwl", ".yaml", ".yml")):
        return "yaml"
    if path_str.endswith((".smk", "snakefile")):
        return "python"
    if path_str.endswith((".nf", ".config", ".groovy")):
        return "groovy"
    return default_lang


def filter_new_findings(
    findings: list[Finding], existing_fingerprints: set[str] | None = None
) -> list[Finding]:
    """Filter findings to retain only those whose fingerprints are untracked.

    Deduplication happens at the individual finding level *before* grouping,
    preventing partial-duplicate category issue corruption.

    Args:
        findings: List of Finding instances
        existing_fingerprints: Set of already published fingerprint hashes

    Returns:
        List of findings that do not exist in existing_fingerprints.
    """
    if not existing_fingerprints:
        return list(findings)

    clean_existing: set[str] = set()
    for fp in existing_fingerprints:
        if not fp:
            continue
        cleaned = fp.lower().strip()
        if SHA256_HEX_REGEX.match(cleaned):
            clean_existing.add(cleaned)
        else:
            logger.warning(
                "Ignoring invalid non-SHA256 existing fingerprint hash: '%s'",
                fp,
            )

    new_findings: list[Finding] = []

    for f in findings:
        fp_hash = _get_valid_fingerprint(f)
        if not fp_hash or fp_hash not in clean_existing:
            new_findings.append(f)

    return new_findings


def _determine_highest_severity(findings: list[Finding]) -> str:
    """Calculate the maximum severity level among a group of findings."""
    max_score = 0
    max_sev = "MEDIUM"

    for f in findings:
        sev_str = str(f.severity).upper()
        score = _SEVERITY_ORDER.get(sev_str, 1)
        if score > max_score:
            max_score = score
            max_sev = sev_str

    return max_sev


def _build_category_issue_body(
    cat_title_str: str,
    cat: str,
    highest_severity: str,
    cat_findings: list[Finding],
    default_lang: str = "groovy",
) -> tuple[str, list[str]]:
    """Build issue Markdown body text and list of embedded fingerprint hashes."""
    fps: list[str] = []
    body_lines: list[str] = [
        f"## ⚠️ Workflow Diagnostic Finding: {cat_title_str}",
        "",
        f"**Category**: `{cat}` | **Severity**: `{highest_severity}`",
        "",
        "The following cloud readiness issues were flagged by `workflow-clinic examine`:",
        "",
    ]

    for idx, f in enumerate(cat_findings, 1):
        fp_hash = _get_valid_fingerprint(f)
        if fp_hash:
            fps.append(fp_hash)

        loc = (
            f.file_path
            if getattr(f, "file_path", None)
            else (getattr(f, "process_name", None) or "global")
        )
        line_str = (
            f" (Line {f.line_number})"
            if getattr(f, "line_number", None) is not None
            else ""
        )

        title_str = (
            f.title
            if getattr(f, "title", None)
            else (getattr(f, "message", None) or f.rule_id)
        )
        details_str = getattr(f, "message", None) or getattr(f, "title", "")

        body_lines.append(f"### {idx}. [{f.rule_id}] {title_str}")
        body_lines.append(f"- **Location**: `{loc}`{line_str}")
        if details_str:
            body_lines.append(f"- **Details**: {details_str}")

        if f.remediation is not None:
            if f.remediation.summary:
                body_lines.append(f"- **Remediation**: {f.remediation.summary}")
            if f.remediation.code_example:
                lang = _detect_code_language(
                    getattr(f, "file_path", None), default_lang=default_lang
                )
                body_lines.append("")
                body_lines.append(f"```{lang}")
                body_lines.append(f.remediation.code_example)
                body_lines.append("```")

        if fp_hash:
            body_lines.append(f"<!-- workflow-clinic:fingerprint:{fp_hash} -->")

        body_lines.append("")

    body_lines.append("---")
    body_lines.append(
        "*Automated finding report generated by [GA4GH Workflow Clinic](https://github.com/ga4gh/ga4gh_workflow_clinic_gsoc_2026).*"
    )
    return "\n".join(body_lines), fps


def group_findings(
    findings: list[Finding], default_lang: str = "groovy"
) -> list[GeneratedIssue]:
    """Group a list of findings by rule category domain into GeneratedIssue objects.

    Args:
        findings: List of diagnostic findings to group
        default_lang: Default code block syntax language if un-inferable

    Returns:
        List of GeneratedIssue objects formatted with GitHub Flavored Markdown.
    """
    if not findings:
        return []

    # Group findings by category
    grouped: dict[str, list[Finding]] = {}
    for f in findings:
        cat = getattr(f, "category", None) or _RULE_CATEGORIES.get(
            f.rule_id, "portability"
        )
        grouped.setdefault(cat, []).append(f)

    issues: list[GeneratedIssue] = []

    for cat, cat_findings in grouped.items():
        highest_severity = _determine_highest_severity(cat_findings)
        cat_title_str = cat.replace("_", " ").replace("-", " ").title()
        count = len(cat_findings)

        title = f"[{highest_severity}] {cat_title_str} Issues ({count} location{'s' if count > 1 else ''})"
        body, fps = _build_category_issue_body(
            cat_title_str,
            cat,
            highest_severity,
            cat_findings,
            default_lang=default_lang,
        )

        issues.append(
            GeneratedIssue(
                title=title,
                category=cat,
                severity=highest_severity,
                body=body,
                fingerprints=fps,
            )
        )

    return issues


def generate_issues(
    report: DiagnosisReport,
    existing_fingerprints: set[str] | None = None,
    default_lang: str = "groovy",
) -> list[GeneratedIssue]:
    """Generate deduplicated issue payloads directly from a DiagnosisReport instance.

    Args:
        report: DiagnosisReport object containing findings
        existing_fingerprints: Optional set of already tracked fingerprint hashes
        default_lang: Default code block syntax language if un-inferable

    Returns:
        List of GeneratedIssue objects ready for issue export.
    """
    new_findings = filter_new_findings(report.findings, existing_fingerprints)
    return group_findings(new_findings, default_lang=default_lang)
