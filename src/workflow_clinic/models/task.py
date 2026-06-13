"""Workflow task and resource representation.

This module defines the Task and TaskResources models representing execution
steps within a workflow and their corresponding hardware and software needs.
"""

from pydantic import BaseModel, Field


class TaskResources(BaseModel):
    """Resource constraints for task execution.

    Attributes:
        cpus: Number of CPU cores requested/allocated, or None if unspecified.
        memory: Memory constraint string (e.g., "8 GB"), or None if unspecified.
        container: Name or URI of the container image used for execution, or None.
    """

    cpus: int | None = None
    memory: str | None = None
    container: str | None = None


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
