"""Hardcoded credentials detection rule.

Flags likely hardcoded secrets (API keys, tokens, passwords) in workflow
script blocks, which risk leaking credentials if pushed to a public repository.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workflow_clinic.models import WorkflowBundle

from workflow_clinic.rules.base import BaseRule, Finding, Severity

# Layer 1: known vendor formats. Near-zero false positive by construction.
_VENDOR_PATTERNS: dict[str, re.Pattern[str]] = {
    "AWS Access Key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "AWS Secret Access Key": re.compile(
        r"(?i)aws_secret_access_key\s*=\s*['\"]([A-Za-z0-9+/]{40})['\"]"
    ),
    "GitHub PAT": re.compile(r"gh[pous]_[A-Za-z0-9]{36}"),
    "GitHub Fine-Grained PAT": re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),
    "Slack Token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "Stripe Key": re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{24,}"),
    "Google API Key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
}

# Layer 2: generic assignment to a suspicious key name.
_GENERIC_ASSIGNMENT = re.compile(
    r"(?i)(api_key|password|secret|token|auth_token)\s*=\s*['\"]([^'\"]+)['\"]"
)

_MIN_GENERIC_LENGTH = 12
_ENTROPY_THRESHOLD = 3.5  # First guess, validated empirically against fixtures


def _shannon_entropy(value: str) -> float:
    """Return the Shannon entropy of a string, in bits per character."""
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum(
        (count / length) * math.log2(count / length) for count in counts.values()
    )


class HardcodedCredentialsRule(BaseRule):
    """Flags likely hardcoded secrets in script blocks (W004).

    Two-layer detection:
    1. Known vendor formats (AWS, GitHub, Slack, Stripe, Google) —
       matched directly by their distinctive structure.
    2. Generic key=value assignments to suspicious names, filtered by
       a minimum length and a Shannon entropy check, to distinguish
       real random secrets from low-entropy placeholder text.
    """

    id = "W004"
    name = "Hardcoded Credentials"
    description = (
        "Checks for likely hardcoded API keys, tokens, or passwords "
        "in script blocks, which risk leaking secrets if pushed to a "
        "public repository."
    )

    def check(self, bundle: WorkflowBundle) -> list[Finding]:
        """Scan every task's command string for likely hardcoded secrets."""
        findings: list[Finding] = []

        for task in bundle.tasks:
            script = task.command
            if not script:
                continue

            findings.extend(self._check_vendor_patterns(task, script))
            findings.extend(self._check_generic_assignments(task, script))

        return findings

    def _check_vendor_patterns(self, task, script: str) -> list[Finding]:
        findings: list[Finding] = []
        for vendor_name, pattern in _VENDOR_PATTERNS.items():
            for match in pattern.finditer(script):
                matched_val = (
                    match.group(1) if match.lastindex is not None else match.group(0)
                )
                findings.append(
                    Finding(
                        rule_id=self.id,
                        severity=Severity.ERROR,
                        message=(
                            f"Process '{task.name}' appears to contain a "
                            f"hardcoded {vendor_name}: '{matched_val[:8]}...'. "
                            f"Remove this and use a secrets manager or "
                            f"environment variable instead."
                        ),
                        task_id=task.id,
                        process_name=task.name,
                        file_path=task.file_path,
                        line_number=task.line_number,
                    )
                )
        return findings

    def _check_generic_assignments(self, task, script: str) -> list[Finding]:
        findings: list[Finding] = []
        for match in _GENERIC_ASSIGNMENT.finditer(script):
            key_name, value = match.group(1), match.group(2)

            if len(value) < _MIN_GENERIC_LENGTH:
                continue

            if any(p.search(value) for p in _VENDOR_PATTERNS.values()):
                continue  # already reported at higher confidence by vendor layer

            entropy = _shannon_entropy(value)
            if entropy < _ENTROPY_THRESHOLD:
                continue  # looks like a placeholder, not a real secret

            findings.append(
                Finding(
                    rule_id=self.id,
                    severity=Severity.WARNING,
                    message=(
                        f"Process '{task.name}' assigns a high-entropy "
                        f"value to '{key_name}', which may be a hardcoded "
                        f"secret. Remove this and use a secrets manager "
                        f"or environment variable instead."
                    ),
                    task_id=task.id,
                    process_name=task.name,
                    file_path=task.file_path,
                    line_number=task.line_number,
                )
            )
        return findings
