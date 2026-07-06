"""Unit tests for the RAG Retriever module using ChromaDB."""

from pathlib import Path

import pytest

from workflow_clinic.advisor.retriever import RAGRetriever, chunk_markdown_file


@pytest.fixture
def temp_kb_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with mock knowledge base files."""
    kb_path = tmp_path / "mock_kb"
    kb_path.mkdir()

    # Create first KB file
    file1 = kb_path / "biocontainers.md"
    file1.write_text(
        "<!-- rule: W001 -->\n"
        "## Container Versioning\n"
        "Always pin your container tags or use digests.\n\n"
        "<!-- rule: W001, W002 -->\n"
        "## Multi-Rule Section\n"
        "This section is relevant for containers and resource limits.\n\n"
        "<!-- rule: global -->\n"
        "## Global Section\n"
        "This contains general bio-workflows context.\n",
        encoding="utf-8",
    )

    # Create second KB file
    file2 = kb_path / "resources.md"
    file2.write_text(
        "<!-- rule: W002 -->\n"
        "## Resource Allocations\n"
        "Do not hardcode memory and cpu variables.\n\n"
        "<!-- rule: global -->\n"
        "## Global Performance Tuning\n"
        "Tuning performance helps scale in cloud environments.\n",
        encoding="utf-8",
    )

    return kb_path


def test_markdown_chunking(temp_kb_dir: Path) -> None:
    """Verify that chunk_markdown_file splits markdown by rule comments correctly."""
    chunks = chunk_markdown_file(temp_kb_dir / "biocontainers.md")
    assert len(chunks) == 3

    assert chunks[0]["rules"] == ["W001"]
    assert "Always pin your container tags" in chunks[0]["content"]

    assert chunks[1]["rules"] == ["W001", "W002"]
    assert "This section is relevant for containers" in chunks[1]["content"]

    assert chunks[2]["rules"] == ["global"]
    assert "This contains general bio-workflows context." in chunks[2]["content"]


def test_retriever_indexing_runs_once(tmp_path: Path, temp_kb_dir: Path) -> None:
    """Verify retriever only indexes files when the database collection is empty."""
    persist_path = tmp_path / "chroma_db"

    # First initialization: should index everything
    retriever1 = RAGRetriever(persist_dir=persist_path, kb_dir=temp_kb_dir)
    initial_count = retriever1.collection.count()
    # W001 (1 doc) + W001 (1 doc) + W002 (1 doc) + global (1 doc) from biocontainers.md
    # plus W002 (1 doc) + global (1 doc) from resources.md = 6 documents total
    assert initial_count == 6

    # Second initialization: should skip indexing and maintain identical count
    retriever2 = RAGRetriever(persist_dir=persist_path, kb_dir=temp_kb_dir)
    assert retriever2.collection.count() == initial_count


def test_retriever_filtering_correctness(tmp_path: Path, temp_kb_dir: Path) -> None:
    """Verify that retrieve queries are filtered to only relevant rule IDs and global."""
    persist_path = tmp_path / "chroma_db"
    retriever = RAGRetriever(persist_dir=persist_path, kb_dir=temp_kb_dir)

    # 1. Query for W001: should get container rules or global rules, but NEVER only W002 rules
    results_w001 = retriever.retrieve(finding_id="W001", query="container versioning")
    assert len(results_w001) > 0
    # Make sure no pure W002 sections are returned
    for doc in results_w001:
        assert "Do not hardcode memory and cpu variables." not in doc

    # 2. Query for W002: should get resource allocations
    results_w002 = retriever.retrieve(finding_id="W002", query="memory resources")
    assert len(results_w002) > 0
    assert any(
        "Do not hardcode memory and cpu variables." in doc for doc in results_w002
    )


def test_retriever_multi_rule_duplication(tmp_path: Path, temp_kb_dir: Path) -> None:
    """Verify that multi-tagged sections are retrieved under either queried rule ID."""
    persist_path = tmp_path / "chroma_db"
    retriever = RAGRetriever(persist_dir=persist_path, kb_dir=temp_kb_dir)

    # Retrieve W001
    res_w001 = retriever.retrieve(
        finding_id="W001", query="Multi-Rule Section", n_results=1
    )
    assert len(res_w001) == 1
    assert "This section is relevant for containers" in res_w001[0]

    # Retrieve W002
    res_w002 = retriever.retrieve(
        finding_id="W002", query="Multi-Rule Section", n_results=1
    )
    assert len(res_w002) == 1
    assert "This section is relevant for containers" in res_w002[0]


def test_retriever_unknown_finding_id(tmp_path: Path, temp_kb_dir: Path) -> None:
    """Verify that querying an unknown rule ID still returns global-tagged context."""
    persist_path = tmp_path / "chroma_db"
    retriever = RAGRetriever(persist_dir=persist_path, kb_dir=temp_kb_dir)

    # W999 is unknown, should return global matches
    results = retriever.retrieve(finding_id="W999", query="general workflows")
    assert len(results) > 0
    # The top result should be global text
    assert any(
        "general bio-workflows context" in doc or "Performance Tuning" in doc
        for doc in results
    )
