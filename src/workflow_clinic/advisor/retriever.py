"""Rule knowledge base store for Workflow Clinic.

Loads the curated rule knowledge base from TOML and maps rule IDs
to exact recommendations, titles, explanations, and remediations.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)


class RAGRetriever:
    """Retriever that looks up workflow best practices and standards from a local TOML file.

    Maintains the RAGRetriever name for backward compatibility with the GSoC proposal and CLI.
    """

    def __init__(self, kb_dir: Path | None = None) -> None:
        """Initialize the retriever and load the rule knowledge data from TOML."""
        if kb_dir is not None:
            self.kb_dir = kb_dir
        else:
            self.kb_dir = Path(__file__).parent.parent / "kb"

        self.kb_path = self.kb_dir / "rules_knowledge.toml"
        self._rules_data: dict = {}
        self._load_knowledge_base()

    def _load_knowledge_base(self) -> None:
        """Load and parse the rules TOML file."""
        if not self.kb_path.exists():
            logger.warning("Knowledge base TOML file not found at: %s", self.kb_path)
            return

        try:
            content = self.kb_path.read_text(encoding="utf-8")
            self._rules_data = tomllib.loads(content)
            logger.info(
                "Successfully loaded rules knowledge base from %s", self.kb_path
            )
        except (tomllib.TOMLDecodeError, OSError):
            logger.exception(
                "Failed to parse or read knowledge base TOML file: %s",
                self.kb_path,
            )

    def retrieve(
        self, finding_id: str, query: str | None = None, n_results: int = 3
    ) -> list[str]:
        """Retrieve exact matching sections for the finding_id, or fall back to global guidelines.

        Args:
            finding_id: The unique ID of the target rule finding (e.g. W001).
            query: Unused parameter kept for API signature compatibility.
            n_results: Unused parameter kept for API signature compatibility.

        Returns:
            A list of markdown strings representing rule guidance.
        """
        _ = query
        _ = n_results

        logger.info("Retrieving recommendations for finding '%s'", finding_id)
        results: list[str] = []

        # 1. Fetch rule-specific sections if they exist in the rules data
        if finding_id in self._rules_data:
            rule_entry = self._rules_data[finding_id]
            if isinstance(rule_entry, dict) and "sections" in rule_entry:
                for section in rule_entry["sections"]:
                    title = section.get("title", "")
                    content = section.get("content", "")
                    results.append(f"## {title}\n\n{content.strip()}")
        # 2. Otherwise fall back to global sections
        elif "global" in self._rules_data:
            global_entry = self._rules_data["global"]
            if isinstance(global_entry, dict) and "sections" in global_entry:
                for section in global_entry["sections"]:
                    title = section.get("title", "")
                    content = section.get("content", "")
                    results.append(f"## {title}\n\n{content.strip()}")

        return results
