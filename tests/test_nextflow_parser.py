"""Unit and integration tests for the Nextflow parser."""

from pathlib import Path

import pytest

from workflow_clinic.exceptions import InvalidWorkflowError, ParserError
from workflow_clinic.parsers import ParserRegistry
from workflow_clinic.parsers.nextflow import NextflowParser

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
    assert len(bundle.tasks) == 2  # noqa: PLR2004

    # Verify FASTQC task details
    fastqc = next(t for t in bundle.tasks if t.name == "FASTQC")
    assert fastqc.id == "FASTQC"
    assert fastqc.resources.container == "biocontainers/fastqc:v0.11.9"
    assert fastqc.resources.cpus == 2  # noqa: PLR2004
    assert fastqc.resources.memory == "8 GB"

    # Verify MULTIQC task details (testing equals-sign formatting support)
    multiqc = next(t for t in bundle.tasks if t.name == "MULTIQC")
    assert multiqc.id == "MULTIQC"
    assert multiqc.resources.container == "biocontainers/multiqc:v1.11"
    assert multiqc.resources.cpus == 4  # noqa: PLR2004
    assert multiqc.resources.memory == "16.GB"


def test_parsing_directory(tmp_path: Path) -> None:
    """Verify parsing a directory using default and custom entrypoints."""
    workflow_dir = tmp_path / "pipeline"
    workflow_dir.mkdir()

    # Default main.nf
    main_file = workflow_dir / "main.nf"
    main_file.write_text(VALID_NF_CONTENT)

    parser = NextflowParser()

    # Test parsing without specifying entrypoint (defaults to main.nf)
    bundle = parser.parse(workflow_dir)
    assert bundle.metadata.name == "main"
    assert len(bundle.tasks) == 2  # noqa: PLR2004

    # Test parsing specifying custom entrypoint file name
    custom_file = workflow_dir / "custom.nf"
    custom_file.write_text(VALID_NF_CONTENT)
    bundle_custom = parser.parse(workflow_dir, entrypoint="custom.nf")
    assert bundle_custom.metadata.name == "custom"
    assert len(bundle_custom.tasks) == 2  # noqa: PLR2004


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
    assert align.resources.cpus == 4  # noqa: PLR2004
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
    assert task.resources.cpus == 8  # noqa: PLR2004
    assert task.resources.memory == "32 GB"
