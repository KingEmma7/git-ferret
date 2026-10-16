"""The merge test: whether a branch's changes are already in the base branch.

This is deliberately *not* decided by commit history. Ancestry-based checks are
wrong in both directions: a squash-merged or rebase-merged branch is not an
ancestor of main and is reported "not merged" when it is fully merged, while a
branch merged and later reverted stays an ancestor and is reported "merged".

The test instead asks the behavioural question directly, by performing a real
three-way merge in memory and comparing the resulting tree to the base tree.
``git merge-tree --write-tree`` does this without touching a worktree, an
index, or a ref.

Known limitation: a branch merged and then reverted still reports MERGED, since
the revert does not remove ancestry and the merge genuinely is a no-op. This is
accepted rather than worked around; the report states the reason inline so the
user can judge it.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from git_wombat.gitcmd import run_git, run_git_text
from git_wombat.gitops import is_shallow, tree_hash
from git_wombat.models import CONFLICT_EXIT, MERGED_EXIT

# git exits 128 when it refuses to merge unrelated histories, which means there
# is no merge base at all. That is "cannot determine", not "conflicting".
UNRELATED_EXIT = 128


class MergeVerdict(StrEnum):
    """The outcome of applying *branch* to *base_ref*."""

    MERGED = "MERGED"
    NOT_MERGED = "NOT_MERGED"
    CONFLICTS = "CONFLICTS"
    UNKNOWN = "UNKNOWN"


def merge_verdict(repo: Path, base_ref: str, branch_ref: str) -> MergeVerdict:
    """Whether applying *branch_ref* to *base_ref* would change anything.

    The three failure modes are distinguished by exit code rather than by
    parsing prose: 0 is a clean merge, 1 lists conflicting hunks, and 128 is a
    refusal with no merge base. Collapsing every non-zero into "conflicts"
    would confidently mislabel an unrelated-history repository.
    """
    if is_shallow(repo):
        # The merge base may be absent, and merge-tree then fails in a way that
        # resembles a conflict without being one.
        return MergeVerdict.UNKNOWN

    proc = run_git(repo, "merge-tree", "--write-tree", base_ref, branch_ref)
    if proc.returncode == MERGED_EXIT:
        result_tree = proc.stdout.strip().splitlines()[0].strip()
        base_tree = tree_hash(repo, base_ref)
        if base_tree is None:
            return MergeVerdict.UNKNOWN
        return (
            MergeVerdict.MERGED
            if result_tree == base_tree
            else MergeVerdict.NOT_MERGED
        )
    if proc.returncode == CONFLICT_EXIT:
        return MergeVerdict.CONFLICTS
    return MergeVerdict.UNKNOWN


def remote_branches(repo: Path) -> list[str]:
    """Every remote-tracking branch ref, across all remotes.

    Full ref names are used deliberately: ``%(refname:short)`` renders
    ``refs/remotes/origin/HEAD`` as bare ``origin``, which is indistinguishable
    from a remote named "origin" and would appear as a phantom branch.

    The ``HEAD`` symref is dropped: it points at the default branch rather than
    being a branch of its own.
    """
    output = run_git_text(repo, "for-each-ref", "--format=%(refname)",
                          "refs/remotes/")
    refs = []
    for line in output.splitlines():
        ref = line.strip()
        if not ref or ref.endswith("/HEAD"):
            continue
        refs.append(ref.removeprefix("refs/remotes/"))
    return refs


def commits_in_range(repo: Path, base_ref: str, branch_ref: str) -> list[str]:
    """``Name <email>`` for commits in ``base_ref..branch_ref``.

    The range is essential. Scanning full reachability would match nearly every
    branch in the repository, because the user's own commits live in the base
    branch's history and are therefore reachable from all of them.
    """
    output = run_git_text(repo, "log", "--format=%an <%ae>",
                          f"{base_ref}..{branch_ref}")
    return [line.strip() for line in output.splitlines() if line.strip()]


def branch_name_from_ref(ref: str, base_ref: str | None = None) -> str:
    """``origin/feature/x`` -> ``feature/x``."""
    return ref.split("/", 1)[1] if "/" in ref else ref
