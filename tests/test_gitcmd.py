# SPDX-FileCopyrightText: 2026 Fran
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the git subprocess seam."""

import os
import subprocess
import sys
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


def test_run_git_decodes_utf8_under_an_ascii_locale(tmp_path: Path):
    # Windows uses the console code page for subprocess text unless told
    # otherwise, and a UTF-8 branch name then raises UnicodeDecodeError. Run
    # the seam in a child interpreter whose locale really is ASCII, so a
    # regression to the locale default fails here rather than on a Windows
    # runner.
    repo = make_repo(tmp_path / "r")
    (repo / "f.txt").write_text("a\n")
    commit(repo, "base")
    run_git(repo, "branch", "café", check=True)

    # The branch name is built from an escape so the child's ASCII argv does
    # not carry the non-ASCII character itself; this test is about decoding
    # git's output, not about passing unicode through a C-locale command line.
    script = (
        "from pathlib import Path\n"
        "from git_ferret.gitcmd import run_git_text\n"
        "name = 'caf\\u00e9'\n"
        f"found = run_git_text(Path({str(repo)!r}), 'branch', '--list')\n"
        "raise SystemExit(0 if name in found else 1)\n"
    )
    env = {**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
