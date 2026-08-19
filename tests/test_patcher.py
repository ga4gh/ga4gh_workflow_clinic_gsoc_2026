"""Unit tests for doctor patcher AST block finder and directive injection engine."""

from __future__ import annotations

from workflow_clinic.doctor.patcher import (
    detect_process_indentation,
    get_process_line_range,
    inject_directive,
    replace_directive_text,
)


def test_get_process_line_range_simple() -> None:
    code = """process FASTQC {
    cpus 2
    memory "4 GB"

    script:
    \"\"\"
    echo "Running FastQC"
    \"\"\"
}"""
    start, end = get_process_line_range(code, "FASTQC")
    assert start == 1
    assert end == 9


def test_get_process_line_range_multi_process_isolation() -> None:
    code = """process PROC_ONE {
    cpus 1
}

process PROC_TWO {
    cpus 4
    script:
    \"\"\"
    echo "inside proc two { brace in string }"
    \"\"\"
}

process PROC_THREE {
    cpus 2
}"""
    start, end = get_process_line_range(code, "PROC_TWO")
    assert start == 4
    assert end == 11


def test_detect_process_indentation() -> None:
    lines = [
        "process FASTQC {",
        "    container 'ubuntu'",
        "    cpus 2",
        "}",
    ]
    indent = detect_process_indentation(lines, 1, 4)
    assert indent == "    "


def test_inject_directive_with_todo_comment() -> None:
    code = """process NO_CONTAINER {
    cpus 1
}"""
    patched = inject_directive(
        code=code,
        process_name="NO_CONTAINER",
        directive='container "quay.io/biocontainers/ubuntu:22.04"',
        comment="// TODO: Replace with specific tool image",
    )
    assert 'container "quay.io/biocontainers/ubuntu:22.04"' in patched
    assert "// TODO: Replace with specific tool image" in patched
    assert "process NO_CONTAINER {\n    container" in patched


def test_replace_directive_text() -> None:
    code = 'container "ubuntu:latest"'
    patched = replace_directive_text(code, "ubuntu:latest", "ubuntu:22.04")
    assert patched == 'container "ubuntu:22.04"'


def test_get_process_line_range_brace_in_script_string() -> None:
    code = """process BASH_HEAVY {
    script:
    \"\"\"
    if [ -f file ]; then
        echo "found { brace } here"
    fi
    \"\"\"
}"""
    start, end = get_process_line_range(code, "BASH_HEAVY")
    assert start == 1
    assert end == 8
