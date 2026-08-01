"""Fingerprint calculation utilities for diagnostic finding deduplication."""

import hashlib

from workflow_clinic.models.diagnosis import Fingerprint


def normalize_token(token: str | None) -> str:
    """Normalize code snippet or token by collapsing all whitespace runs."""
    if not token:
        return ""
    return " ".join(token.split())


def compute_fingerprint(
    file_path: str,
    rule_id: str,
    task_id: str | None = None,
    target_token: str | None = None,
) -> Fingerprint:
    """Compute a stable, structure-based fingerprint for a diagnostic finding.

    Anchored to AST task node and normalized target snippet, not line numbers.
    Survives line shifts, comment additions, and whitespace reformatting.

    Args:
        file_path: Relative or absolute file path
        rule_id: Rule identifier (e.g. W001, W002)
        task_id: Name/ID of the AST task node (or None for global scope)
        target_token: Optional target token or code snippet to disambiguate

    Returns:
        Fingerprint instance containing deterministic 64-character SHA-256 hash.
    """
    clean_task = task_id.strip() if task_id else "global"
    normalized_target = normalize_token(target_token)
    raw_payload = f"{file_path}::{clean_task}::{rule_id}::{normalized_target}"
    digest = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
    return Fingerprint(hash=digest)
