"""Tests for the git subprocess seam."""

import subprocess
from pathlib import Path

import pytest

from git_ferret.gitcmd import GitError, run_git, run_git_text
from tests.helpers import commit, make_repo


def test_run_git_text_returns_stdout(tmp_path: Path):
    repo = make_repo(tmp_path / "r")
    (repo / "f.txt").write_text("a\n")
    commit(repo, "base")
    assert run_git_text(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_run_git_text_raises_on_error(tmp_path: Path):
    repo = make_repo(tmp_path / "r")
    with pytest.raises(GitError) as exc:
        run_git_text(repo, "rev-parse", "does-not-exist")
    assert exc.value.returncode != 0


def test_run_git_check_false_does_not_raise(tmp_path: Path):
    repo = make_repo(tmp_path / "r")
    proc = run_git(repo, "rev-parse", "does-not-exist")
    assert proc.returncode != 0


def test_a_missing_git_binary_is_a_git_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Not installed is the obvious case, and it must arrive as the same error
    # type as any other git failure rather than as a FileNotFoundError that
    # callers do not know to expect.
    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr("git_ferret.gitcmd.subprocess.run", missing)
    with pytest.raises(GitError) as exc:
        run_git(tmp_path, "status")
    assert "git status failed" in str(exc.value)


def test_a_git_timeout_is_a_git_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # A slow fetch is exactly the long operation, so the 120s timeout is
    # reachable; it has to be a reported failure, not a traceback.
    def timed_out(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr("git_ferret.gitcmd.subprocess.run", timed_out)
    with pytest.raises(GitError) as exc:
        run_git(tmp_path, "fetch")
    assert "timed out" in str(exc.value)
