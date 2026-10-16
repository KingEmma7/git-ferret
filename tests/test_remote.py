"""Tests for remote branch filtering and safe-to-delete gating."""
from git_wombat.analysis.remote import (
    analyse_merged_candidates,
    analyse_remote,
    local_branch_unpushed,
)
from git_wombat.config import Config
from git_wombat.gitops import resolve_base_ref

from tests.helpers import clone_local, commit, make_repo, run

FRAN = "Fran <fran@z.com>"
OTHER = "Other <o@x.com>"


def _repo_with_branches(tmp_path, branches):
    """*branches* is a list of (name, author) pairs pushed to origin."""
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\nb\nc\n")
    commit(src, "base")
    for name, author in branches:
        run(src, "checkout", "-b", name, "main")
        (src / f"{name.replace('/', '-')}.txt").write_text(f"{name}\n")
        commit(src, f"work on {name}", author=author)
        run(src, "checkout", "main")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch", "--all", "--prune")
    return clone


def _names(rep):
    return {f.branch for f in rep.findings}


def test_remote_filters_by_branch_name(tmp_path):
    clone = _repo_with_branches(
        tmp_path, [("ABC-123-add-thing", FRAN), ("ZZZ-other", OTHER)]
    )
    rep = analyse_remote(clone, Config(), resolve_base_ref(clone), [], "ABC-123", False)
    assert "origin/ABC-123-add-thing" in _names(rep)
    assert "origin/ZZZ-other" not in _names(rep)


def test_remote_filters_by_author(tmp_path):
    clone = _repo_with_branches(tmp_path, [("by-fran", FRAN), ("by-someone", OTHER)])
    rep = analyse_remote(clone, Config(), resolve_base_ref(clone), ["fran@z.com"], None, False)
    assert "origin/by-fran" in _names(rep)
    assert "origin/by-someone" not in _names(rep)


def test_both_filters_combine_with_and(tmp_path):
    clone = _repo_with_branches(tmp_path, [("by-fran", FRAN), ("by-someone", OTHER)])
    rep = analyse_remote(
        clone, Config(), resolve_base_ref(clone), ["fran@z.com"], "by-fran", False
    )
    assert _names(rep) == {"origin/by-fran"}


def test_remote_ignores_whitelisted_branches(tmp_path):
    clone = _repo_with_branches(tmp_path, [("release/9", FRAN)])
    rep = analyse_remote(clone, Config(whitelist=["release/*"]), resolve_base_ref(clone),
                         [], None, False)
    assert "origin/release/9" not in _names(rep)


def test_remote_never_lists_the_base_ref(tmp_path):
    clone = _repo_with_branches(tmp_path, [("feature", FRAN)])
    rep = analyse_remote(clone, Config(), resolve_base_ref(clone), [], None, False)
    assert "origin/main" not in _names(rep)


def test_empty_identities_lists_all_remote_branches(tmp_path):
    clone = _repo_with_branches(tmp_path, [("someone-elses", OTHER)])
    rep = analyse_remote(clone, Config(), resolve_base_ref(clone), [], None, False)
    assert "origin/someone-elses" in _names(rep)


def _merged_repo(tmp_path):
    """src with `done` squash-merged into main; clone has origin/done merged."""
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\nb\n")
    commit(src, "base")
    run(src, "checkout", "-b", "done")
    (src / "f.txt").write_text("a\nDONE\n")
    commit(src, "work")
    run(src, "checkout", "main")
    run(src, "merge", "--squash", "done")
    commit(src, "merge done")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch", "--all", "--prune")
    return clone


def test_merged_branch_annotated_safe_when_no_local_unpushed(tmp_path):
    clone = _merged_repo(tmp_path)
    assert local_branch_unpushed(clone, "done") is False
    rep = analyse_merged_candidates(clone, Config(), resolve_base_ref(clone), [], None, False)
    f = next(x for x in rep.findings if x.branch == "origin/done")
    assert f.safe_to_delete is True


def test_merged_branch_withheld_when_local_unpushed(tmp_path):
    # The gate that matters most: the remote branch is merged, but the user's
    # local copy of it holds work that exists nowhere else.
    clone = _merged_repo(tmp_path)
    run(clone, "checkout", "-b", "done", "origin/done")
    (clone / "local-only.txt").write_text("mine\n")
    commit(clone, "local only")
    assert local_branch_unpushed(clone, "done") is True
    rep = analyse_merged_candidates(clone, Config(), resolve_base_ref(clone), [], None, False)
    f = next(x for x in rep.findings if x.branch == "origin/done")
    assert f.safe_to_delete is False
    assert "unpushed" in f.detail.lower()


def test_local_branch_unpushed_false_when_branch_absent(tmp_path):
    clone = _merged_repo(tmp_path)
    assert local_branch_unpushed(clone, "no-such-branch") is False


def test_merged_unmerged_branch_is_not_safe(tmp_path):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\nb\nc\n")
    commit(src, "base")
    run(src, "checkout", "-b", "open-work")
    (src / "f.txt").write_text("a\nOPEN\nc\n")
    commit(src, "work")
    run(src, "checkout", "main")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch", "--all", "--prune")
    rep = analyse_merged_candidates(clone, Config(), resolve_base_ref(clone), [], None, False)
    f = next(x for x in rep.findings if x.branch == "origin/open-work")
    assert f.safe_to_delete is False


def test_merged_conflicting_branch_is_not_safe(tmp_path):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\nb\nc\n")
    commit(src, "base")
    run(src, "checkout", "-b", "conflict")
    (src / "f.txt").write_text("SIDE-A\nb\nc\n")
    commit(src, "side a")
    run(src, "checkout", "main")
    (src / "f.txt").write_text("SIDE-B\nb\nc\n")
    commit(src, "side b")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch", "--all", "--prune")
    rep = analyse_merged_candidates(clone, Config(), resolve_base_ref(clone), [], None, False)
    f = next(x for x in rep.findings if x.branch == "origin/conflict")
    assert f.safe_to_delete is False


def test_merged_never_marks_unknown_as_safe(tmp_path):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    run(src, "checkout", "--orphan", "orphan")
    (src / "f.txt").write_text("zzz\n")
    run(src, "add", "-A")
    commit(src, "orphan")
    run(src, "checkout", "main")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch", "--all", "--prune")
    rep = analyse_merged_candidates(clone, Config(), resolve_base_ref(clone), [], None, False)
    f = next(x for x in rep.findings if x.branch == "origin/orphan")
    assert f.safe_to_delete is False


def test_merged_respects_branch_filter(tmp_path):
    clone = _merged_repo(tmp_path)
    rep = analyse_merged_candidates(clone, Config(), resolve_base_ref(clone), [], "nomatch", False)
    assert "origin/done" not in _names(rep)
