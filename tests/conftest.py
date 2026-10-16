"""Pytest fixtures wrapping the importable helpers in ``tests.helpers``.

Test modules import helpers from ``tests.helpers``, never from here. The
``make_repo`` fixture takes no arguments and hands back a repository under
``tmp_path``; a test that needs the repository at a specific path calls the
helper directly instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import clone_local as clone_local_helper
from tests.helpers import make_repo as make_repo_helper


@pytest.fixture
def make_repo(tmp_path: Path) -> Path:
    """A fresh repository under this test's ``tmp_path``."""
    return make_repo_helper(tmp_path / "repo")


@pytest.fixture
def clone_local(tmp_path: Path):
    """``clone_local(src)`` clones under ``tmp_path``; ``clone_local(src, dest)`` is honoured."""

    def _clone(src: Path, dest: Path | None = None, depth: int | None = None) -> Path:
        return clone_local_helper(src, dest if dest is not None else tmp_path / "clone", depth=depth)

    return _clone
