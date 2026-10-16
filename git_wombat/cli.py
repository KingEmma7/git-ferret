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

from git_wombat.analysis.health import report_remote_health
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
from git_wombat.report import display_path, emit, render_header, render_repo

app = typer.Typer(help="Audit many local git repositories at once. Reports only.")

ANALYSES = {
    "local": lambda repo, cfg, base, ids, bfilter: analyse_local(repo, cfg, base),
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
        attempted, failed = _fetch_report(repo, no_fetch)
        base_ref = resolve_base_ref(repo)
        try:
            report = analyse_local(repo, config, base_ref)
        except (GitError, OSError, subprocess.SubprocessError) as exc:
            _warn(f"error analysing {repo}: {exc}")
            continue
        report.fetch_attempted = attempted
        report.fetch_failed = failed
        report_remote_health(repo, report)
        if update:
            updated.extend((repo, branch)
                           for branch in fast_forward_worktrees(repo))
        _emit_one(report, root, header_state)

    _emit_summary(updated, root)


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


def _fetch_report(repo: Path, no_fetch: bool) -> tuple[bool, bool]:
    """Fetch *repo* unless asked not to; return ``(attempted, failed)``.

    The one place a fetch happens. Its outcome is recorded on the report the
    caller is about to build, so the analyses stay free of the network and the
    stale warning and the ``[UNREACHABLE]`` finding read from the same source.
    """
    if no_fetch:
        return False, False
    ok, _when = fetch_all(repo)
    return True, not ok


def _run(analysis, path: Path, no_fetch: bool, author: list[str] | None,
         branch_filter: str | None) -> None:
    config = Config.load()
    root = _resolve_root(path)
    header_state = [False]

    for repo in discover_repos(root):
        # Fetch BEFORE resolving the base ref. Resolving first would judge a
        # branch against a local `main` that this run's own fetch is about to
        # supersede, reporting live branches as unmerged.
        attempted, failed = _fetch_report(repo, no_fetch)
        base_ref = resolve_base_ref(repo)
        identities = resolve_identities(author or [], config, repo)
        try:
            report = analysis(repo, config, base_ref, identities, branch_filter)
        except (GitError, OSError, subprocess.SubprocessError) as exc:
            _warn(f"error analysing {repo}: {exc}")
            continue
        report.fetch_attempted = attempted
        report.fetch_failed = failed
        _emit_one(report, root, header_state)

    _emit_summary([], root)


def run_command(command: str, path: Path, no_fetch: bool,
                author: list[str] | None = None,
                branch_filter: str | None = None) -> None:
    """Run one named subcommand's analysis over *path*."""
    _run(ANALYSES[command], path, no_fetch, author or [], branch_filter)


def _emit_one(report: RepoReport, root: Path, header_state: list[bool]) -> None:
    """Stream one repository's section as soon as it is produced.

    The document header and the staleness warning are emitted once, on the
    first repository, rather than repeated above every section. Staleness is
    marked per repository in the heading itself, so nothing is lost.

    The flag lives in a caller-owned list rather than a module global: a global
    would leave it set after the first run, so a second invocation in the same
    process would emit no header and, with it, no staleness warning at all.

    Sections are emitted from ``render_repo`` directly rather than by slicing a
    whole-document render: the document strips its trailing blank line, so
    reusing it per repository ran every section into the next one.
    """
    if not header_state[0]:
        header_state[0] = True
        emit(render_header(1 if report.is_stale else 0, total=1))
    emit(render_repo(report, root))


def _emit_summary(updated: list, root: Path) -> None:
    """The record of what this run wrote, one line per fast-forward.

    No marker on these lines: the section says what they are, and a status
    marker here would be a judgement about work that was simply done as asked.
    """
    if not updated:
        return
    lines = ["## fast-forwarded", ""]
    lines += [f"- {display_path(repo, root)}: `{branch}` fast-forwarded"
              for repo, branch in updated]
    emit("\n".join(lines) + "\n")


def fast_forward_worktrees(repo: Path) -> list[str]:
    """The branches fast-forwarded in *repo*, and nothing else.

    ``git merge --ff-only`` in each clean, attached worktree that is behind its
    upstream. Git itself refuses a fast-forward that would overwrite a locally
    modified file, so the checks here are about *reporting* rather than about
    safety, and the one check that cannot be read fails closed.

    Only the branches that moved are returned, because every other outcome is
    already in the repository's own section: a detached HEAD, a dirty worktree,
    a worktree whose status could not be read (``worktree_status`` reports that
    as dirty, which is what it is for), a branch with no upstream, and a
    diverged one each produce a finding there. Listing them again under a
    heading that says "fast-forwarded" would put work that was deliberately
    not done beside work that was.

    The one case no finding covers is a base branch (``main``, ``master``, the
    resolved base ref) with no upstream: base branches are excluded from branch
    findings by design, on the grounds that they are not findings. It is left
    unreported for that reason, not by oversight.
    """
    updated: list[str] = []
    for worktree, branch in list_worktrees(repo):
        if branch is None:
            continue
        status = run_git(worktree, "status", "--porcelain", check=False)
        if status.returncode != 0 or status.stdout.strip():
            continue
        has_upstream = run_git(worktree, "rev-parse", "--verify", "--quiet",
                               "@{upstream}").returncode == 0
        if not has_upstream:
            continue
        behind = run_git(worktree, "rev-list", "--count", "HEAD..@{upstream}")
        if behind.returncode == 0 and behind.stdout.strip() == "0":
            continue
        proc = run_git(worktree, "merge", "--ff-only")
        if proc.returncode == 0:
            updated.append(branch)
    return updated


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
