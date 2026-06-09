from workflow_clinic.models import Task, WorkflowBundle, WorkflowMetadata


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
