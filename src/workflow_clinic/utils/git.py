"""Git repository handling utilities for remote workflow scanning."""

import subprocess
from pathlib import Path
from urllib.parse import urlparse

from workflow_clinic.exceptions import ParserError


def is_remote_url(target: str) -> bool:
    """Check if the given string represents a remote Git or HTTP(S) URL.

    Args:
        target: File path or URL string to inspect

    Returns:
        True if target is a remote repository URL, False otherwise.
    """
    target_clean = target.strip()

    # Standard remote URL schemes
    if target_clean.startswith(("http://", "https://", "git://", "git@")):
        return True

    # If a local file or directory exists at this path, it is NOT a remote URL
    if Path(target_clean).exists():
        return False

    # Check for .git suffix with URL-like patterns (e.g., contains :// or scp host:repo.git)
    if target_clean.endswith(".git"):
        if "://" in target_clean:
            return True
        if ":" in target_clean and "/" in target_clean and "\\" not in target_clean:
            return True

    parsed = urlparse(target_clean)
    return bool(parsed.scheme in ("http", "https", "git") and parsed.netloc)


def clone_remote_repo(url: str, destination: Path) -> Path:
    """Perform a shallow clone of a remote Git repository into destination path.

    Args:
        url: Remote Git repository URL
        destination: Local directory path to clone into

    Returns:
        Path to the cloned repository directory.

    Raises:
        ParserError: If git command fails or is not installed.
    """
    cmd = ["git", "clone", "--depth", "1", "--single-branch", url, str(destination)]
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            err_details = result.stderr.strip() or result.stdout.strip()
            msg = f"Failed to clone remote repository from '{url}': {err_details}"
            raise ParserError(msg)
    except FileNotFoundError as e:
        missing_msg = "Git executable not found in PATH. Please install git to scan remote repositories."
        raise ParserError(missing_msg) from e

    return destination
