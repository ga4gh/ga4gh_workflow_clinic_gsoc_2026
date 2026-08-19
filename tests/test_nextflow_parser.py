"""Unit and integration tests for the Nextflow parser."""

import importlib.util
from pathlib import Path

import pytest

HAS_NEXTFLOW = importlib.util.find_spec("groovy_parser") is not None

if not HAS_NEXTFLOW:
    pytest.skip("groovy-parser dependency not installed", allow_module_level=True)

from workflow_clinic.exceptions import InvalidWorkflowError, ParserError  # noqa: E402
from workflow_clinic.parsers import ParserRegistry  # noqa: E402
from workflow_clinic.parsers.nextflow import NextflowParser  # noqa: E402

VALID_NF_CONTENT = """
process FASTQC {
    container 'biocontainers/fastqc:v0.11.9'
    cpus 2
    memory '8 GB'

    script:
    \"\"\"
    fastqc input.fastq
    \"\"\"
}

process MULTIQC {
    container "biocontainers/multiqc:v1.11"
    cpus = 4
    memory = "16.GB"

    script:
    \"\"\"
    multiqc .
    \"\"\"
}
"""

MISMATCHED_BRACES_CONTENT = """
process BAD_PROCESS {
    container 'biocontainers/bad:v1'
    cpus 2
    // Missing closing brace for process
"""

BASH_BRACES_CONTENT = """
process ALIGN {
    container 'biocontainers/bwa:v0.7.17'
    cpus 4
    memory '16 GB'

    script:
    \"\"\"
    bwa mem -t ${task.cpus} ref.fa reads.fq > aligned.sam
    if [ -f aligned.sam ]; then
        echo "alignment complete"
    fi
    \"\"\"
}
"""

INVALID_RESOURCES_CONTENT = """
process BAD_RESOURCES {
    container 'biocontainers/tool:v1'
    cpus 0
    memory '8 GB'

    script:
    \"\"\"
    echo "hello"
    \"\"\"
}
"""


def test_can_parse_methods(tmp_path: Path) -> None:
    """Verify can_parse behavior for files and directories."""
    # Test valid .nf file extension
    nf_file = tmp_path / "main.nf"
    nf_file.write_text("content")
    assert NextflowParser.can_parse(nf_file) is True

    # Test invalid file extension
    txt_file = tmp_path / "main.txt"
    txt_file.write_text("content")
    assert NextflowParser.can_parse(txt_file) is False

    # Test valid directory (contains main.nf)
    valid_dir = tmp_path / "valid_workflow"
    valid_dir.mkdir()
    (valid_dir / "main.nf").write_text("content")
    assert NextflowParser.can_parse(valid_dir) is True

    # Test invalid directory (no main.nf)
    invalid_dir = tmp_path / "invalid_workflow"
    invalid_dir.mkdir()
    assert NextflowParser.can_parse(invalid_dir) is False


def test_nextflow_parser_registration() -> None:
    """Verify NextflowParser is registered automatically under 'nextflow'."""
    parser = ParserRegistry.get_parser("nextflow")
    assert isinstance(parser, NextflowParser)


def test_parsing_valid_file(tmp_path: Path) -> None:
    """Verify that a valid Nextflow script is parsed successfully."""
    nf_file = tmp_path / "my_workflow.nf"
    nf_file.write_text(VALID_NF_CONTENT)

    parser = NextflowParser()
    bundle = parser.parse(nf_file)

    # Verify metadata
    assert bundle.metadata.name == "my_workflow"

    # Verify tasks list
    assert len(bundle.tasks) == 2

    # Verify FASTQC task details
    fastqc = next(t for t in bundle.tasks if t.name == "FASTQC")
    assert fastqc.id == "FASTQC"
    assert fastqc.resources.container == "biocontainers/fastqc:v0.11.9"
    assert fastqc.resources.cpus == 2
    assert fastqc.resources.memory == "8 GB"

    # Verify MULTIQC task details (testing equals-sign formatting support)
    multiqc = next(t for t in bundle.tasks if t.name == "MULTIQC")
    assert multiqc.id == "MULTIQC"
    assert multiqc.resources.container == "biocontainers/multiqc:v1.11"
    assert multiqc.resources.cpus == 4
    assert multiqc.resources.memory == "16.GB"


def test_parsing_directory(tmp_path: Path) -> None:
    """Verify parsing a directory using default and custom entrypoints."""
    workflow_dir = tmp_path / "pipeline"
    workflow_dir.mkdir()

    # Default main.nf
    main_file = workflow_dir / "main.nf"
    main_file.write_text(VALID_NF_CONTENT)

    parser = NextflowParser()

    # Test parsing without specifying entrypoint
    bundle = parser.parse(workflow_dir)
    assert bundle.metadata.name == "pipeline"
    assert len(bundle.tasks) == 2

    # Test parsing specifying custom entrypoint file name
    custom_file = workflow_dir / "custom.nf"
    custom_file.write_text(VALID_NF_CONTENT)
    bundle_custom = parser.parse(workflow_dir, entrypoint="custom.nf")
    assert bundle_custom.metadata.name == "custom"
    assert len(bundle_custom.tasks) == 2


def test_include_statement_dependency_tracing(tmp_path: Path) -> None:
    """Verify NextflowParser recursively follows DSL2 include statements to construct task bundle."""
    pipeline_dir = tmp_path / "my_pipeline"
    pipeline_dir.mkdir()

    modules_dir = pipeline_dir / "modules"
    modules_dir.mkdir()

    module_file = modules_dir / "fastqc.nf"
    module_file.write_text("""
    process FASTQC {
        container 'biocontainers/fastqc:v0.11.9'
        cpus 2
        memory '4 GB'

        script:
        ""
        fastqc $reads
        ""
    }
    """)

    main_file = pipeline_dir / "main.nf"
    main_file.write_text("""
    include { FASTQC } from './modules/fastqc'

    workflow {
        FASTQC()
    }
    """)

    parser = NextflowParser()
    bundle = parser.parse(main_file)

    assert bundle.metadata.name == "main"
    assert len(bundle.tasks) == 1
    task = bundle.tasks[0]
    assert task.name == "FASTQC"
    assert task.resources.container == "biocontainers/fastqc:v0.11.9"
    assert task.resources.cpus == 2


def test_discover_dependencies(tmp_path: Path) -> None:
    """Verify discover_dependencies returns all imported workflow files."""
    pipeline_dir = tmp_path / "my_pipeline"
    pipeline_dir.mkdir()

    modules_dir = pipeline_dir / "modules"
    modules_dir.mkdir()

    module_file = modules_dir / "fastqc.nf"
    module_file.write_text("process FASTQC {}")

    main_file = pipeline_dir / "main.nf"
    main_file.write_text("include { FASTQC } from './modules/fastqc'")

    parser = NextflowParser()
    deps = parser.discover_dependencies(main_file)

    assert len(deps) == 2
    assert main_file in deps
    assert module_file in deps


def test_error_handling_scenarios(tmp_path: Path) -> None:
    """Verify parser raises appropriate exceptions for invalid scenarios."""
    parser = NextflowParser()

    # Scenario 1: Path does not exist
    non_existent = tmp_path / "missing.nf"
    with pytest.raises(ParserError) as exc_info:
        parser.parse(non_existent)
    assert "Path does not exist" in str(exc_info.value)

    # Scenario 2: Directory entrypoint file not found
    workflow_dir = tmp_path / "empty_dir"
    workflow_dir.mkdir()
    with pytest.raises(ParserError) as exc_info:
        parser.parse(workflow_dir, entrypoint="missing.nf")
    assert "Entrypoint file not found" in str(exc_info.value)

    # Scenario 3: Empty workflow file
    empty_file = tmp_path / "empty.nf"
    empty_file.write_text("   \n   ")
    with pytest.raises(InvalidWorkflowError) as exc_info:
        parser.parse(empty_file)
    assert "Workflow file is empty" in str(exc_info.value)

    # Scenario 4: Mismatched curly braces (raises AST syntax error)
    bad_file = tmp_path / "bad.nf"
    bad_file.write_text(MISMATCHED_BRACES_CONTENT)
    with pytest.raises(InvalidWorkflowError) as exc_info:
        parser.parse(bad_file)
    assert "Syntax error in Nextflow file" in str(exc_info.value)


def test_bash_braces_in_script(tmp_path: Path) -> None:
    """Verify parser handles curly braces inside bash script blocks correctly."""
    nf_file = tmp_path / "bash_test.nf"
    nf_file.write_text(BASH_BRACES_CONTENT)

    parser = NextflowParser()
    bundle = parser.parse(nf_file)

    # Should successfully parse without mismatched brace errors
    assert len(bundle.tasks) == 1
    align = bundle.tasks[0]
    assert align.name == "ALIGN"
    assert align.resources.container == "biocontainers/bwa:v0.7.17"
    assert align.resources.cpus == 4
    assert align.resources.memory == "16 GB"


def test_invalid_resource_values(tmp_path: Path) -> None:
    """Verify parser wraps Pydantic ValidationError in InvalidWorkflowError."""
    nf_file = tmp_path / "bad_resources.nf"
    nf_file.write_text(INVALID_RESOURCES_CONTENT)

    parser = NextflowParser()
    with pytest.raises(InvalidWorkflowError) as exc_info:
        parser.parse(nf_file)
    assert "Invalid resource values" in str(exc_info.value)


def test_parse_simple_nf_file(tmp_path: Path) -> None:
    """Verify end-to-end parsing of a simple Nextflow file."""
    real_content = """
    process BWA_ALIGN {
        container 'nf-core/bwa:0.7.17'
        cpus 8
        memory '32 GB'

        script:
        \"\"\"
        bwa mem -t 8 ref.fa reads.fq > aligned.sam
        \"\"\"
    }
    """
    nf_file = tmp_path / "real_workflow.nf"
    nf_file.write_text(real_content)

    parser = NextflowParser()
    bundle = parser.parse(nf_file)

    assert bundle.metadata.name == "real_workflow"
    assert len(bundle.tasks) == 1
    task = bundle.tasks[0]
    assert task.name == "BWA_ALIGN"
    assert task.resources.container == "nf-core/bwa:0.7.17"
    assert task.resources.cpus == 8
    assert task.resources.memory == "32 GB"


def test_parse_dummy_nf_fixture() -> None:
    """Verify that NextflowParser correctly parses the real-world dummy.nf fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / "dummy.nf"
    parser = NextflowParser()
    bundle = parser.parse(fixture_path)

    assert bundle.metadata.name == "dummy"
    assert len(bundle.tasks) == 3

    # Check that processes are parsed and resource limits exist
    processes = {t.name for t in bundle.tasks}
    assert processes == {"FASTQC", "TRIM_READS", "ALIGN"}

    fastqc = next(t for t in bundle.tasks if t.name == "FASTQC")
    assert (
        fastqc.resources.container == "quay.io/biocontainers/fastqc:0.12.1--hdfd78af_0"
    )
    assert fastqc.resources.cpus == 2
    assert fastqc.resources.memory == "4"  # Evaluates closure to first numeric literal


def test_parser_populates_line_numbers(tmp_path: Path) -> None:
    """Verify line numbers match actual process declarations in source."""
    content = "\n\nprocess FASTQC {\n    cpus 2\n}\n"
    nf_file = tmp_path / "test.nf"
    nf_file.write_text(content)

    parser = NextflowParser()
    bundle = parser.parse(nf_file)

    assert len(bundle.tasks) == 1
    task = bundle.tasks[0]

    assert task.name == "FASTQC"
    assert task.file_path.endswith("test.nf")
    assert task.line_number == 3


def test_parser_ast_line_numbers_with_comments(tmp_path: Path) -> None:
    """Verify AST line extraction is accurate even when comments contain process keywords."""
    content = (
        "// Documentation mentioning process FAKE_PROCESS { ... }\n"
        "/* Multiline block comment\n"
        "   process ANOTHER_FAKE {\n"
        "*/\n"
        "\n"
        "process REAL_PROCESS {\n"
        "    cpus 4\n"
        "    script:\n"
        '    "echo real"\n'
        "}\n"
    )
    nf_file = tmp_path / "comment_test.nf"
    nf_file.write_text(content)

    parser = NextflowParser()
    bundle = parser.parse(nf_file)

    assert len(bundle.tasks) == 1
    task = bundle.tasks[0]
    assert task.name == "REAL_PROCESS"
    assert task.line_number == 6
