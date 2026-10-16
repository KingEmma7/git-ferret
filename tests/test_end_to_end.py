"""End-to-end coverage: all three subcommands over a realistic repository tree."""
from typer.testing import CliRunner

from git_wombat.cli import build_parser

from tests.helpers import clone_local, commit, make_repo, run

runner = CliRunner()


def _tree(tmp_path):
    """A pushed+stale branch, a merged branch, a clean branch, and a remote-less repo."""
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\nb\nc\n")
    commit(src, "base")

    run(src, "checkout", "-b", "stale-work")
    (src / "f.txt").write_text("a\nSTALE\nc\n")
    commit(src, "work")
    run(src, "checkout", "main")

    run(src, "checkout", "-b", "already-done")
    (src / "d.txt").write_text("done\n")
    commit(src, "finished work")
    run(src, "checkout", "main")
    run(src, "merge", "--squash", "already-done")
    commit(src, "merge already-done")

    clone = clone_local(src, tmp_path / "project")
    run(clone, "fetch", "--all", "--prune")

    # Unpushed work that exists only in the clone.
    run(clone, "checkout", "-b", "local-only", "origin/main")
    (clone / "mine.txt").write_text("mine\n")
    commit(clone, "local only")
    run(clone, "checkout", "main")

    make_repo(tmp_path / "solo")  # a repository with no remote at all
    return tmp_path


def test_local_end_to_end_emits_valid_markdown(tmp_path):
    root = _tree(tmp_path)
    res = runner.invoke(build_parser(), ["local", str(root), "--no-fetch"])
    assert res.exit_code == 0, res.output
    assert res.stdout.startswith("# git-wombat")
    assert "\x1b[" not in res.stdout


def test_all_three_subcommands_succeed(tmp_path):
    root = _tree(tmp_path)
    for cmd in ("local", "remote", "merged"):
        res = runner.invoke(build_parser(), [cmd, str(root), "--no-fetch"])
        assert res.exit_code == 0, f"{cmd} failed: {res.output}"


def test_reports_every_discovered_repo(tmp_path):
    root = _tree(tmp_path)
    res = runner.invoke(build_parser(), ["local", str(root), "--no-fetch"])
    assert "project" in res.stdout
    assert "solo" in res.stdout


def test_output_has_no_tables(tmp_path):
    root = _tree(tmp_path)
    res = runner.invoke(build_parser(), ["local", str(root), "--no-fetch"])
    assert "|" not in res.stdout


def test_every_finding_carries_a_marker(tmp_path):
    root = _tree(tmp_path)
    res = runner.invoke(build_parser(), ["local", str(root), "--no-fetch"])
    for line in res.stdout.splitlines():
        if line.startswith("- ") and "`" in line:
            assert "[" in line and "]" in line, f"unmarked finding: {line}"


def test_merged_reports_the_merged_branch(tmp_path):
    root = _tree(tmp_path)
    res = runner.invoke(build_parser(), ["merged", str(root), "--no-fetch"])
    assert "origin/already-done" in res.stdout
    assert "safe to delete" in res.stdout


def test_merged_does_not_recommend_the_stale_branch(tmp_path):
    root = _tree(tmp_path)
    res = runner.invoke(build_parser(), ["merged", str(root), "--no-fetch"])
    assert "origin/stale-work" not in res.stdout.split("safe to delete")[0]


def test_stale_warning_present_when_no_fetch(tmp_path):
    root = _tree(tmp_path)
    res = runner.invoke(build_parser(), ["local", str(root), "--no-fetch"])
    assert "stale" in res.stdout.lower()


def test_nested_repo_is_not_reported_twice(tmp_path):
    outer = make_repo(tmp_path / "outer")
    make_repo(outer / "vendor" / "inner")
    res = runner.invoke(build_parser(), ["local", str(tmp_path), "--no-fetch"])
    assert res.stdout.count("## ") == 1


def test_repo_without_base_branch_is_skipped_with_reason(tmp_path):
    repo = make_repo(tmp_path / "trunky")
    (repo / "f.txt").write_text("a\n")
    commit(repo, "base")
    run(repo, "branch", "-m", "main", "trunk")
    res = runner.invoke(build_parser(), ["local", str(tmp_path), "--no-fetch"])
    assert "no base branch" in res.stdout


def test_local_does_not_write_without_update(tmp_path):
    root = _tree(tmp_path)
    before = run(root / "project", "status", "--porcelain").stdout
    runner.invoke(build_parser(), ["local", str(root), "--no-fetch"])
    after = run(root / "project", "status", "--porcelain").stdout
    assert before == after
