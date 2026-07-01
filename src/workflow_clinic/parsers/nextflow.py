"""Nextflow workflow parser implementation.

This module parses Nextflow files (.nf) and directories containing a main.nf
file, extracting metadata and processes into a standard WorkflowBundle using AST.
"""

from pathlib import Path
from typing import Any

from groovy_parser.parser import parse_and_digest_groovy_content
from lark.exceptions import LarkError
from pydantic import ValidationError

from workflow_clinic.exceptions import InvalidWorkflowError, ParserError
from workflow_clinic.models import WorkflowBundle, WorkflowMetadata
from workflow_clinic.models.task import Task, TaskResources
from workflow_clinic.parsers.base import BaseParser


class NextflowParser(BaseParser):
    """Parser implementation for Nextflow workflows using Abstract Syntax Trees."""

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

    def _find_leaf_value(self, node: Any, leaf_types: list[str]) -> str | None:
        """Recursively search for a leaf node of specified types and return its value."""
        if not isinstance(node, dict):
            return None
        if "leaf" in node and node["leaf"] in leaf_types:
            return node.get("value")
        for child in node.get("children", []):
            val = self._find_leaf_value(child, leaf_types)
            if val is not None:
                return val
        return None

    def _collect_processes(self, node: Any) -> list[dict[str, Any]]:
        """Collect all command_expression nodes representing process declarations."""
        results: list[dict[str, Any]] = []
        if not isinstance(node, dict):
            return results
        if "rule" in node and "command_expression" in node["rule"]:
            children = node.get("children", [])
            # A process command requires at least 2 components:
            # the 'process' keyword/token and the process body/name.
            if len(children) >= 2:  # noqa: PLR2004
                first_val = self._find_leaf_value(children[0], ["IDENTIFIER"])
                if first_val == "process":
                    results.append(node)
        for child in node.get("children", []):
            results.extend(self._collect_processes(child))
        return results

    def _collect_block_statements(self, node: Any) -> list[dict[str, Any]]:
        """Collect all block_statement nodes under the closure block.

        We return early when finding a block_statement node to collect process
        directives without recursing into nested closures (e.g. within scripts).
        """
        results: list[dict[str, Any]] = []
        if not isinstance(node, dict):
            return results
        if "rule" in node and "block_statement" in node["rule"]:
            results.append(node)
            return results
        for child in node.get("children", []):
            results.extend(self._collect_block_statements(child))
        return results

    def _extract_directives(
        self, p_node: dict[str, Any]
    ) -> tuple[str | None, str | None, str | None]:
        """Extract container, cpus, and memory directive values from a process node."""
        container_image = None
        cpus = None
        memory = None

        statements = self._collect_block_statements(p_node)
        for s in statements:
            s_children = s.get("children", [])
            if not s_children:
                continue

            directive_name = self._find_leaf_value(s_children[0], ["IDENTIFIER"])
            if directive_name not in ("container", "cpus", "memory"):
                continue

            # Search the remaining arguments for any string or numeric literal
            literal_types = [
                "STRING_LITERAL",
                "STRING_LITERAL_PART",
                "FLOATING_POINT_LITERAL",
                "INTEGER_LITERAL",
                "NUMERIC_LITERAL",
            ]
            val = self._find_leaf_value({"children": s_children[1:]}, literal_types)
            if val is not None:
                if directive_name == "container":
                    container_image = val
                elif directive_name == "cpus":
                    cpus = val
                elif directive_name == "memory":
                    memory = val

        return container_image, cpus, memory

    def _parse_processes(self, ast: Any) -> list[Task]:
        """Traverse the AST to extract processes and map them to Task structures."""
        tasks = []
        processes = self._collect_processes(ast)
        for p in processes:
            children = p.get("children", [])
            # The second child contains the process identifier/name
            process_name = self._find_leaf_value(
                children[1], ["CAPITALIZED_IDENTIFIER", "IDENTIFIER"]
            )
            if not process_name:
                continue

            container_image, cpus, memory = self._extract_directives(p)

            # Construct resources and Task models
            try:
                cpus_val = None
                if cpus is not None:
                    try:
                        cpus_val = int(cpus)
                    except ValueError:
                        # Retain raw value so TaskResources validator raises validation error
                        cpus_val = cpus  # type: ignore[assignment]

                resources = TaskResources(
                    cpus=cpus_val,
                    memory=memory,
                    container=container_image,
                )
                task = Task(
                    id=process_name,
                    name=process_name,
                    resources=resources,
                )
            except (ValueError, ValidationError) as e:
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

        try:
            ast = parse_and_digest_groovy_content(content)
        except LarkError as e:
            msg = f"Syntax error in Nextflow file {script_file}: {e}"
            raise InvalidWorkflowError(msg) from e
        except Exception as e:
            msg = f"Failed to parse Nextflow file {script_file}: {e}"
            raise ParserError(msg) from e

        tasks = self._parse_processes(ast)
        metadata = WorkflowMetadata(name=script_file.stem)

        return WorkflowBundle(
            metadata=metadata,
            tasks=tasks,
        )
