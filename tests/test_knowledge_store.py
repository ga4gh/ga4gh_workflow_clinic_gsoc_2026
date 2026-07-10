"""Unit tests for the Rule Knowledge Store (retriever)."""

from pathlib import Path

import pytest

from workflow_clinic.advisor.retriever import RAGRetriever


@pytest.fixture
def temp_kb_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with a mock knowledge base TOML file."""
    kb_path = tmp_path / "mock_kb"
    kb_path.mkdir()

    toml_file = kb_path / "rules_knowledge.toml"
    toml_file.write_text(
        """
[W001]
title = "Mock Container Rule"

[[W001.sections]]
title = "Mock Container Versioning"
content = "Always pin container tags."

[[W001.sections]]
title = "Mock Digest Pinning"
content = "Digest pinning is superior."

[W002]
title = "Mock Resource Rule"

[[W002.sections]]
title = "Mock Resource Declarations"
content = "Declare memory and cpus."

[global]
title = "Mock Global Best Practices"

[[global.sections]]
title = "Mock Global Performance"
content = "Performance is important."
""",
        encoding="utf-8",
    )

    return kb_path


def test_retriever_filtering_correctness(temp_kb_dir: Path) -> None:
    """Verify retrieve filters and returns relevant rule IDs without global context bloat."""
    retriever = RAGRetriever(kb_dir=temp_kb_dir)

    # 1. Query W001: should return only W001 sections, and NEVER W002 or global sections
    results_w001 = retriever.retrieve(finding_id="W001")
    assert len(results_w001) == 2  # exactly 2 W001 sections

    assert any("Mock Container Versioning" in doc for doc in results_w001)
    assert any("Mock Digest Pinning" in doc for doc in results_w001)
    assert not any("Mock Global Performance" in doc for doc in results_w001)
    assert not any("Mock Resource Declarations" in doc for doc in results_w001)

    # 2. Query W002: should return only W002 sections, and NEVER W001 or global sections
    results_w002 = retriever.retrieve(finding_id="W002")
    assert len(results_w002) == 1  # exactly 1 W002 section

    assert any("Mock Resource Declarations" in doc for doc in results_w002)
    assert not any("Mock Global Performance" in doc for doc in results_w002)
    assert not any("Mock Container Versioning" in doc for doc in results_w002)


def test_retriever_unknown_finding_id(temp_kb_dir: Path) -> None:
    """Verify that querying an unknown rule ID falls back to returning global guidance."""
    retriever = RAGRetriever(kb_dir=temp_kb_dir)

    results = retriever.retrieve(finding_id="W999")
    assert len(results) == 1  # falls back to only global section
    assert "Mock Global Performance" in results[0]


def test_retriever_malformed_toml(tmp_path: Path) -> None:
    """Verify that a malformed rules TOML does not crash the retriever, but fails gracefully."""
    kb_path = tmp_path / "bad_kb"
    kb_path.mkdir()

    # Write invalid TOML syntax
    toml_file = kb_path / "rules_knowledge.toml"
    toml_file.write_text("invalid = [toml : syntax }", encoding="utf-8")

    retriever = RAGRetriever(kb_dir=kb_path)
    assert retriever._rules_data == {}

    # Querying should yield empty results gracefully instead of crashing
    results = retriever.retrieve(finding_id="W001")
    assert results == []
