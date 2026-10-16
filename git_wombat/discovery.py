"""Finding the repositories to audit, and the worktrees inside each one.

Discovery stops descending at the first repository it finds. A personal
collection routinely contains nested checkouts — a dotfiles repo with cloned
plugins, a vendored dependency — and walking into them would fetch and report
the same tree twice.
"""

from __future__ import annotations

import os
from pathlib import Path

from git_wombat.gitcmd import run_git_text


def is_git_dir(path: Path) -> bool:
    """Whether *path* is a repository root: ``.git`` as a dir or as a file."""
    dot_git = path / ".git"
    return dot_git.is_dir() or dot_git.is_file()


def is_submodule(path: Path) -> bool:
    """Whether *path* is a submodule rather than a repository of the user's.

    A submodule's ``.git`` is a file pointing into the superproject's
    ``.git/modules`` tree. Those are vendored content and get skipped.
    """
    dot_git = path / ".git"
    if not dot_git.is_file():
        return False
    try:
        content = dot_git.read_text().strip()
    except OSError:
        return False
    if not content.startswith("gitdir:"):
        return False
    target = content.split(":", 1)[1].strip()
    resolved = Path(target)
    if not resolved.is_absolute():
        resolved = (path / resolved).resolve()
    return ".git/modules/" in f"{resolved}/" or "/.git/modules" in f"{resolved}"


def is_bare(repo: Path) -> bool:
    """Whether *repo* is a bare repository, which this tool does not analyse."""
    return run_git_text(repo, "rev-parse", "--is-bare-repository") == "true"


def discover_repos(root: Path) -> list[Path]:
    """Every repository at or under *root*, sorted, without descending into one.

    Sorting keeps report output deterministic across runs, which is what makes
    two reports worth diffing.
    """
    if not root.is_dir():
        return []

    found: list[Path] = []
    for dirpath, dirnames, _ in os.walk(root):
        current = Path(dirpath)
        if is_git_dir(current) and not is_submodule(current):
            found.append(current)
            # Prune: everything below this repository is part of it.
            dirnames[:] = []
        else:
            # Never descend into a repository's internals.
            dirnames[:] = [d for d in dirnames if d != ".git"]
    return sorted(found)


def list_worktrees(repo: Path) -> list[tuple[Path, str | None]]:
    """``(path, branch)`` for every worktree; branch is ``None`` when detached.

    Parses ``git worktree list --porcelain``. A block starts with ``worktree``;
    it carries ``branch`` only when HEAD is attached, so an absent line is the
    signal for a detached worktree.
    """
    output = run_git_text(repo, "worktree", "list", "--porcelain")
    worktrees: list[tuple[Path, str | None]] = []
    path: Path | None = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree ") :])
            worktrees.append((path, None))
        elif line.startswith("branch refs/heads/") and worktrees:
            name = line[len("branch refs/heads/") :]
            worktrees[-1] = (worktrees[-1][0], name)
    return worktrees
