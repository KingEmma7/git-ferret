"""The command line interface.

Three subcommands, each independently runnable. All default to fetching and all
accept ``--no-fetch``, so the intended rhythm is one fetching run followed by
cheap repeat runs against local state.

Report-only: the sole write this tool performs is ``git merge --ff-only``
behind ``local --update``, which is off unless asked for.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from git_wombat.analysis.local import analyse_local
from git_wombat.analysis.remote import (
    analyse_merged_candidates,
    analyse_remote,
)
from git_wombat.config import Config, resolve_identities
from git_wombat.discovery import discover_repos, list_worktrees
from git_wombat.gitcmd import GitError, run_git
from git_wombat.gitops import fetch_all, resolve_base_ref
from git_wombat.models import RepoReport
from git_wombat.report import emit, render_report

app = typer.Typer(help="Audit many local git repositories at once. Reports only.")


@app.command()
def local(
    path: Path = typer.Argument(Path("."), help="Directory to scan recursively."),
    no_fetch: bool = typer.Option(False, "--no-fetch",
                                   help="Skip fetching; use existing local state."),
    update: bool = typer.Option(
        False, "--update",
        help="Fast-forward clean worktrees. This is the only write this tool performs.",
    ),
) -> None:
    """Branches checked out locally, worktrees, dirty state."""
    config = Config.load()
    root = path.expanduser().resolve()
    reports: list[RepoReport] = []
    updated: list[str] = []

    for repo in discover_repos(root):
        base_ref = resolve_base_ref(repo)
        try:
            report = analyse_local(repo, config, base_ref, not no_fetch)
        except GitError as exc:
            print(f"error analysing {repo}: {exc}", file=sys.stderr)
            continue
        reports.append(report)
        print(f"analysed {repo}", file=sys.stderr)
        if update:
            updated.extend(fast_forward_worktrees(repo))

    stale = sum(1 for r in reports if r.fetched_at is None or r.fetch_failed)
    text = render_report(reports, stale)
    if updated:
        text += "\n## fast-forwarded\n\n" + "".join(f"- {line}\n" for line in updated)
    emit(text)


def fast_forward_worktrees(repo: Path) -> list[str]:
    """``git merge --ff-only`` in each clean, attached worktree.

    Skips are reported rather than passed over silently, because a worktree
    holding an unfinished refactor is exactly the kind of thing a repo-hygiene
    tool should surface rather than hide.
    """
    results: list[str] = []
    for worktree, branch in list_worktrees(repo):
        if branch is None:
            results.append(f"{worktree} — skipped: detached HEAD")
            continue
        dirty = run_git(worktree, "status", "--porcelain").stdout.strip()
        if dirty:
            results.append(f"`{branch}` — skipped: uncommitted changes")
            continue
        has_upstream = run_git(worktree, "rev-parse", "--verify", "--quiet",
                               "@{upstream}").returncode == 0
        if not has_upstream:
            results.append(f"`{branch}` — skipped: no upstream configured")
            continue
        proc = run_git(worktree, "merge", "--ff-only")
        if proc.returncode == 0:
            results.append(f"`{branch}` — fast-forwarded")
        else:
            results.append(f"`{branch}` — skipped: not a fast-forward")
    return results


@app.command()
def remote(
    path: Path = typer.Argument(Path(".")),
    no_fetch: bool = typer.Option(False, "--no-fetch"),
    author: list[str] = typer.Option([], "--author",
                                      help="Substring of 'Name <email>'. Repeatable."),
    branch: str | None = typer.Option(None, "--branch",
                                      help="Substring of the branch name."),
) -> None:
    """Remote branches matching the filters."""
    _run(lambda repo, cfg, base, ids, bfilter, fetch: analyse_remote(
        repo, cfg, base, ids, bfilter, fetch), path, no_fetch, author, branch)


@app.command()
def merged(
    path: Path = typer.Argument(Path(".")),
    no_fetch: bool = typer.Option(False, "--no-fetch"),
    author: list[str] = typer.Option([], "--author"),
    branch: str | None = typer.Option(None, "--branch"),
) -> None:
    """Remote branches whose changes are already in the base branch."""
    _run(lambda repo, cfg, base, ids, bfilter, fetch: analyse_merged_candidates(
        repo, cfg, base, ids, bfilter, fetch), path, no_fetch, author, branch)


def build_parser() -> typer.Typer:
    """The configured app, exposed so tests can drive it."""
    return app


def _run(analysis, path: Path, no_fetch: bool, author: list[str] | None,
         branch_filter: str | None) -> None:
    config = Config.load()
    root = path.expanduser().resolve()
    reports: list[RepoReport] = []

    for repo in discover_repos(root):
        base_ref = resolve_base_ref(repo)
        identities = resolve_identities(author or [], config, repo)
        try:
            report = analysis(repo, config, base_ref, identities, branch_filter,
                              not no_fetch)
        except GitError as exc:
            print(f"error analysing {repo}: {exc}", file=sys.stderr)
            continue
        reports.append(report)
        print(f"analysed {repo}", file=sys.stderr)

    stale = sum(1 for r in reports if r.fetched_at is None or r.fetch_failed)
    emit(render_report(reports, stale))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
