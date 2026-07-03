"""Rule registry for Workflow Clinic.

This module provides a central registry for discovering and retrieving
validation rules. Rules are registered at import time and looked up
by their unique ``id``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from workflow_clinic.rules.base import BaseRule


class RuleRegistry:
    """Central registry for validation rules.

    Rules are stored as classes (not instances) and instantiated
    on retrieval. This mirrors the ``ParserRegistry`` pattern.
    """

    _rules: ClassVar[dict[str, type[BaseRule]]] = {}

    @classmethod
    def register(cls, rule_class: type[BaseRule]) -> None:
        """Register a rule class by its ``id`` attribute.

        Args:
            rule_class: A concrete subclass of BaseRule.

        Raises:
            ValueError: If a rule with the same id is already registered.
        """
        rule_id = rule_class.id
        if rule_id in cls._rules:
            msg = f"Rule '{rule_id}' is already registered"
            raise ValueError(msg)
        cls._rules[rule_id] = rule_class

    @classmethod
    def get_rule(cls, rule_id: str) -> BaseRule:
        """Retrieve and instantiate a registered rule by id.

        Args:
            rule_id: The unique identifier of the rule.

        Returns:
            An instance of the requested rule.

        Raises:
            KeyError: If no rule with the given id is registered.
        """
        if rule_id not in cls._rules:
            msg = f"No rule registered with id '{rule_id}'"
            raise KeyError(msg)
        return cls._rules[rule_id]()

    @classmethod
    def get_all_rules(cls) -> list[BaseRule]:
        """Instantiate and return all registered rules.

        Returns:
            A list of rule instances, one per registered rule class.
        """
        return [rule_class() for rule_class in cls._rules.values()]

    @classmethod
    def list_rule_ids(cls) -> list[str]:
        """Return a list of all registered rule ids."""
        return list(cls._rules.keys())

    @classmethod
    def clear(cls) -> None:
        """Remove all registered rules. Intended for test isolation."""
        cls._rules.clear()
