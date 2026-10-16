"""Remote branch enumeration, filtering, and the safe-to-delete verdict.

Two subcommands share this enumeration. ``remote`` answers "which remote
branches match X" and does not judge deletion safety at all; ``merged`` adds
the merge verdict and the gate that keeps a live branch out of the deletable
set.
"""

from __future__ import annotations

from pathlib import Path

from git_wombat.analysis.local import (
    EMOJI_ERROR,
    EMOJI_OK,
    EMOJI_WARN,
    NO_BASE_REASON,
)
from git_wombat.analysis.merged import (
    MergeVerdict,
    branch_name_from_ref,
    commits_in_range,
    merge_verdict,
    remote_branches,
)
from git_wombat.config import Config, identity_matches
from git_wombat.gitcmd import run_git
from git_wombat.gitops import fetch_all, fetch_head_mtime, list_branches
from git_wombat.models import Finding, RepoReport, Status

_STATUS_FOR_VERDICT = {
    MergeVerdict.MERGED: Status.OK,
    MergeVerdict.NOT_MERGED: Status.NOT_PUSHED,
    MergeVerdict.CONFLICTS: Status.CONFLICTS,
    MergeVerdict.UNKNOWN: Status.UNKNOWN,
}


def local_branch_unpushed(repo: Path, branch: str) -> bool:
    """Whether a local branch of this name holds work the remote does not.

    This is the gate that keeps the tool from recommending deletion of a remote
    branch while the user's local copy of the same name carries commits that
    exist nowhere else.
    """
    if run_git(repo, "rev-parse", "--verify", "--quiet",
               f"refs/heads/{branch}").returncode != 0:
        return False
    info = {b.name: b for b in list_branches(repo)}.get(branch)
    if info is None:
        return False
    return info.ahead > 0 or info.upstream_gone


def _candidate_refs(
    repo: Path, config: Config, base_ref: str | None
) -> list[str]:
    """Remote branch refs worth analysing, minus base, main, master, whitelist."""
    candidates = []
    for ref in remote_branches(repo):
        name = branch_name_from_ref(ref)
        if name in ("main", "master"):
            continue
        if base_ref is not None and ref in (base_ref, branch_name_from_ref(base_ref)):
            continue
        # For remote scans the whitelist is absolute: ignored entirely.
        if config.is_whitelisted(name):
            continue
        candidates.append(ref)
    return candidates


def _passes_filters(
    repo: Path,
    ref: str,
    base_ref: str,
    identities: list[str],
    branch_filter: str | None,
) -> bool:
    """Apply the optional author and name filters, combined with AND."""
    if branch_filter and branch_filter not in ref:
        return False
    if identities:
        # The range matters: the user's commits sit in the base history and are
        # therefore reachable from every branch.
        authors = commits_in_range(repo, base_ref, ref)
        if not identity_matches(authors, identities):
            return False
    return True


def _new_report(repo: Path, base_ref: str | None, do_fetch: bool) -> RepoReport:
    report = RepoReport(path=repo, base_ref=base_ref)
    if do_fetch:
        ok, when = fetch_all(repo)
        report.fetch_failed = not ok
        report.fetched_at = when
    else:
        report.fetched_at = fetch_head_mtime(repo)
    if base_ref is None:
        report.skipped_reason = NO_BASE_REASON
        report.findings.append(
            Finding(
                branch=repo.name,
                status=Status.NO_BASE,
                detail=NO_BASE_REASON,
                emoji=EMOJI_WARN,
            )
        )
    return report


def analyse_remote(
    repo: Path,
    config: Config,
    base_ref: str | None,
    identities: list[str],
    branch_filter: str | None,
    do_fetch: bool,
) -> RepoReport:
    """List remote branches matching the filters. No deletion verdict here."""
    report = _new_report(repo, base_ref, do_fetch)
    if base_ref is None:
        return report

    for ref in _candidate_refs(repo, config, base_ref):
        if not _passes_filters(repo, ref, base_ref, identities, branch_filter):
            continue
        report.findings.append(
            Finding(
                branch=ref,
                status=Status.OK,
                detail="remote branch",
                emoji=EMOJI_OK,
            )
        )
    return report


def analyse_merged_candidates(
    repo: Path,
    config: Config,
    base_ref: str | None,
    identities: list[str],
    branch_filter: str | None,
    do_fetch: bool,
) -> RepoReport:
    """Remote branches whose changes are already in the base branch.

    Merged-ness and deletability are separate claims. A branch can be genuinely
    merged and still be unsafe to delete because a same-named local branch
    holds unpushed work, so the annotation is withheld and the reason shown.
    """
    report = _new_report(repo, base_ref, do_fetch)
    if base_ref is None:
        return report

    for ref in _candidate_refs(repo, config, base_ref):
        if not _passes_filters(repo, ref, base_ref, identities, branch_filter):
            continue

        verdict = merge_verdict(repo, base_ref, ref)
        status = _STATUS_FOR_VERDICT[verdict]
        name = branch_name_from_ref(ref)

        if verdict is not MergeVerdict.MERGED:
            report.findings.append(
                Finding(
                    branch=ref,
                    status=status,
                    detail=_detail_for(verdict),
                    emoji=EMOJI_ERROR if verdict is MergeVerdict.CONFLICTS else EMOJI_WARN,
                )
            )
            continue

        if local_branch_unpushed(repo, name):
            report.findings.append(
                Finding(
                    branch=ref,
                    status=status,
                    detail=f"merged into {base_ref}, but local '{name}' has unpushed changes",
                    emoji=EMOJI_WARN,
                    safe_to_delete=False,
                )
            )
            continue

        report.findings.append(
            Finding(
                branch=ref,
                status=status,
                detail=f"safe to delete: merged into {base_ref}, no unpushed local changes",
                emoji=EMOJI_OK,
                safe_to_delete=True,
            )
        )
    return report


def _detail_for(verdict: MergeVerdict) -> str:
    if verdict is MergeVerdict.CONFLICTS:
        return "conflicts with base branch"
    if verdict is MergeVerdict.UNKNOWN:
        return "cannot determine (no merge base, or shallow clone)"
    return "not merged"
