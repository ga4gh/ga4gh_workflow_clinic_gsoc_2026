import pytest
from pydantic import ValidationError

from workflow_clinic.models import Task, TaskResources, WorkflowBundle, WorkflowMetadata


def test_workflow_bundle_creation():
    """Verify that WorkflowBundle instantiates correctly with nested metadata and tasks."""
    bundle = WorkflowBundle(
        metadata=WorkflowMetadata(
            name="test-workflow", version="1.0", author="Test Author"
        ),
        tasks=[Task(id="FASTQC", name="fastqc")],
    )
    assert bundle.metadata.name == "test-workflow"
    assert bundle.metadata.version == "1.0"
    assert bundle.metadata.author == "Test Author"
    assert len(bundle.tasks) == 1
    assert bundle.tasks[0].id == "FASTQC"
    assert bundle.tasks[0].name == "fastqc"


@pytest.mark.parametrize("cpu_val", [1, 4, 64, None])
def test_valid_cpu_values(cpu_val):
    """Verify that valid positive integer CPU counts or None are accepted."""
    resources = TaskResources(cpus=cpu_val)
    assert resources.cpus == cpu_val


@pytest.mark.parametrize(
    ("memory_val", "expected"),
    [
        ("8GB", "8GB"),
        ("8 GB", "8 GB"),
        ("8.GB", "8.GB"),
        ("8.g", "8.g"),
        ("512MB", "512MB"),
        ("4GiB", "4GiB"),
        ("1024", "1024"),
        ("  8.GB  ", "8.GB"),  # Tests stripping with units
        ("   1024   ", "1024"),  # Tests stripping without units
        (None, None),
    ],
)
def test_valid_memory_formats(memory_val, expected):
    """Verify that common memory formats from various workflow engines are correctly parsed."""
    resources = TaskResources(memory=memory_val)
    assert resources.memory == expected


@pytest.mark.parametrize("invalid_cpu", [0, -5, -1])
def test_invalid_cpu_values_negative_or_zero(invalid_cpu):
    """Verify that CPU counts <= 0 raise validation error."""
    with pytest.raises(ValidationError):
        TaskResources(cpus=invalid_cpu)


def test_invalid_cpu_value_type():
    """Verify that non-integer CPU values raise validation error."""
    with pytest.raises(ValidationError):
        TaskResources(cpus="four")


@pytest.mark.parametrize(
    "invalid_memory",
    [
        "",  # Empty string
        "GB",  # No number
        "-8GB",  # Negative number
        "large",  # Plain text
        "16 GBs",  # Invalid plural unit
        "16GB B",  # Extra space and characters
        "8.",  # Trailing dot without unit
    ],
)
def test_invalid_memory_formats(invalid_memory):
    """Verify that invalid memory specification formats raise validation error."""
    with pytest.raises(ValidationError):
        TaskResources(memory=invalid_memory)


@pytest.mark.parametrize(
    ("model_cls", "kwargs", "field_name"),
    [
        (WorkflowMetadata, {"name": ""}, "name"),
        (WorkflowMetadata, {"name": "   "}, "name"),
        (Task, {"id": "", "name": "fastqc"}, "id"),
        (Task, {"id": "   ", "name": "fastqc"}, "id"),
        (Task, {"id": "FASTQC", "name": ""}, "name"),
        (Task, {"id": "FASTQC", "name": "   "}, "name"),
    ],
)
def test_empty_or_whitespace_fields(model_cls, kwargs, field_name):
    """Verify that empty or whitespace-only inputs for required name/id fields raise validation error."""
    with pytest.raises(ValidationError) as excinfo:
        model_cls(**kwargs)
    assert field_name in str(excinfo.value)


def test_workflow_bundle_json_roundtrip():
    """Verify that a complex WorkflowBundle can be correctly serialized to and deserialized from JSON."""
    original_bundle = WorkflowBundle(
        metadata=WorkflowMetadata(
            name="alignment-workflow",
            version="2.1.0",
            author="Bioinformatics Team",
            description="Align raw reads using BWA and run QC",
        ),
        tasks=[
            Task(
                id="FASTQC",
                name="run-fastqc",
                command="fastqc input.fastq",
                resources=TaskResources(cpus=2, memory="4GB"),
                inputs=["input.fastq"],
                outputs=["qc_report.html"],
            ),
            Task(
                id="BWA_ALIGN",
                name="align-reads",
                command="bwa mem ref.fa input.fastq > aligned.sam",
                resources=TaskResources(cpus=8, memory="32.GB"),
                inputs=["ref.fa", "input.fastq"],
                outputs=["aligned.sam"],
            ),
        ],
    )

    # Round-trip JSON serialization
    json_data = original_bundle.model_dump_json()
    deserialized_bundle = WorkflowBundle.model_validate_json(json_data)

    # Check equality of the entire bundle
    assert deserialized_bundle == original_bundle


def test_optional_fields_remain_optional():
    """Verify that optional fields are not required and default to None / empty structures."""
    # Test Metadata optional fields
    meta = WorkflowMetadata(name="test")
    assert meta.version is None
    assert meta.author is None
    assert meta.description is None

    # Test TaskResources optional fields
    resources = TaskResources()
    assert resources.cpus is None
    assert resources.memory is None
    assert resources.container is None

    # Test Task optional fields
    task = Task(id="T1", name="Task 1")
    assert task.command is None
    assert task.resources == TaskResources()
    assert task.inputs == []
    assert task.outputs == []
