from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import chromadb

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

# Pattern to match rule tag comments, e.g. <!-- rule: W001 --> or <!-- rule: global -->
RULE_MARKER_PATTERN = re.compile(r"<!--\s*rule:\s*(.+?)\s*-->")


def chunk_markdown_file(file_path: Path) -> list[dict]:
    """Parse a markdown file and split it into sections by rule markers."""
    text = file_path.read_text(encoding="utf-8")
    parts = RULE_MARKER_PATTERN.split(text)

    # parts[0] is any text before the first marker. Treat it as a global chunk
    # so introductory content is preserved and retrievable.
    chunks = []
    leading_text = parts[0].strip()
    if leading_text:
        chunks.append({"rules": ["global"], "content": leading_text, "index": 0})

    num_markers = len(parts) // 2

    for i in range(num_markers):
        rule_str = parts[2 * i + 1]
        content = parts[2 * i + 2]

        rules = [r.strip() for r in rule_str.split(",") if r.strip()]
        cleaned_content = content.strip()

        if cleaned_content:
            chunks.append(
                {
                    "rules": rules,
                    "content": cleaned_content,
                    "index": i,
                }
            )

    return chunks


class RAGRetriever:
    """Offline retriever that searches a local ChromaDB index populated with workflow best practices."""

    def __init__(
        self, persist_dir: Path | None = None, kb_dir: Path | None = None
    ) -> None:
        """Initialize the ChromaDB persistent client and retrieve/index documents."""
        if persist_dir is None:
            persist_dir = Path.home() / ".workflow_clinic" / "chromadb"

        # Ensure directory exists
        persist_dir.mkdir(parents=True, exist_ok=True)
        self.persist_dir = persist_dir
        self.kb_dir = kb_dir

        logger.info("Initializing persistent ChromaDB client at: %s", persist_dir)
        self.client = chromadb.PersistentClient(path=str(persist_dir))

        # Get or create the standard KB collection
        self.collection = self.client.get_or_create_collection(
            name="workflow_clinic_kb"
        )

        # Populate database if empty
        self._ensure_indexed()

    def _ensure_indexed(self) -> None:
        """Scan the local kb/ directory and index files if the collection is empty."""
        count = self.collection.count()
        if count > 0:
            logger.info(
                "ChromaDB collection already contains %d documents. Skipping indexing.",
                count,
            )
            return

        # Find the embedded KB files directory
        if self.kb_dir is not None:
            kb_dir = self.kb_dir
        else:
            kb_dir = Path(__file__).parent.parent / "kb"

        if not kb_dir.exists():
            logger.warning("Knowledge base directory not found at: %s", kb_dir)
            return

        logger.info("Indexing knowledge base documents from: %s", kb_dir)

        docs: list[str] = []
        metadatas: list[Mapping[str, Any]] = []
        ids: list[str] = []

        for file_path in kb_dir.glob("*.md"):
            file_basename = file_path.stem
            chunks = chunk_markdown_file(file_path)

            for chunk in chunks:
                content = chunk["content"]
                index = chunk["index"]
                rules = chunk["rules"]

                # Duplicate the chunk per rule tag to make metadata indexing simpler
                for rule in rules:
                    docs.append(content)
                    metadatas.append({"rules": rule})
                    # Generate unique ID structured as file_basename-index-rule
                    ids.append(f"{file_basename}-{index}-{rule}")

        if docs:
            logger.info("Adding %d documents to local ChromaDB index...", len(docs))
            self.collection.add(
                documents=docs,
                metadatas=metadatas,
                ids=ids,
            )
            logger.info("Indexing completed successfully.")
        else:
            logger.warning("No documents found in knowledge base to index.")

    def retrieve(self, finding_id: str, query: str, n_results: int = 3) -> list[str]:
        """Query the local collection for document chunks matching the finding_id or 'global'.

        Args:
            finding_id: The unique ID of the target rule finding (e.g. W001).
            query: The search text query.
            n_results: The number of context chunks to return (default 3 is chosen
                       to give a balanced context size in Critic prompt).
        """
        logger.info(
            "Retrieving recommendations for finding '%s' with query '%s'",
            finding_id,
            query,
        )

        # Filter: documents must have a "rules" metadata field matching either the finding_id or "global"
        where_filter: dict[str, Any] = {"rules": {"$in": [finding_id, "global"]}}

        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where_filter,
            )
        except Exception:
            logger.exception("Failed to query ChromaDB collection")
            return []

        docs_list = results.get("documents")
        if not docs_list:
            return []

        # Return the top matching documents for our single query text
        return docs_list[0]
