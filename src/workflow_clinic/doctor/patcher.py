"""AST-anchored patch engine for modifying Nextflow and Snakemake workflow source code."""

from __future__ import annotations

import re

from groovy_parser.parser import parse_and_digest_groovy_content
from lark.exceptions import LarkError


def get_process_line_range(  # noqa: C901, PLR0912, PLR0915
    code: str, process_name: str
) -> tuple[int, int]:
    """Find the 1-based (start_line, end_line) range of a process definition in Nextflow code.

    Attempts AST parsing via groovy_parser first. If AST parsing fails or line numbers are missing,
    falls back to string-safe regex matching process process_name { ... }.

    Args:
        code: Full source code text of the Nextflow file.
        process_name: Name of the process block to find.

    Returns:
        Tuple of (start_line, end_line) 1-based inclusive indices.
    """
    lines = code.splitlines()

    # Try AST extraction first
    try:
        ast, _ = parse_and_digest_groovy_content(code)
        if isinstance(ast, list):
            for node in ast:
                if (
                    isinstance(node, dict)
                    and node.get("type") == "process"
                    and node.get("name") == process_name
                ):
                    start = node.get("start_line") or node.get("line_number")
                    end = node.get("end_line")
                    if start and end:
                        return (int(start), int(end))
    except (LarkError, Exception):  # noqa: BLE001, S110
        pass

    # Regex fallback with string-aware brace matching
    process_pattern = re.compile(
        rf"^\s*process\s+{re.escape(process_name)}\s*\{{", re.MULTILINE
    )
    match = process_pattern.search(code)
    if not match:
        return (1, len(lines))

    start_line = code[: match.start()].count("\n") + 1

    brace_depth = 0
    in_single_quote = False
    in_double_quote = False
    in_triple_quote = False
    triple_quote_char: str | None = None
    in_line_comment = False
    in_block_comment = False
    process_ended = False
    end_line = start_line

    char_idx = match.start()
    while char_idx < len(code):
        ch = code[char_idx]

        if not in_single_quote and not in_double_quote and not in_triple_quote:
            if in_line_comment:
                if ch == "\n":
                    in_line_comment = False
                char_idx += 1
                continue

            if in_block_comment:
                if code[char_idx : char_idx + 2] == "*/":
                    in_block_comment = False
                    char_idx += 2
                    continue
                char_idx += 1
                continue

            if code[char_idx : char_idx + 2] == "//":
                in_line_comment = True
                char_idx += 2
                continue

            if code[char_idx : char_idx + 2] == "/*":
                in_block_comment = True
                char_idx += 2
                continue

        if code[char_idx : char_idx + 3] in ('"""', "'''"):
            current_triple = code[char_idx : char_idx + 3]
            if not in_triple_quote:
                in_triple_quote = True
                triple_quote_char = current_triple
            elif triple_quote_char == current_triple:
                in_triple_quote = False
                triple_quote_char = None
            char_idx += 3
            continue

        if not in_triple_quote:
            if ch == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
                char_idx += 1
                continue

            if ch == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
                char_idx += 1
                continue

        if (
            not in_single_quote
            and not in_double_quote
            and not in_triple_quote
            and not in_line_comment
            and not in_block_comment
        ):
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    end_line = code[: char_idx + 1].count("\n") + 1
                    process_ended = True
                    break

        char_idx += 1

    if not process_ended:
        end_line = len(lines)

    return (start_line, end_line)


def detect_process_indentation(lines: list[str], start_line: int, end_line: int) -> str:
    """Auto-detect leading indentation inside a process block."""
    for idx in range(start_line, min(end_line - 1, len(lines))):
        line = lines[idx]
        stripped = line.strip()
        if stripped and not stripped.startswith("//") and not stripped.startswith("/*"):
            leading_whitespace = line[: len(line) - len(line.lstrip())]
            return leading_whitespace or "    "
    return "    "


def inject_directive(
    code: str,
    process_name: str,
    directive: str,
    comment: str | None = None,
) -> str:
    """Inject a directive into a process block right after its declaration header."""
    lines = code.splitlines(keepends=True)
    start_line, end_line = get_process_line_range(code, process_name)
    indent = detect_process_indentation(
        [line.rstrip("\r\n") for line in lines], start_line, end_line
    )

    formatted_comment = f"  {comment.strip()}" if comment else ""
    newline_char = "\n"
    new_line = f"{indent}{directive.strip()}{formatted_comment}{newline_char}"

    insert_idx = start_line
    lines.insert(insert_idx, new_line)
    return "".join(lines)


def replace_directive_text(code: str, old_text: str, new_text: str) -> str:
    """Replace exact directive text occurrences safely across source code.

    Args:
        code: Original source code text.
        old_text: Substring to match and replace.
        new_text: Substring to insert in place.

    Returns:
        Patched source code string.
    """
    if old_text not in code:
        return code
    return code.replace(old_text, new_text, 1)
