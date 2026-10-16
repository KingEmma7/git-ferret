"""Tests for the git subprocess seam."""
import pytest

from git_wombat.gitcmd import GitError, git_available, run_git, run_git_text

from tests.helpers import commit, make_repo


def test_run_git_text_returns_stdout(tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "f.txt").write_text("a\n")
    commit(repo, "base")
    assert run_git_text(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_run_git_text_raises_on_error(tmp_path):
    repo = make_repo(tmp_path / "r")
    with pytest.raises(GitError) as exc:
        run_git_text(repo, "rev-parse", "does-not-exist")
    assert exc.value.returncode != 0


def test_run_git_check_false_does_not_raise(tmp_path):
    repo = make_repo(tmp_path / "r")
    proc = run_git(repo, "rev-parse", "does-not-exist")
    assert proc.returncode != 0


def test_git_available():
    assert git_available() is True
