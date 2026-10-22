# SPDX-FileCopyrightText: 2026 Fran
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the SPDX header check that runs in ``make check``."""

from pathlib import Path

import pytest

from scripts.check_spdx import COPYRIGHT, LICENSE, main, missing_headers


def _source(path: Path, header: str = f"{COPYRIGHT}\n{LICENSE}\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'{header}\n"""A module."""\n')
    return path


def test_a_file_with_both_headers_passes(tmp_path: Path) -> None:
    _source(tmp_path / "pkg" / "good.py")
    assert missing_headers([tmp_path]) == []


def test_a_file_without_headers_is_reported(tmp_path: Path) -> None:
    bad = tmp_path / "pkg" / "bad.py"
    bad.parent.mkdir()
    bad.write_text('"""No licence header."""\n')
    assert missing_headers([tmp_path]) == [bad]


def test_a_file_with_only_the_licence_is_reported(tmp_path: Path) -> None:
    _source(tmp_path / "pkg" / "half.py", f"{LICENSE}\n")
    assert missing_headers([tmp_path]) == [tmp_path / "pkg" / "half.py"]


def test_a_header_after_code_does_not_count(tmp_path: Path) -> None:
    bad = tmp_path / "late.py"
    bad.write_text(f'"""A module."""\n\n{COPYRIGHT}\n{LICENSE}\n')
    assert missing_headers([tmp_path]) == [bad]


def test_non_python_files_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("no header here\n")
    assert missing_headers([tmp_path]) == []


def test_missing_roots_are_ignored(tmp_path: Path) -> None:
    assert missing_headers([tmp_path / "absent"]) == []


def test_main_fails_and_names_the_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("x = 1\n")
    assert main(["check_spdx", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "bad.py" in err
    assert "SPDX" in err


def test_main_succeeds_when_every_file_has_a_header(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _source(tmp_path / "good.py")
    assert main(["check_spdx", str(tmp_path)]) == 0
    assert capsys.readouterr().err == ""
