"""Custom exceptions for Workflow Clinic.

This module defines the exception hierarchy for domain-specific error handling
thought the Workflow Clinic application. All exceptions inherit from the base
WorkflowClinicError class, enabling specific exception catching for different
error scenarios.

Exception Hierarchy:
    WorkflowClinicError (base) → Exception
        └── ParserError → WorkflowClinicError
            ├── InvalidWorkflowError → ParserError
            └── UnsupportedWorkflowError → ParserError
"""


class WorkflowClinicError(Exception):
    """Base exception for all Workflow Clinic errors.

    This is the root exception class that all domain-specific exceptions
    inherit from. It allows for catching all Workflow Clinic errors with
    a single except clause when needed.
    """


class ParserError(WorkflowClinicError):
    """Exception raised for general parser infrastructure errors.

    This exception is used for parser-related failures that don't fall into
    more specific categories, such as parser registration issues, path validation
    failures, or general parser infrastructure problems.
    """


class InvalidWorkflowError(ParserError):
    """Exception raised when workflow file content is malformed or invalid.

    This exception indicates that a workflow file exists and can be handled by
    a parser, but the content has syntax errors, validation failures, or other
    issues that prevent successful parsing. This is distinct from infrastructure
    errors and represents issues with the workflow file itself.
    """


class UnsupportedWorkflowError(ParserError):
    """Exception raised when no parser can handle the given workflow.

    This exception indicates that a workflow file or directory was provided,
    but no registered parser is capable of handling that workflow language or
    format. This typically means the workflow is written in an unsupported
    language or uses an unrecognized file structure.
    """
