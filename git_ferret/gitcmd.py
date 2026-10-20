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

# Exit codes this module synthesises for a git that never ran. 124 and 127 are
# the shell's own conventions for a timeout and a command that is not found, so
# they cannot collide with a real git exit code (git uses 128 for fatal).
TIMEOUT_EXIT = 124
NOT_FOUND_EXIT = 127

# Re-exported so callers can name this return type without importing
# ``subprocess`` outside this module.
CompletedProcess = subprocess.CompletedProcess[str]


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

    A git that cannot be executed at all -- not installed, or past the timeout
    -- raises :class:`GitError` like any other git failure, so callers have one
    error type to handle instead of two plus the OSError that a missing binary
    would otherwise leak.

    Output is decoded as UTF-8 explicitly. git stores refnames and commit text
    as bytes and emits them untouched; the process locale is cp1252 on a default
    Windows console, where a branch with an accented name would otherwise kill
    the run with a UnicodeDecodeError. ``errors="replace"`` degrades a name that
    is not valid UTF-8 to U+FFFD instead of failing the whole repository.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise GitError(list(args), TIMEOUT_EXIT, f"timed out after {TIMEOUT_SECONDS}s") from None
    except OSError as exc:
        raise GitError(list(args), NOT_FOUND_EXIT, str(exc)) from None
    if check and proc.returncode != 0:
        raise GitError(list(args), proc.returncode, proc.stderr)
    return proc


def run_git_text(repo: Path, *args: str) -> str:
    """Run git, raising :class:`GitError` on failure, and return stdout stripped."""
    return run_git(repo, *args, check=True).stdout.strip()
