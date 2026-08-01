"""Unit tests for SHA-256 fingerprint generation utilities."""

from workflow_clinic.models.diagnosis import Fingerprint
from workflow_clinic.reporting.fingerprint import compute_fingerprint


def test_compute_fingerprint_returns_valid_instance() -> None:
    """Verify compute_fingerprint returns Fingerprint instance with SHA-256 string."""
    fp = compute_fingerprint(
        file_path="modules/fastqc.nf",
        line_number=14,
        rule_id="W001",
    )
    assert isinstance(fp, Fingerprint)
    assert isinstance(fp.hash, str)
    assert len(fp.hash) == 64  # Hex length of SHA-256 digest


def test_fingerprint_reproducibility() -> None:
    """Verify compute_fingerprint produces identical hash for identical inputs."""
    fp1 = compute_fingerprint(
        file_path="processes/align.nf",
        line_number=20,
        rule_id="W002",
    )
    fp2 = compute_fingerprint(
        file_path="processes/align.nf",
        line_number=20,
        rule_id="W002",
    )
    assert fp1.hash == fp2.hash


def test_fingerprint_sensitivity_to_changes() -> None:
    """Verify modifying file path, line number, or rule ID alters the output hash."""
    base_fp = compute_fingerprint("main.nf", 10, "W001")

    different_path = compute_fingerprint("sub.nf", 10, "W001")
    different_line = compute_fingerprint("main.nf", 11, "W001")
    different_rule = compute_fingerprint("main.nf", 10, "W002")

    assert base_fp.hash != different_path.hash
    assert base_fp.hash != different_line.hash
    assert base_fp.hash != different_rule.hash


def test_fingerprint_none_line_number_handling() -> None:
    """Verify None line number is handled deterministically without crashing."""
    fp_none = compute_fingerprint("main.nf", None, "W003")
    fp_zero = compute_fingerprint("main.nf", 0, "W003")

    assert isinstance(fp_none.hash, str)
    assert len(fp_none.hash) == 64
    assert fp_none.hash == fp_zero.hash
