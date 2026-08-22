"""Hardcoded absolute path detection rule.

Flags absolute filesystem paths found in workflow script blocks. These
paths break portability when a pipeline moves between machines or into
the cloud.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workflow_clinic.models import WorkflowBundle
from workflow_clinic.rules.base import BaseRule, Finding, Severity

# Minimal exclusion list for v1 — sensitivity over specificity.
# Add more here only once real usage shows genuine false-positive noise.
_EXCLUDED_EXACT_PATHS = frozenset(
    {
        "/usr/bin/env",
        "/bin/bash",
        "/bin/sh",
        "/dev/null",
        "/dev/stdout",
        "/dev/stderr",
    }
)

# Delimiters that commonly glue a path to an operator in bash.
# e.g. VAR=/home/user/ref.fa  or  2>/home/user/error.log
_TOKEN_DELIMITERS = re.compile(r"[=>]")


def _is_flaggable_absolute_path(token: str) -> bool:
    """Return True if *token* looks like a hardcoded absolute path worth flagging."""
    if "://" in token or token.startswith(("//", "#")):
        return False  # URLs, comments, etc.
    if token in ("/", "//") or token in _EXCLUDED_EXACT_PATHS:
        return False
    try:
        return Path(token).is_absolute()
    except (ValueError, OSError):
        return False


class HardcodedPathRule(BaseRule):
    """Flags absolute filesystem paths hardcoded in script blocks (W003).

    Detection uses ``pathlib.Path.is_absolute()`` — no custom regex.
    A small exclusion list covers standard shell paths and URLs to
    avoid obvious false positives; the list is intentionally minimal
    following the "sensitivity over specificity" principle for v1.
    """

    id = "W003"
    name = "Hardcoded Path"
    description = (
        "Checks that script blocks do not contain hardcoded absolute "
        "filesystem paths, which break portability across machines."
    )

    def check(self, bundle: WorkflowBundle) -> list[Finding]:
        """Scan every task's command string for absolute paths."""
        findings: list[Finding] = []

        for task in bundle.tasks:
            script = task.command
            if not script:
                continue

            for token in script.split():
                # Split on = and > to catch bash idioms like
                # VAR=/home/user/ref.fa or 2>/home/user/error.log
                candidates = _TOKEN_DELIMITERS.split(token)
                for candidate in candidates:
                    # Strip common surrounding punctuation/quotes from each candidate
                    stripped = candidate.strip("'\"(),;")
                    if not stripped:
                        continue
                    if _is_flaggable_absolute_path(stripped):
                        findings.append(
                            Finding(
                                rule_id=self.id,
                                severity=Severity.WARNING,
                                message=(
                                    f"Process '{task.name}' contains a "
                                    f"hardcoded absolute path: '{stripped}'. "
                                    f"This will break on other machines or "
                                    f"in cloud environments."
                                ),
                                task_id=task.id,
                                process_name=task.name,
                                file_path=task.file_path,
                                line_number=task.line_number,
                            )
                        )

        return findings
