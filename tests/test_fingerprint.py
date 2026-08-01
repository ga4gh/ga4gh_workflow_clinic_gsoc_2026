"""Unit tests for structural SHA-256 fingerprint generation utilities."""

from workflow_clinic.models.diagnosis import Fingerprint
from workflow_clinic.reporting.fingerprint import compute_fingerprint, normalize_token


def test_normalize_token() -> None:
    """Verify normalize_token collapses whitespace runs and handles None."""
    assert normalize_token(None) == ""
    assert (
        normalize_token("  container   'fastqc:v1.0'  \n ") == "container 'fastqc:v1.0'"
    )


def test_compute_fingerprint_returns_valid_instance() -> None:
    """Verify compute_fingerprint returns Fingerprint instance with SHA-256 string."""
    fp = compute_fingerprint(
        file_path="modules/fastqc.nf",
        rule_id="W001",
        task_id="FASTQC",
        target_token="container 'biocontainers/fastqc:v0.11.9'",
    )
    assert isinstance(fp, Fingerprint)
    assert isinstance(fp.hash, str)
    assert len(fp.hash) == 64  # Hex length of SHA-256 digest


def test_structural_fingerprint_line_shift_and_whitespace_immunity() -> None:
    """Verify structural fingerprinting is 100% immune to whitespace formatting changes."""
    # Finding target with standard spacing
    fp1 = compute_fingerprint(
        file_path="modules/fastqc.nf",
        rule_id="W001",
        task_id="FASTQC",
        target_token="container  'fastqc:v1.0'",
    )
    # Same finding target with extra spaces and newlines
    fp2 = compute_fingerprint(
        file_path="modules/fastqc.nf",
        rule_id="W001",
        task_id="FASTQC",
        target_token="container \n  'fastqc:v1.0'",
    )
    assert fp1.hash == fp2.hash


def test_fingerprint_sensitivity_to_structural_changes() -> None:
    """Verify modifying task ID, rule ID, or target snippet alters the output hash."""
    base_fp = compute_fingerprint("main.nf", "W001", "FASTQC", "target_a")

    different_task = compute_fingerprint("main.nf", "W001", "ALIGN", "target_a")
    different_rule = compute_fingerprint("main.nf", "W002", "FASTQC", "target_a")
    different_target = compute_fingerprint("main.nf", "W001", "FASTQC", "target_b")

    assert base_fp.hash != different_task.hash
    assert base_fp.hash != different_rule.hash
    assert base_fp.hash != different_target.hash


def test_fingerprint_unnamed_task_fallback() -> None:
    """Verify None task_id falls back to 'global' deterministically without crashing."""
    fp_none = compute_fingerprint("main.nf", "W003", None, "target")
    fp_global = compute_fingerprint("main.nf", "W003", "global", "target")

    assert isinstance(fp_none.hash, str)
    assert len(fp_none.hash) == 64
    assert fp_none.hash == fp_global.hash
