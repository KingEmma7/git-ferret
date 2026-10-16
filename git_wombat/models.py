"""Shared data shapes for analysis and reporting.

Every analysis task produces these, and the report task renders them, so the
names and fields here are the contract between the two halves of the program.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

# `git merge-tree --write-tree` exit codes, verified empirically. They separate
# the three failure modes without heuristics: 0 is a clean merge, 1 lists
# conflicting hunks, 128 is a refusal with no merge base.
MERGED_EXIT = 0
CONFLICT_EXIT = 1


class Status(StrEnum):
    """A single finding's state, rendered as a capitalised bracketed marker."""

    OK = "OK"
    NOT_PUSHED = "NOT_PUSHED"
    NOT_MERGED = "NOT_MERGED"
    DIVERGED = "DIVERGED"
    UPSTREAM_DELETED = "UPSTREAM_DELETED"
    CONFLICTS = "CONFLICTS"
    UNKNOWN = "UNKNOWN"
    DIRTY = "DIRTY"
    DETACHED = "DETACHED"
    STASH = "STASH"
    UNREACHABLE = "UNREACHABLE"
    NO_BASE = "NO_BASE"

    @property
    def MARKER(self) -> str:
        """The bracketed text that carries the meaning.

        Emoji are decorative; this is the part that must survive a font-less
        terminal, a pipe, and a copy-paste into a ticket.
        """
        return f"[{self.value.replace('_', ' ')}]"


@dataclass(frozen=True)
class Finding:
    """One line of the report about one branch or worktree.

    ``branch`` holds the display ref: a bare name for local analysis, an
    ``origin/x``-style ref for remote analysis.
    """

    branch: str
    status: Status
    detail: str = ""
    emoji: str = ""
    safe_to_delete: bool = False


@dataclass
class BranchInfo:
    """A local branch and its relationship to its upstream."""

    name: str
    remote: str | None = None
    upstream: str | None = None
    upstream_gone: bool = False
    ahead: int = 0
    behind: int = 0
    has_upstream: bool = False
    last_commit_rel: str = ""


@dataclass
class WorktreeInfo:
    """One worktree of a repository, primary or linked."""

    path: Path
    branch: str | None
    is_bare: bool = False
    dirty: bool = False
    dirty_detail: str = ""


@dataclass
class RepoReport:
    """Everything the report needs to say about one repository."""

    path: Path
    base_ref: str | None = None
    fetched_at: datetime | None = None
    fetch_failed: bool = False
    fetch_attempted: bool = True
    skipped_reason: str | None = None
    worktrees: list[WorktreeInfo] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def is_stale(self) -> bool:
        """Whether this report rests on state this run did not refresh.

        True whenever no fetch was attempted (``--no-fetch``) or the attempt
        failed. A repository that was never fetched is a different case from one
        fetched seconds ago, and both differ from one holding month-old refs.
        """
        return not self.fetch_attempted or self.fetch_failed or self.fetched_at is None
