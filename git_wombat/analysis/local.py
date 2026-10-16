"""Local branch and worktree state: what is checked out, and what is amiss."""

from __future__ import annotations

from pathlib import Path

from git_wombat.config import Config
from git_wombat.discovery import list_worktrees
from git_wombat.gitcmd import run_git_text
from git_wombat.gitops import fetch_all, list_branches
from git_wombat.models import BranchInfo, Finding, RepoReport, Status, WorktreeInfo

EMOJI_OK = "✓"
EMOJI_WARN = "⚠️"
EMOJI_ERROR = "❌"

_EMOJI_FOR = {
    Status.OK: EMOJI_OK,
    Status.DIRTY: EMOJI_WARN,
    Status.DETACHED: EMOJI_WARN,
    Status.NOT_PUSHED: EMOJI_WARN,
    Status.DIVERGED: EMOJI_WARN,
    Status.UPSTREAM_DELETED: EMOJI_WARN,
    Status.CONFLICTS: EMOJI_ERROR,
    Status.UNKNOWN: EMOJI_WARN,
    Status.UNREACHABLE: EMOJI_ERROR,
    Status.NO_BASE: EMOJI_WARN,
}

NO_BASE_REASON = "no base branch (neither origin/main, origin/master, main nor master)"


def branch_finding(branch: BranchInfo) -> Finding | None:
    """One finding for *branch*, or ``None`` when it should not be listed.

    Overlapping conditions collapse to a single primary status in precedence
    order, because a branch whose upstream was deleted is simultaneously ahead
    of an upstream that no longer exists; listing it three times is noise, and
    the primary label is the one that determines the action.
    """
    if branch.upstream_gone:
        return _finding(branch, Status.UPSTREAM_DELETED, "upstream deleted on remote")
    if branch.ahead > 0 and branch.behind > 0:
        return _finding(
            branch,
            Status.DIVERGED,
            f"{branch.ahead} ahead, {branch.behind} behind — cannot fast-forward",
        )
    if not branch.has_upstream:
        return _finding(branch, Status.NOT_PUSHED, "no upstream configured")
    if branch.ahead > 0:
        return _finding(branch, Status.NOT_PUSHED, f"{branch.ahead} commits ahead")
    return _finding(branch, Status.OK, "fully pushed")


def _finding(branch: BranchInfo, status: Status, detail: str) -> Finding:
    return Finding(
        branch=branch.name,
        status=status,
        detail=detail,
        emoji=_EMOJI_FOR[status],
    )


def worktree_status(worktree: Path) -> tuple[bool, str]:
    """``(dirty, detail)`` for one worktree.

    Untracked files count as dirty: they are unsaved work, and a report that
    called a worktree clean while untracked files sat in it would be wrong.
    """
    try:
        output = run_git_text(worktree, "status", "--porcelain")
    except Exception:
        return False, ""
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return False, ""
    untracked = sum(1 for line in lines if line.startswith("??"))
    modified = len(lines) - untracked
    parts = []
    if modified:
        parts.append(f"{modified} file{'s' if modified != 1 else ''} modified")
    if untracked:
        parts.append(f"{untracked} untracked")
    return True, ", ".join(parts)


def analyse_local(
    repo: Path, config: Config, base_ref: str | None, do_fetch: bool
) -> RepoReport:
    """Report on every worktree and every non-excluded local branch.

    With ``do_fetch`` false this never writes anything and never contacts a
    remote; it reports on whatever is already in ``.git``, stamping the age of
    the last successful fetch so a stale answer is visibly stale.
    """
    report = RepoReport(path=repo, base_ref=base_ref)

    if do_fetch:
        ok, when = fetch_all(repo)
        report.fetch_failed = not ok
        report.fetched_at = when
    else:
        from git_wombat.gitops import fetch_head_mtime

        report.fetched_at = fetch_head_mtime(repo)

    for path, branch_name in list_worktrees(repo):
        dirty, detail = worktree_status(path)
        report.worktrees.append(
            WorktreeInfo(path=path, branch=branch_name, dirty=dirty, dirty_detail=detail)
        )
        if branch_name is None:
            report.findings.append(
                Finding(
                    branch=str(path),
                    status=Status.DETACHED,
                    detail="detached HEAD",
                    emoji=_EMOJI_FOR[Status.DETACHED],
                )
            )
        if dirty:
            report.findings.append(
                Finding(
                    branch=branch_name or str(path),
                    status=Status.DIRTY,
                    detail=detail,
                    emoji=_EMOJI_FOR[Status.DIRTY],
                )
            )

    if base_ref is None:
        report.skipped_reason = NO_BASE_REASON
        report.findings.append(
            Finding(
                branch=repo.name,
                status=Status.NO_BASE,
                detail=NO_BASE_REASON,
                emoji=_EMOJI_FOR[Status.NO_BASE],
            )
        )

    for branch in list_branches(repo):
        if _always_excluded(branch.name, base_ref):
            # `main`, `master`, and the base ref are never listed as branches.
            continue
        if config.is_whitelisted(branch.name) and branch_status_is_clean(branch):
            # The whitelist suppresses "exists and is fine". A whitelisted
            # branch with a real problem still surfaces.
            continue
        finding = branch_finding(branch)
        if finding is not None:
            report.findings.append(finding)

    return report


def _always_excluded(name: str, base_ref: str | None) -> bool:
    """``main``, ``master``, and the resolved base ref: never listed at all."""
    if name in ("main", "master"):
        return True
    return base_ref is not None and name in (base_ref, base_ref.split("/")[-1])


def branch_status_is_clean(branch: BranchInfo) -> bool:
    """Whether *branch* has nothing worth reporting."""
    return not branch.upstream_gone and branch.ahead == 0 and branch.has_upstream
