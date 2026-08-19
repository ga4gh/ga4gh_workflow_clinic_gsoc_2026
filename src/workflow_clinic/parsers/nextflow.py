import re
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
            return (path / "main.nf").is_file() or any(path.rglob("*.nf"))
        return False

    def discover_dependencies(  # noqa: C901
        self, path: Path, entrypoint: str | None = None
    ) -> list[Path]:
        """Discover Nextflow workflow files by following DSL2 include statements.

        Recursively resolves imported subworkflows/modules starting from the
        given entrypoint file or directory.
        """
        if not path.exists():
            return []

        files_to_visit: list[Path] = []
        if path.is_file() or entrypoint:
            try:
                files_to_visit.append(self._resolve_script_file(path, entrypoint))
            except ParserError:
                return []
        elif path.is_dir():
            main_file = path / "main.nf"
            if main_file.is_file():
                files_to_visit.append(main_file)
            else:
                ignored_parts = {".git", ".nextflow", "work", "bin"}
                files_to_visit = [
                    f
                    for f in sorted(path.rglob("*.nf"))
                    if not any(part in ignored_parts for part in f.parts)
                ]

        discovered: list[Path] = []
        visited: set[Path] = set()

        def _traverse(target: Path) -> None:
            resolved = target.resolve()
            if resolved in visited or not target.is_file():
                return
            visited.add(resolved)
            discovered.append(target)

            try:
                content = target.read_text(encoding="utf-8")
                base_dir = target.parent
                for inc in self._extract_include_paths(content):
                    inc_file = self._resolve_include_file(base_dir, inc)
                    if inc_file:
                        _traverse(inc_file)
            except (InvalidWorkflowError, ParserError, OSError):
                pass

        for start_file in files_to_visit:
            _traverse(start_file)

        return discovered

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

    def _extract_include_paths(self, content: str) -> list[str]:
        """Extract import path strings from Nextflow DSL2 include statements.

        Example:
            include { FASTQC } from './modules/fastqc/main'
            --> returns ['./modules/fastqc/main']
        """
        pattern = r"""include\s*\{[^}]*\}\s*from\s*['"]([^'"]+)['"]"""
        return re.findall(pattern, content)

    def _resolve_include_file(
        self, base_dir: Path, include_path_str: str
    ) -> Path | None:
        """Resolve a relative include path to an actual .nf script file."""
        target = base_dir / include_path_str
        if target.is_file():
            return target
        if (base_dir / f"{include_path_str}.nf").is_file():
            return base_dir / f"{include_path_str}.nf"
        if (target / "main.nf").is_file():
            return target / "main.nf"
        return None

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
            if len(children) >= 2:  # noqa: PLR2004
                first_val = self._find_leaf_value(children[0], ["IDENTIFIER"])
                if first_val == "process":
                    results.append(node)
        for child in node.get("children", []):
            results.extend(self._collect_processes(child))
        return results

    def _collect_block_statements(self, node: Any) -> list[dict[str, Any]]:
        """Collect all block_statement nodes under the closure block."""
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
        """Recursively find the script/shell block statement in a process AST."""
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
        """Recursively collect leaf values representing the script content."""
        if not isinstance(node, dict):
            return ""
        if "leaf" in node:
            leaf_type = node["leaf"]
            if leaf_type in ("GSTRING_BEGIN", "GSTRING_END"):
                return ""
            return str(node.get("value", ""))
        parts = [self._collect_script_text(child) for child in node.get("children", [])]
        return "".join(parts)

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

    def _find_process_line_number(self, content: str, process_name: str) -> int | None:
        """Find the 1-based line number of a process declaration, ignoring comments."""
        # Replace block comments with equal number of newlines to preserve line numbering
        cleaned = re.sub(
            r"/\*.*?\*/",
            lambda m: "\n" * m.group(0).count("\n"),
            content,
            flags=re.DOTALL,
        )
        # Match process declaration anchored at line start (ignores single-line // comments)
        match = re.search(
            r"^[ \t]*process\s+" + re.escape(process_name) + r"\s*\{",
            cleaned,
            flags=re.MULTILINE,
        )
        if match:
            return cleaned[: match.start()].count("\n") + 1
        return None

    def _parse_processes(self, ast: Any, script_file: Path, content: str) -> list[Task]:
        """Traverse the AST to extract processes and map them to Task structures."""
        tasks = []
        processes = self._collect_processes(ast)
        for p in processes:
            children = p.get("children", [])
            process_name = self._find_leaf_value(
                children[1], ["CAPITALIZED_IDENTIFIER", "IDENTIFIER"]
            )
            if not process_name:
                continue

            line_number = self._find_process_line_number(content, process_name)

            container_image, cpus, memory = self._extract_directives(p)

            script_text: str | None = None
            script_stmt = self._find_script_statement(p)
            if script_stmt and len(script_stmt.get("children", [])) >= 3:  # noqa: PLR2004
                script_text = self._collect_script_text(script_stmt["children"][2])

            try:
                cpus_val = None
                if cpus is not None:
                    try:
                        cpus_val = int(cpus)
                    except ValueError:
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
                    file_path=str(script_file),
                    line_number=line_number,
                )
            except (ValueError, ValidationError) as e:
                msg = f"Invalid resource values in process '{process_name}': {e}"
                raise InvalidWorkflowError(msg) from e

            tasks.append(task)
        return tasks

    def _parse_file_tasks_recursive(
        self, script_file: Path, visited: set[Path] | None = None
    ) -> list[Task]:
        """Parse a Nextflow file and recursively follow DSL2 include statements."""
        if visited is None:
            visited = set()

        resolved_path = script_file.resolve()
        if resolved_path in visited:
            return []
        visited.add(resolved_path)

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

        tasks = self._parse_processes(ast, script_file, content)

        # Recursively follow DSL2 include statements
        base_dir = script_file.parent
        include_paths = self._extract_include_paths(content)
        for inc in include_paths:
            inc_file = self._resolve_include_file(base_dir, inc)
            if inc_file and inc_file.resolve() not in visited:
                try:
                    sub_tasks = self._parse_file_tasks_recursive(inc_file, visited)
                    tasks.extend(sub_tasks)
                except (InvalidWorkflowError, ParserError):
                    pass

        return tasks

    def parse(self, path: Path, entrypoint: str | None = None) -> WorkflowBundle:
        """Parse a Nextflow workflow path into a WorkflowBundle.

        Supports single .nf files, specified entrypoints, or directory scanning
        with recursive DSL2 include statement graph traversal.
        """
        if not path.exists():
            msg = f"Path does not exist: {path}"
            raise ParserError(msg)

        if path.is_file() or entrypoint:
            script_file = self._resolve_script_file(path, entrypoint)
            tasks = self._parse_file_tasks_recursive(script_file)
            metadata = WorkflowMetadata(name=script_file.stem)
            return WorkflowBundle(metadata=metadata, tasks=tasks)

        if path.is_dir():
            # 1. Primary entrypoint: main.nf
            main_file = path / "main.nf"
            if main_file.is_file():
                tasks = self._parse_file_tasks_recursive(main_file)
                metadata = WorkflowMetadata(name=path.name)
                return WorkflowBundle(metadata=metadata, tasks=tasks)

            # 2. Directory scan fallback (with include graph traversal)
            ignored_parts = {".git", ".nextflow", "work", "bin"}
            nf_files = [
                f
                for f in sorted(path.rglob("*.nf"))
                if not any(part in ignored_parts for part in f.parts)
            ]
            if not nf_files:
                msg = f"No .nf workflow files found in directory: {path}"
                raise ParserError(msg)

            all_tasks: list[Task] = []
            visited: set[Path] = set()
            for f in nf_files:
                if f.resolve() not in visited:
                    try:
                        file_tasks = self._parse_file_tasks_recursive(f, visited)
                        all_tasks.extend(file_tasks)
                    except (InvalidWorkflowError, ParserError):
                        pass

            if not all_tasks:
                msg = f"No valid tasks parsed from .nf files in directory: {path}"
                raise InvalidWorkflowError(msg)

            metadata = WorkflowMetadata(name=path.name)
            return WorkflowBundle(metadata=metadata, tasks=all_tasks)

        msg = f"Unsupported path type: {path}"
        raise ParserError(msg)
