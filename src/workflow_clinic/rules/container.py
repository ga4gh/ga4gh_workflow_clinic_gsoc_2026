"""Container pinning validation rule.

Checks that every task in a WorkflowBundle specifies a container image
with a pinned tag or digest. Unpinned containers (missing, tagless,
or using ``:latest``) are flagged as portability risks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workflow_clinic.models import WorkflowBundle
from workflow_clinic.rules.base import BaseRule, Finding, Severity


class PinnedContainerRule(BaseRule):
    """Flag tasks with missing or unpinned container images (W001).

    A container is considered **pinned** if it includes a tag separator
    (``:``) followed by a tag that is not ``latest``, or uses a digest
    reference (``@sha256:``).
    """

    id = "W001"
    name = "Pinned Container"
    description = (
        "Checks that all tasks specify a container with a pinned tag or digest."
    )

    def check(self, bundle: WorkflowBundle) -> list[Finding]:
        """Validate container declarations across all tasks."""
        findings: list[Finding] = []

        for task in bundle.tasks:
            container = task.resources.container

            if not container:
                findings.append(
                    Finding(
                        rule_id=self.id,
                        message=f"Process '{task.name}' has no container defined.",
                        severity=Severity.ERROR,
                        task_id=task.id,
                        location=task.name,
                    )
                )
                continue

            if self._is_unpinned(container):
                findings.append(
                    Finding(
                        rule_id=self.id,
                        message=(
                            f"Process '{task.name}' uses an unpinned container "
                            f"image: '{container}'. Pin to a specific tag or "
                            f"digest for reproducibility."
                        ),
                        severity=Severity.WARNING,
                        task_id=task.id,
                        location=task.name,
                    )
                )

        return findings

    @staticmethod
    def _is_unpinned(container: str) -> bool:
        """Return True if the container reference is not pinned.

        A container is unpinned if:
        - It has no tag separator (``:``) at all.
        - Its tag is exactly ``latest``.
        - It has no digest (``@sha256:``).

        Port-number colons (e.g. ``registry:5000/image``) are handled
        by checking the portion after the last ``/``.
        """
        # Digest is always pinned
        if "@sha256:" in container:
            return False

        # Isolate the image portion after the last slash
        image_part = container.rsplit("/", maxsplit=1)[-1]

        if ":" not in image_part:
            # No tag at all → unpinned
            return True

        tag = image_part.rsplit(":", maxsplit=1)[-1]
        return tag == "latest"
