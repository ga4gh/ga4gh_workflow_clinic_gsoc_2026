"""
Workflow Clinic: AI-Powered Cloudification of Bioinformatics Workflows.
GA4GH GSoC 2026 — Revaa Rathore
"""

import importlib.metadata

try:
    __version__ = importlib.metadata.version("workflow-clinic")
except importlib.metadata.PackageNotFoundError:
    # Fallback when the package is not installed (e.g. local dev/testing)
    __version__ = "0.0.1"
