"""The command line interface.

Three subcommands, each independently runnable. All default to fetching and all
accept ``--no-fetch``, so the intended rhythm is one fetching run followed by
cheap repeat runs against local state.

Report-only: the sole write this tool performs is ``git merge --ff-only``
behind ``local --update``, which is off unless asked for.
"""

from __future__ import annotations

import subprocess
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
from git_wombat.gitops import fetch_all, fetch_head_mtime, resolve_base_ref
from git_wombat.report import emit, render_report

app = typer.Typer(help="Audit many local git repositories at once. Reports only.")

ANALYSES = {
    "local": lambda repo, cfg, base, ids, bfilter, fetch: analyse_local(
        repo, cfg, base, fetch
    ),
    "remote": analyse_remote,
    "merged": analyse_merged_candidates,
}


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
    root = _resolve_root(path)
    updated: list[tuple[Path, str]] = []

    for repo in discover_repos(root):
        if not no_fetch:
            fetch_all(repo)
        base_ref = resolve_base_ref(repo)
        try:
            report = analyse_local(repo, config, base_ref, False)
        except (GitError, OSError, subprocess.SubprocessError) as exc:
            _warn(f"error analysing {repo}: {exc}")
            continue
        report.fetch_attempted = not no_fetch
        if not no_fetch:
            report.fetched_at = fetch_head_mtime(repo)
        if update:
            updated.extend((repo, line) for line in fast_forward_worktrees(repo))
        _emit_one(report)

    _emit_summary(updated)


@app.command()
def remote(
    path: Path = typer.Argument(Path(".")),
    no_fetch: bool = typer.Option(False, "--no-fetch"),
    author: list[str] = typer.Option([], "--author",
                                      help="Substring of 'Name <email>'. Repeatable, "
                                           "or comma-separated."),
    branch: str | None = typer.Option(None, "--branch",
                                      help="Substring of the branch name."),
) -> None:
    """Remote branches matching the filters."""
    _run(ANALYSES["remote"], path, no_fetch, author, branch)


@app.command()
def merged(
    path: Path = typer.Argument(Path(".")),
    no_fetch: bool = typer.Option(False, "--no-fetch"),
    author: list[str] = typer.Option([], "--author",
                                      help="Substring of 'Name <email>'. Repeatable, "
                                           "or comma-separated."),
    branch: str | None = typer.Option(None, "--branch",
                                      help="Substring of the branch name."),
) -> None:
    """Remote branches whose changes are already in the base branch."""
    _run(ANALYSES["merged"], path, no_fetch, author, branch)


def build_parser() -> typer.Typer:
    """The configured app, exposed so tests can drive it."""
    return app


def _resolve_root(path: Path) -> Path:
    """The scan root, or a usage error if it is not a directory.

    A mistyped path must not produce an empty, exit-0 report: in a weekly cron
    job that is indistinguishable from "everything is fine".
    """
    root = path.expanduser().resolve()
    if not root.is_dir():
        _warn(f"error: not a directory: {root}")
        raise typer.Exit(code=1)
    return root


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


def _run(analysis, path: Path, no_fetch: bool, author: list[str] | None,
         branch_filter: str | None) -> None:
    config = Config.load()
    root = _resolve_root(path)
    reports: list = []

    for repo in discover_repos(root):
        # Fetch BEFORE resolving the base ref. Resolving first would judge a
        # branch against a local `main` that this run's own fetch is about to
        # supersede, reporting live branches as unmerged.
        if not no_fetch:
            fetch_all(repo)
        base_ref = resolve_base_ref(repo)
        identities = resolve_identities(author or [], config, repo)
        try:
            report = analysis(repo, config, base_ref, identities, branch_filter, False)
        except (GitError, OSError, subprocess.SubprocessError) as exc:
            _warn(f"error analysing {repo}: {exc}")
            continue
        report.fetch_attempted = not no_fetch
        if not no_fetch:
            report.fetched_at = fetch_head_mtime(repo)
        reports.append(report)
        _emit_one(report)

    _emit_summary([])


def run_command(command: str, path: Path, no_fetch: bool,
                author: list[str] | None = None,
                branch_filter: str | None = None) -> None:
    """Run one named subcommand's analysis over *path*."""
    _run(ANALYSES[command], path, no_fetch, author or [], branch_filter)


def _emit_one(report) -> None:
    """Stream one repository's section as soon as it is produced."""
    emit(render_report([report], 1 if report.is_stale else 0))


def _emit_summary(updated: list) -> None:
    """Print the roll-up: staleness warning and any fast-forwards."""
    if updated:
        lines = ["", "## fast-forwarded", ""]
        for repo, line in updated:
            lines.append(f"- {repo.name}: {line}")
        emit("\n".join(lines) + "\n")


def fast_forward_worktrees(repo: Path) -> list[str]:
    """``git merge --ff-only`` in each clean, attached worktree.

    Skips are reported rather than passed over silently, because a worktree
    holding an unfinished refactor is exactly the kind of thing a repo-hygiene
    tool should surface rather than hide. A worktree whose cleanliness cannot
    be determined is skipped: a safety gate must fail closed.
    """
    results: list[str] = []
    for worktree, branch in list_worktrees(repo):
        if branch is None:
            results.append("`?` — [DETACHED] skipped: detached HEAD")
            continue
        status = run_git(worktree, "status", "--porcelain", check=False)
        if status.returncode != 0:
            results.append(f"`{branch}` — [UNKNOWN] skipped: worktree status unreadable")
            continue
        if status.stdout.strip():
            results.append(f"`{branch}` — [DIRTY] skipped: uncommitted changes")
            continue
        has_upstream = run_git(worktree, "rev-parse", "--verify", "--quiet",
                               "@{upstream}").returncode == 0
        if not has_upstream:
            results.append(f"`{branch}` — [OK] skipped: no upstream configured")
            continue
        proc = run_git(worktree, "merge", "--ff-only")
        if proc.returncode == 0:
            results.append(f"`{branch}` — [OK] fast-forwarded")
        else:
            results.append(f"`{branch}` — [OK] skipped: not a fast-forward")
    return results


def main() -> None:
    app()


if __name__ == "__main__":
    main()
