"""Fingerprint calculation utilities for diagnostic finding deduplication."""

import hashlib

from workflow_clinic.models.diagnosis import Fingerprint


def compute_fingerprint(
    file_path: str,
    line_number: int | None,
    rule_id: str,
) -> Fingerprint:
    """Compute a deterministic SHA-256 fingerprint hash for a finding.

    Args:
        file_path: Path to target workflow file
        line_number: Line number where issue was detected (or None)
        rule_id: Rule identifier (e.g. W001, W002)

    Returns:
        Fingerprint instance containing the calculated SHA-256 hash.
    """
    line_str = str(line_number) if line_number is not None else "0"
    raw_payload = f"{file_path}:{line_str}:{rule_id}"
    digest = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
    return Fingerprint(hash=digest)
