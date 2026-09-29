"""UI module for Workflow Clinic providing formatting, interactive prompts, and validators."""

from workflow_clinic.ui.console import console, err_console
from workflow_clinic.ui.formatters import (
    display_enhance_status,
    display_examine_results,
    display_fix_results,
    display_models_table,
    display_publish_results,
)
from workflow_clinic.ui.prompts import (
    parse_selection,
    prompt_category_selection,
    prompt_issue_selection,
)
from workflow_clinic.ui.validators import verify_github_repo

__all__ = [
    "console",
    "display_enhance_status",
    "display_examine_results",
    "display_fix_results",
    "display_models_table",
    "display_publish_results",
    "err_console",
    "parse_selection",
    "prompt_category_selection",
    "prompt_issue_selection",
    "verify_github_repo",
]
