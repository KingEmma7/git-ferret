"""Tests for the --update write path.

This is the only code in the project permitted to change a repository, so its
skips are asserted as carefully as its effect.
"""
from git_wombat.cli import fast_forward_worktrees

from tests.helpers import clone_local, commit, make_repo, run


def test_fast_forwards_a_clean_worktree_behind_upstream(tmp_path):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "checkout", "-b", "feat", "origin/main")
    (src / "f.txt").write_text("a\nremote\n")
    commit(src, "remote work")
    run(clone, "fetch", "--all", "--prune")
    assert run(clone, "rev-list", "--count", "HEAD..@{upstream}").stdout.strip() == "1"

    results = fast_forward_worktrees(clone)

    assert any("fast-forwarded" in r for r in results)
    assert run(clone, "rev-list", "--count", "HEAD..@{upstream}").stdout.strip() == "0"


def test_skips_dirty_worktree(tmp_path):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "checkout", "-b", "feat", "origin/main")
    (src / "f.txt").write_text("a\nremote\n")
    commit(src, "remote work")
    run(clone, "fetch", "--all", "--prune")
    (clone / "uncommitted.txt").write_text("wip\n")

    results = fast_forward_worktrees(clone)

    assert any("uncommitted changes" in r for r in results)
    # The dirty file must survive untouched.
    assert (clone / "uncommitted.txt").read_text() == "wip\n"


def test_skips_branch_with_no_upstream(tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "f.txt").write_text("a\n")
    commit(repo, "base")
    run(repo, "checkout", "-b", "solo")
    results = fast_forward_worktrees(repo)
    assert any("no upstream" in r for r in results)


def test_skips_detached_head(tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "f.txt").write_text("a\n")
    commit(repo, "base")
    sha = run(repo, "rev-parse", "HEAD").stdout.strip()
    run(repo, "checkout", sha)
    results = fast_forward_worktrees(repo)
    assert any("detached HEAD" in r for r in results)


def test_reports_when_already_up_to_date(tmp_path):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    clone = clone_local(src, tmp_path / "clone")
    results = fast_forward_worktrees(clone)
    assert results, "an up-to-date worktree should still report its state"


def test_does_not_delete_anything(tmp_path):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    run(src, "branch", "keepme")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "checkout", "-b", "keepme", "origin/keepme")
    before = run(clone, "branch", "--format=%(refname)").stdout
    fast_forward_worktrees(clone)
    after = run(clone, "branch", "--format=%(refname)").stdout
    assert before == after
