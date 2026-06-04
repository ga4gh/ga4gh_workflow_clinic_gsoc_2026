"""
Smoke tests — verifies the package installs and imports correctly.
These run on every PR via GitHub Actions CI.
"""

import workflow_clinic


def test_package_importable():
    """CI gate: package must import without errors."""
    assert workflow_clinic is not None


def test_version_string():
    """CI gate: version string must be present and non-empty."""
    assert hasattr(workflow_clinic, "__version__")
    assert isinstance(workflow_clinic.__version__, str)
    assert len(workflow_clinic.__version__) > 0
