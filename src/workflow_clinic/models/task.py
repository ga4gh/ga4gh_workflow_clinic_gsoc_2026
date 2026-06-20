"""Workflow task and resource representation.

This module defines the Task and TaskResources models representing execution
steps within a workflow and their corresponding hardware and software needs.
"""

import re

from pydantic import BaseModel, Field, field_validator

# Matches numbers (int or float) optionally followed by separator (spaces or single dot)
# and a case-insensitive memory unit (B, KB, MB, GB, TB, PB, KiB, MiB, GiB, TiB, K, M, G, T)
MEMORY_PATTERN = re.compile(
    r"^\d+(?:\.\d+)?\s*\.?(?:[kKmMgGtTpP][iI]?[bB]|[bB]|[kKmMgGtT])?$"
)


class TaskResources(BaseModel):
    """Resource constraints for task execution.

    Attributes:
        cpus: Number of CPU cores requested/allocated, or None if unspecified.
        memory: Memory constraint string (e.g., "8 GB"), or None if unspecified.
        container: Name or URI of the container image used for execution, or None.
    """

    cpus: int | None = Field(default=None, gt=0)
    memory: str | None = None
    container: str | None = None

    @field_validator("memory")
    @classmethod
    def validate_memory(cls, v: str | None) -> str | None:
        """Validate memory string format."""
        if v is None:
            return v
        stripped = v.strip()
        if not MEMORY_PATTERN.match(stripped):
            msg = (
                f"invalid memory format: '{v}'. Must be a number optionally "
                f"followed by unit (e.g., '8 GB', '512MB', '4.GiB', '8192')"
            )
            raise ValueError(msg)
        return stripped


class Task(BaseModel):
    """A single unit of execution (e.g., Nextflow process, Snakemake rule).

    Attributes:
        id: Unique identifier for the task within the workflow.
        name: Human-readable name of the task.
        command: Command-line string or script to run, or None if unspecified.
        resources: System resources constrained or allocated for this task.
        inputs: List of input file paths or datasets.
        outputs: List of output file paths or datasets.
    """

    id: str
    name: str
    command: str | None = None
    resources: TaskResources = Field(default_factory=TaskResources)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        """Ensure task id is not empty or containing only whitespace."""
        if not v.strip():
            msg = "id must not be empty or contain only whitespace"
            raise ValueError(msg)
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Ensure task name is not empty or containing only whitespace."""
        if not v.strip():
            msg = "name must not be empty or contain only whitespace"
            raise ValueError(msg)
        return v
