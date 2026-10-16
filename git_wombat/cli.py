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
from git_wombat.gitops import fetch_all, refs_updated_at, resolve_base_ref
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
    header_state = [False]

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
            report.fetched_at = refs_updated_at(repo)
        if update:
            updated.extend((repo, line) for line in fast_forward_worktrees(repo))
        _emit_one(report, header_state)

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


class UsageError(Exception):
    """A problem with how the tool was invoked, as opposed to what it found."""


def _resolve_root(path: Path) -> Path:
    """The scan root, or a usage error if it is not a directory.

    A mistyped path must not produce an empty, exit-0 report: in a weekly cron
    job that is indistinguishable from "everything is fine".

    This raises ``UsageError`` rather than ``typer.Exit`` because typer's own
    ``__call__`` swallows non-ClickException exceptions, so a deliberate exit
    code raised inside a command never reaches the entry point.
    """
    root = path.expanduser().resolve()
    if not root.is_dir():
        raise UsageError(f"not a directory: {root}")
    return root


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


def _run(analysis, path: Path, no_fetch: bool, author: list[str] | None,
         branch_filter: str | None) -> None:
    config = Config.load()
    root = _resolve_root(path)
    reports: list = []
    header_state = [False]

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
            report.fetched_at = refs_updated_at(repo)
        reports.append(report)
        _emit_one(report, header_state)

    _emit_summary([])


def run_command(command: str, path: Path, no_fetch: bool,
                author: list[str] | None = None,
                branch_filter: str | None = None) -> None:
    """Run one named subcommand's analysis over *path*."""
    _run(ANALYSES[command], path, no_fetch, author or [], branch_filter)


def _emit_one(report, header_state: list[bool]) -> None:
    """Stream one repository's section as soon as it is produced.

    The document header and the staleness warning are emitted once, on the
    first repository, rather than repeated above every section. Staleness is
    marked per repository in the heading itself, so nothing is lost.

    The flag lives in a caller-owned list rather than a module global: a global
    would leave it set after the first run, so a second invocation in the same
    process would emit no header and, with it, no staleness warning at all.
    """
    text = render_report([report], 1 if (report.is_stale and not header_state[0]) else 0)
    if header_state[0]:
        text = text.split("\n", 1)[1].lstrip("\n")
    else:
        header_state[0] = True
    emit(text)


def _emit_summary(updated: list) -> None:
    """Print the roll-up: staleness warning and any fast-forwards."""
    if updated:
        lines = ["", "## fast-forwarded", ""]
        for repo, line in updated:
            lines.append(f"- {repo.name}: {line}")
        emit("\n".join(lines) + "\n")


def fast_forward_worktrees(repo: Path) -> list[str]:
    """``git merge --ff-only`` in each clean, attached worktree.

    Git itself refuses a fast-forward that would overwrite a locally modified
    file, so checking first is about *reporting*, not about safety: a skipped
    worktree still needs to say why, and "not a fast-forward" would be wrong for
    a branch that simply has uncommitted work in it.

    Skips are reported rather than passed over silently, because a worktree
    holding an unfinished refactor is exactly the kind of thing a repo-hygiene
    tool should surface. A worktree whose cleanliness cannot be determined is
    skipped: a safety gate must fail closed.
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
            results.append(
                f"`{branch}` — [DIRTY] skipped: uncommitted changes "
                "(git could fast-forward these, if they do not overlap)"
            )
            continue
        has_upstream = run_git(worktree, "rev-parse", "--verify", "--quiet",
                               "@{upstream}").returncode == 0
        if not has_upstream:
            results.append(f"`{branch}` — [OK] skipped: no upstream configured")
            continue
        behind = run_git(worktree, "rev-list", "--count", "HEAD..@{upstream}")
        if behind.returncode == 0 and behind.stdout.strip() == "0":
            results.append(f"`{branch}` — [OK] already up to date")
            continue
        proc = run_git(worktree, "merge", "--ff-only")
        if proc.returncode == 0:
            results.append(f"`{branch}` — [OK] fast-forwarded")
        else:
            results.append(
                f"`{branch}` — [DIVERGED] skipped: cannot fast-forward "
                "(local and upstream have diverged)"
            )
    return results


def main() -> None:
    """Entry point.

    Click's default exit code for a usage error is 2, which reads like a crash
    rather than "you typed it wrong". Everything the tool considers a usage
    problem exits 1: a bad flag, an unknown command, or a path that is not a
    directory. Findings never change the exit code.
    """
    from click.exceptions import Abort, Exit, UsageError as ClickUsageError

    try:
        app(standalone_mode=False)
    except UsageError as exc:
        _warn(f"error: {exc}")
        raise SystemExit(1) from None
    except ClickUsageError as exc:
        # An unknown flag or command. Click's own default is 2, which reads
        # like a crash rather than "you typed it wrong".
        exc.show()
        raise SystemExit(1) from None
    except Abort:
        _warn("aborted")
        raise SystemExit(1) from None
    except Exit as exc:
        raise SystemExit(exc.exit_code) from None


if __name__ == "__main__":
    main()
