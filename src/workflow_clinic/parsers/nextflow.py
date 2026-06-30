"""Nextflow workflow parser implementation.

This module parses Nextflow files (.nf) and directories containing a main.nf
file, extracting metadata and processes into a standard WorkflowBundle.
"""

import re
from pathlib import Path

from workflow_clinic.exceptions import InvalidWorkflowError, ParserError
from workflow_clinic.models import WorkflowBundle, WorkflowMetadata
from workflow_clinic.models.task import Task, TaskResources
from workflow_clinic.parsers.base import BaseParser

# Regex patterns for parsing Nextflow elements
PROCESS_PATTERN = re.compile(r"process\s+([A-Za-z0-9_]+)\s*\{")
CONTAINER_PATTERN = re.compile(r"container\s*=?\s*['\"]([^'\"]+)['\"]")
CPUS_PATTERN = re.compile(r"cpus\s*=?\s*(\d+)")
MEMORY_PATTERN = re.compile(r"memory\s*=?\s*['\"]([^'\"]+)['\"]")


class NextflowParser(BaseParser):
    """Parser implementation for Nextflow workflows."""

    @classmethod
    def can_parse(cls, path: Path) -> bool:
        """Determine if this parser can handle the given workflow path.

        Args:
            path: Path to a workflow file or directory

        Returns:
            True if this parser can handle the workflow, False otherwise
        """
        if path.is_file():
            return path.suffix == ".nf"
        if path.is_dir():
            return (path / "main.nf").is_file()
        return False

    def _resolve_script_file(self, path: Path, entrypoint: str | None) -> Path:
        """Resolve the script file path from the given directory/file path."""
        if path.is_file():
            return path
        if path.is_dir():
            target_entrypoint = entrypoint or "main.nf"
            script_file = path / target_entrypoint
            if not script_file.is_file():
                msg = f"Entrypoint file not found: {script_file}"
                raise ParserError(msg)
            return script_file
        msg = f"Unsupported path type: {path}"
        raise ParserError(msg)

    def _skip_comment(
        self, content: str, idx: int, length: int, body_chars: list[str]
    ) -> int:
        """Skip a single-line comment, appending characters to body_chars."""
        while idx < length and content[idx] != "\n":
            body_chars.append(content[idx])
            idx += 1
        return idx

    def _skip_quoted_string(
        self, content: str, idx: int, length: int, body_chars: list[str]
    ) -> int:
        """Skip a quoted string, appending characters to body_chars."""
        quote_char = content[idx]
        body_chars.append(quote_char)
        idx += 1
        while idx < length and content[idx] != quote_char:
            body_chars.append(content[idx])
            idx += 1
        if idx < length:
            body_chars.append(content[idx])
            idx += 1
        return idx

    def _extract_process_body(self, content: str, start_idx: int) -> str | None:
        """Extract process body text, skipping braces inside strings and comments."""
        brace_count = 1
        idx = start_idx
        length = len(content)
        body_chars: list[str] = []

        while idx < length:
            char = content[idx]

            # Skip single-line comments
            if char == "/" and idx + 1 < length and content[idx + 1] == "/":
                idx = self._skip_comment(content, idx, length, body_chars)
                continue

            # Skip quoted strings (single and double quotes)
            if char in ("'", '"'):
                idx = self._skip_quoted_string(content, idx, length, body_chars)
                continue

            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    break

            body_chars.append(char)
            idx += 1

        if brace_count != 0:
            return None
        return "".join(body_chars)

    def _parse_processes(self, content: str) -> list[Task]:
        """Parse process blocks from the Nextflow script content."""
        tasks = []
        for match in PROCESS_PATTERN.finditer(content):
            process_name = match.group(1)

            body = self._extract_process_body(content, match.end())
            if body is None:
                msg = f"Mismatched curly braces in process definition: {process_name}"
                raise InvalidWorkflowError(msg)

            # Search for container image string
            container_match = CONTAINER_PATTERN.search(body)
            container_image = container_match.group(1) if container_match else None

            # Search for CPUs limit
            cpus_match = CPUS_PATTERN.search(body)
            cpus = int(cpus_match.group(1)) if cpus_match else None

            # Search for Memory allocation
            memory_match = MEMORY_PATTERN.search(body)
            memory = memory_match.group(1) if memory_match else None

            # Assemble Resources and Task, wrapping validation errors
            try:
                resources = TaskResources(
                    cpus=cpus,
                    memory=memory,
                    container=container_image,
                )
                task = Task(
                    id=process_name,
                    name=process_name,
                    resources=resources,
                )
            except Exception as e:
                msg = f"Invalid resource values in process '{process_name}': {e}"
                raise InvalidWorkflowError(msg) from e

            tasks.append(task)
        return tasks

    def parse(self, path: Path, entrypoint: str | None = None) -> WorkflowBundle:
        """Parse a Nextflow workflow path into a WorkflowBundle.

        Args:
            path: Path to a workflow file or directory
            entrypoint: Optional entrypoint file name (for directory-based workflows)

        Returns:
            WorkflowBundle representation of the Nextflow workflow

        Raises:
            ParserError: If path is not found or is inaccessible
            InvalidWorkflowError: If workflow content is empty or malformed
        """
        if not path.exists():
            msg = f"Path does not exist: {path}"
            raise ParserError(msg)

        script_file = self._resolve_script_file(path, entrypoint)

        try:
            content = script_file.read_text(encoding="utf-8")
        except Exception as e:
            msg = f"Failed to read file {script_file}: {e}"
            raise ParserError(msg) from e

        # Basic validation: ensure file is not empty or whitespace only
        if not content.strip():
            msg = f"Workflow file is empty: {script_file}"
            raise InvalidWorkflowError(msg)

        tasks = self._parse_processes(content)
        metadata = WorkflowMetadata(name=script_file.stem)

        return WorkflowBundle(
            metadata=metadata,
            tasks=tasks,
        )
