"""Workflow Clinic: AI-Powered Cloudification of Bioinformatics Workflows.

This package provides tools for parsing, modeling, and validating scientific
workflows (such as Nextflow and Snakemake) to ease cloud deployment and
integration.
"""

import importlib.metadata

try:
    __version__ = importlib.metadata.version("workflow-clinic")
except importlib.metadata.PackageNotFoundError:
    # Fallback when the package is not installed (e.g. local dev/testing)
    __version__ = "0.0.1"
