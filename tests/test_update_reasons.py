"""Tests for the fast-forward reasons --update reports.

"Not a fast-forward" must mean the branch genuinely diverged, not "we declined
to try", or a reader cannot tell what state their branch is in.
"""
from git_wombat.cli import fast_forward_worktrees

from tests.helpers import clone_local, commit, make_repo, run


def _behind_clone(tmp_path):
    """A clone whose checked-out branch is behind its upstream by one commit."""
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "checkout", "-b", "feat", "origin/main")
    (src / "f.txt").write_text("a\nremote\n")
    commit(src, "remote work")
    run(clone, "fetch", "--all", "--prune")
    return clone


def test_dirty_worktree_explains_it_could_have_been_updated(tmp_path):
    clone = _behind_clone(tmp_path)
    (clone / "wip.txt").write_text("x\n")
    results = fast_forward_worktrees(clone)
    line = next(r for r in results if "feat" in r)
    assert "uncommitted changes" in line
    assert "DIRTY" in line
    assert "not a fast-forward" not in line


def test_diverged_branch_is_reported_as_diverged(tmp_path):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    run(src, "branch", "feat")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "checkout", "-b", "feat", "origin/feat")
    (clone / "local.txt").write_text("local\n")
    commit(clone, "local work")
    # The remote's `feat` must move too, or the branch is merely ahead.
    run(src, "checkout", "feat")
    (src / "f.txt").write_text("a\nremote\n")
    commit(src, "remote work")
    run(src, "checkout", "main")
    run(clone, "fetch", "--all", "--prune")

    results = fast_forward_worktrees(clone)
    line = next(r for r in results if "feat" in r)
    assert "DIVERGED" in line
    assert "diverged" in line


def test_up_to_date_branch_is_not_called_fast_forwarded(tmp_path):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    clone = clone_local(src, tmp_path / "clone")
    results = fast_forward_worktrees(clone)
    line = next(r for r in results if "main" in r)
    assert "already up to date" in line
    assert "fast-forwarded" not in line


def test_every_result_carries_a_marker(tmp_path):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "checkout", "-b", "solo")
    results = fast_forward_worktrees(clone)
    assert results
    for line in results:
        assert "[" in line and "]" in line, f"unmarked update result: {line}"
