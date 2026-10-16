"""Tests for fetch-age reporting and CLI exit codes.

Both come from the final review:

- `git clone` does not create FETCH_HEAD, so a freshly cloned repository read as
  "never fetched" and was counted stale -- a false staleness signal on the
  freshest state possible, which trains you to ignore the warning that exists
  precisely to stop stale answers passing as current.
- An unrecognised command or flag exited 2; the documented contract is 1 for a
  usage error.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from git_wombat.gitops import fetch_head_mtime, refs_updated_at

from tests.helpers import clone_local, commit, make_repo, run

REPO_ROOT = Path(__file__).resolve().parent.parent


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "git_wombat.cli", *args],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )


# --- fetch age ----------------------------------------------------------------

def test_fresh_clone_has_a_known_refs_timestamp(tmp_path):
    # `git clone` writes no FETCH_HEAD, so the clone must fall back to the
    # remote-tracking refs rather than reporting "never fetched".
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    clone = clone_local(src, tmp_path / "clone")
    assert fetch_head_mtime(clone) is None
    assert refs_updated_at(clone) is not None


def test_refs_timestamp_is_recent_for_a_fresh_clone(tmp_path):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    clone = clone_local(src, tmp_path / "clone")
    stamp = refs_updated_at(clone)
    assert stamp is not None
    assert (time.time() - stamp.timestamp()) < 300


def test_refs_updated_at_none_for_a_repo_with_no_refs(tmp_path):
    repo = make_repo(tmp_path / "r")
    assert refs_updated_at(repo) is None


def test_fetched_repo_uses_fetch_head(tmp_path):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch")
    assert fetch_head_mtime(clone) is not None
    assert refs_updated_at(clone) is not None


def test_fresh_clone_reports_when_its_refs_were_written(tmp_path, capsys):
    from git_wombat.cli import run_command

    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    clone_local(src, tmp_path / "clone")

    run_command("local", tmp_path / "clone", True, [], None)
    out = capsys.readouterr().out
    # A fresh clone writes refs but no FETCH_HEAD: the timestamp must come
    # from the refs rather than claiming the repository was never fetched.
    assert "never fetched" not in out
    assert "fetched " in out
    # --no-fetch still warns, because no fetch happened this run.
    assert "stale" in out.lower()


def test_genuinely_stale_state_is_still_flagged(tmp_path, capsys):
    from git_wombat.cli import run_command

    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch")
    # Age every timestamp this tool could read.
    old = time.time() - 90 * 86400
    git_dir = clone / ".git"
    for path in [git_dir / "FETCH_HEAD", *git_dir.glob("refs/remotes/**/*")]:
        if path.is_file():
            os.utime(path, (old, old))

    run_command("local", tmp_path / "clone", True, [], None)
    assert "stale" in capsys.readouterr().out.lower()


# --- exit codes ---------------------------------------------------------------

def test_unknown_command_exits_one():
    result = _cli("bogus-command")
    assert result.returncode == 1, result.stderr


def test_unknown_flag_exits_one():
    result = _cli("local", "--bogus-flag")
    assert result.returncode == 1, result.stderr


def test_missing_path_exits_one(tmp_path):
    result = _cli("local", str(tmp_path / "nope"), "--no-fetch")
    assert result.returncode == 1, result.stderr


def test_help_exits_zero():
    assert _cli("--help").returncode == 0


def test_findings_alone_exit_zero(tmp_path):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    run(src, "branch", "never-pushed")
    result = _cli("local", str(tmp_path / "src"), "--no-fetch")
    assert result.returncode == 0, result.stderr


def test_valid_run_exits_zero(tmp_path):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    for cmd in ("local", "remote", "merged"):
        assert _cli(cmd, str(tmp_path / "src"), "--no-fetch").returncode == 0
