# SPDX-FileCopyrightText: 2026 Fran
# SPDX-License-Identifier: GPL-3.0-only

"""Enforce the SPDX licence header on every Python source file.

The header has to sit in the leading comment block, before the module
docstring. Checking the first few lines alone would miss a file whose header
was pasted below the imports, and a licence that is not attached to the file
is not doing its job.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

COPYRIGHT = "# SPDX-FileCopyrightText: 2026 Fran"
LICENSE = "# SPDX-License-Identifier: GPL-3.0-only"
DEFAULT_ROOTS = ("git_ferret", "tests", "scripts")


def _leading_comments(path: Path) -> list[str]:
    """The comment and blank lines before the first line of code."""
    block: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            block.append(stripped)
            continue
        break
    return block


def has_header(path: Path) -> bool:
    block = _leading_comments(path)
    return COPYRIGHT in block and LICENSE in block


def python_files(roots: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            files.append(root)
        elif root.is_dir():
            files.extend(path for path in root.rglob("*.py") if path.is_file())
    return sorted(files)


def missing_headers(roots: Iterable[Path]) -> list[Path]:
    return [path for path in python_files(roots) if not has_header(path)]


def main(argv: list[str]) -> int:
    roots = [Path(arg) for arg in argv[1:]] or [Path(root) for root in DEFAULT_ROOTS]
    missing = missing_headers(roots)
    if not missing:
        return 0

    for path in missing:
        print(
            f"check_spdx: {path} is missing '{COPYRIGHT}' and '{LICENSE}'",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
