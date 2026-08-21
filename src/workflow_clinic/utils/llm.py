"""Shared utilities for LLM provider API key checking, auto-detection, and model resolution."""

from __future__ import annotations

import os

_PROVIDER_ENV_KEYS: dict[str, list[str]] = {
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "google": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "mistral": ["MISTRAL_API_KEY"],
    "cohere": ["COHERE_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "azure": ["AZURE_API_KEY", "AZURE_OPENAI_API_KEY"],
    "bedrock": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
}

PROVIDER_MODEL_MAP: list[tuple[str, str]] = [
    ("GEMINI_API_KEY", "gemini/gemini-3.6-flash"),
    ("OPENAI_API_KEY", "gpt-4o-mini"),
    ("ANTHROPIC_API_KEY", "claude-3-5-sonnet-20240620"),
    ("MISTRAL_API_KEY", "mistral/mistral-large-latest"),
    ("GROQ_API_KEY", "groq/llama-3.1-8b-instant"),
    ("COHERE_API_KEY", "cohere/command-r"),
]


def resolve_model(explicit_model: str | None = None, api_key: str | None = None) -> str:
    """Resolve the LiteLLM model using explicit model, env vars, or auto-detection from available API keys."""
    if explicit_model:
        return explicit_model
    if clinic_model := os.getenv("CLINIC_MODEL"):
        return clinic_model
    for env_var, model_name in PROVIDER_MODEL_MAP:
        if os.getenv(env_var):
            return model_name
    if api_key:
        return "gemini/gemini-3.6-flash"
    return "gemini/gemini-3.6-flash"


def check_model_api_key(
    model_name: str | None, explicit_key: str | None = None
) -> bool:
    """Validate if an API key exists specifically for the requested model provider.

    Args:
        model_name: Model identifier (e.g. 'gemini/gemini-3.6-flash', 'anthropic/claude-3', 'gpt-4o').
        explicit_key: Explicitly provided API key from caller.

    Returns:
        True if an API key is available for the given model/provider, False otherwise.
    """
    if explicit_key:
        return True
    if not model_name:
        return False

    clean_model = model_name.strip().lower()
    if "/" in clean_model:
        provider = clean_model.split("/", 1)[0]
    elif clean_model.startswith(("gpt-", "o1", "o3", "text-embedding-", "dall-e")):
        provider = "openai"
    elif clean_model.startswith(("claude-", "anthropic")):
        provider = "anthropic"
    elif clean_model.startswith("gemini"):
        provider = "gemini"
    elif clean_model.startswith("mistral"):
        provider = "mistral"
    elif clean_model.startswith("command"):
        provider = "cohere"
    else:
        provider = clean_model

    if provider in _PROVIDER_ENV_KEYS:
        expected_keys = _PROVIDER_ENV_KEYS[provider]
        return any(bool(os.getenv(k)) for k in expected_keys)

    all_keys = [
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MISTRAL_API_KEY",
        "COHERE_API_KEY",
        "GROQ_API_KEY",
    ]
    return any(bool(os.getenv(k)) for k in all_keys)
