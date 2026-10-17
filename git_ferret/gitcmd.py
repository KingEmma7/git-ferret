"""The one seam between git-ferret and the git binary.

Every git invocation in the project goes through this module, and no other
module imports ``subprocess``. Concentrating the calls in a single file is what
makes the report-only safety property auditable: the whole of what git-ferret
can do to a repository is the argv assembled by :func:`run_git`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

TIMEOUT_SECONDS = 120

# Re-exported so callers can name this return type without importing
# ``subprocess`` outside this module.
CompletedProcess = subprocess.CompletedProcess


class GitError(Exception):
    """A git command exited non-zero. Carries the code and stderr for reporting."""

    def __init__(self, args: list[str], returncode: int, stderr: str):
        super().__init__(f"git {' '.join(args)} failed ({returncode}): {stderr}")
        self.returncode = returncode
        self.stderr = stderr


def run_git(repo: Path, *args: str, check: bool = False) -> CompletedProcess:
    """Run ``git`` in *repo* with captured text output.

    ``-C`` is always passed rather than setting ``cwd``, so no caller can
    accidentally depend on this process's working directory. With the default
    ``check=False`` a non-zero exit is returned rather than raised, which lets
    callers ask git questions whose answer *is* the exit code.
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    if check and proc.returncode != 0:
        raise GitError(list(args), proc.returncode, proc.stderr)
    return proc


def run_git_text(repo: Path, *args: str) -> str:
    """Run git, raising :class:`GitError` on failure, and return stdout stripped."""
    return run_git(repo, *args, check=True).stdout.strip()
