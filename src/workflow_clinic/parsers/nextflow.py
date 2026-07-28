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
            return any(path.rglob("*.nf"))
        return False

    def _resolve_script_file(self, path: Path, entrypoint: str | None) -> Path:
        """Resolve the script file path from the given directory/file path."""
        if path.is_file():
            if path.suffix != ".nf":
                msg = f"Unsupported Nextflow file extension: {path}"
                raise ParserError(msg)
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
        """Recursively search for a leaf node of specified types and return its value.

        NOTE: This performs a depth-first search and returns the FIRST matching
        leaf. It does not evaluate expressions. For closure-based directives
        like ``memory { 4.GB * task.attempt }``, this returns only the first
        numeric literal (``"4"``), ignoring unit suffixes and arithmetic.
        """
        if not isinstance(node, dict):
            return None
        if "leaf" in node and node["leaf"] in leaf_types:
            return node.get("value")
        for child in node.get("children", []):
            val = self._find_leaf_value(child, leaf_types)
            if val is not None:
                return val
        return None

    def _find_all_leaf_values(self, node: Any, leaf_types: list[str]) -> list[str]:
        """Recursively search for all leaf nodes of specified types and return their values."""
        results: list[str] = []
        if not isinstance(node, dict):
            return results
        if "leaf" in node and node["leaf"] in leaf_types:
            val = node.get("value")
            if val is not None:
                results.append(str(val))
        for child in node.get("children", []):
            results.extend(self._find_all_leaf_values(child, leaf_types))
        return results

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

    def _find_script_statement(self, node: Any) -> dict[str, Any] | None:
        """Recursively find the script/shell block statement in a process AST.

        Looks for a labeled statement of the form ``script:`` or ``shell:``
        and returns the containing node so the caller can extract the
        string content that follows the colon.
        """
        if not isinstance(node, dict):
            return None
        children = node.get("children", [])
        if len(children) >= 2:  # noqa: PLR2004
            identifier_val = self._find_leaf_value(children[0], ["IDENTIFIER"])
            colon_val = self._find_leaf_value(children[1], ["COLON"])
            if identifier_val in ("script", "shell") and colon_val == ":":
                return node
        for child in children:
            result = self._find_script_statement(child)
            if result is not None:
                return result
        return None

    def _collect_script_text(self, node: Any) -> str:
        """Recursively collect leaf values representing the script content.

        Skips GString begin/end delimiters (triple-quotes) so only the
        actual script body text is returned.
        """
        if not isinstance(node, dict):
            return ""
        if "leaf" in node:
            leaf_type = node["leaf"]
            if leaf_type in ("GSTRING_BEGIN", "GSTRING_END"):
                return ""
            return str(node.get("value", ""))
        parts = [self._collect_script_text(child) for child in node.get("children", [])]
        return "".join(parts)

    def _extract_container_image(
        self, s_children: list[Any], literal_types: list[str]
    ) -> str | None:
        """Select preferred container image literal from statement children."""
        literals = self._find_all_leaf_values(
            {"children": s_children[1:]}, literal_types
        )
        pinned = [item for item in literals if ":" in item or "/" in item]
        real_images = [item for item in pinned if item not in ("singularity", "docker")]
        if real_images:
            return real_images[0]
        if pinned:
            return pinned[0]
        if literals:
            return literals[0]
        return None

    def _extract_directives(
        self, p_node: dict[str, Any]
    ) -> tuple[str | None, str | None, str | None]:
        """Extract container, cpus, and memory directive values from a process node."""
        container_image = None
        cpus = None
        memory = None

        statements = self._collect_block_statements(p_node)
        literal_types = [
            "STRING_LITERAL",
            "STRING_LITERAL_PART",
            "FLOATING_POINT_LITERAL",
            "INTEGER_LITERAL",
            "NUMERIC_LITERAL",
        ]

        for s in statements:
            s_children = s.get("children", [])
            if not s_children:
                continue

            directive_name = self._find_leaf_value(s_children[0], ["IDENTIFIER"])
            if directive_name == "container":
                container_image = self._extract_container_image(
                    s_children, literal_types
                )
            elif directive_name in ("cpus", "memory"):
                val = self._find_leaf_value({"children": s_children[1:]}, literal_types)
                if val is not None:
                    if directive_name == "cpus":
                        cpus = val
                    elif directive_name == "memory":
                        memory = val

        return container_image, cpus, memory

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

            # Extract script block content for downstream rule inspection
            script_text: str | None = None
            script_stmt = self._find_script_statement(p)
            if script_stmt and len(script_stmt.get("children", [])) >= 3:  # noqa: PLR2004
                script_text = self._collect_script_text(script_stmt["children"][2])

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
                    command=script_text,
                    resources=resources,
                )
            except (ValueError, ValidationError) as e:
                msg = f"Invalid resource values in process '{process_name}': {e}"
                raise InvalidWorkflowError(msg) from e

            tasks.append(task)
        return tasks

    def _parse_file_tasks(self, script_file: Path) -> list[Task]:
        """Parse a single Nextflow file and return its extracted process tasks."""
        try:
            content = script_file.read_text(encoding="utf-8")
        except Exception as e:
            msg = f"Failed to read file {script_file}: {e}"
            raise ParserError(msg) from e

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

        return self._parse_processes(ast)

    def parse(self, path: Path, entrypoint: str | None = None) -> WorkflowBundle:
        """Parse a Nextflow workflow path into a WorkflowBundle.

        Supports single .nf files, specified entrypoints, or directory scanning.
        """
        if not path.exists():
            msg = f"Path does not exist: {path}"
            raise ParserError(msg)

        if entrypoint or path.is_file():
            script_file = self._resolve_script_file(path, entrypoint)
            tasks = self._parse_file_tasks(script_file)
            metadata = WorkflowMetadata(name=script_file.stem)
            return WorkflowBundle(metadata=metadata, tasks=tasks)

        if path.is_dir():
            nf_files = sorted(path.rglob("*.nf"))
            if not nf_files:
                msg = f"No .nf workflow files found in directory: {path}"
                raise ParserError(msg)

            all_tasks: list[Task] = []
            for f in nf_files:
                try:
                    tasks = self._parse_file_tasks(f)
                    all_tasks.extend(tasks)
                except (InvalidWorkflowError, ParserError):
                    pass

            metadata = WorkflowMetadata(name=path.name)
            return WorkflowBundle(metadata=metadata, tasks=all_tasks)

        msg = f"Unsupported path type: {path}"
        raise ParserError(msg)
