"""User configuration: identity resolution and the branch whitelist.

Identity precedence is ``--author`` > ``~/.git-wombat.toml`` > git config, and
it resolves *per repository* because a collection of repos may legitimately
carry different local identities.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Iterable

from git_wombat.gitcmd import run_git

DEFAULT_CONFIG_PATH = Path.home() / ".git-wombat.toml"

# Always excluded from branch reports, whatever the configuration says.
ALWAYS_EXCLUDED = ("main", "master")


@dataclass
class Config:
    """Resolved user configuration.

    ``author`` holds one or more identities; they combine with OR, because the
    intent is "all of my addresses" rather than "a team".
    """

    author: list[str] = field(default_factory=list)
    whitelist: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Read the TOML config. A missing file is not an error."""
        config_path = path if path is not None else DEFAULT_CONFIG_PATH
        if not config_path.exists():
            return cls()
        data = tomllib.loads(config_path.read_text())
        raw_author = data.get("author", [])
        author = [raw_author] if isinstance(raw_author, str) else list(raw_author)
        whitelist = list(data.get("whitelist", {}).get("patterns", []))
        return cls(author=author, whitelist=whitelist)

    def is_whitelisted(self, branch: str) -> bool:
        """Whether *branch* matches a whitelist pattern.

        Case-sensitive: git branch names are, and a case-insensitive match here
        would silently hide branches the user meant to see.
        """
        return any(fnmatchcase(branch, pattern) for pattern in self.whitelist)

    def is_excluded(self, branch: str, base_ref: str | None) -> bool:
        """Whether *branch* should never appear in a report.

        Covers ``main``, ``master``, the resolved base ref, and the whitelist.
        """
        if branch in ALWAYS_EXCLUDED:
            return True
        if base_ref is not None and branch in (base_ref, base_ref.split("/")[-1]):
            return True
        return self.is_whitelisted(branch)


def resolve_identities(
    cli_author: list[str], config: Config, repo: Path
) -> list[str]:
    """Resolve author identities, most specific source first.

    Returns ``[]`` when nothing resolves. An empty result must never be passed
    to git's ``--author``: an empty pattern matches *every* commit, which would
    report every branch as the user's own.
    """
    if cli_author:
        return list(cli_author)
    if config.author:
        return list(config.author)

    name = run_git(repo, "config", "--get", "user.name").stdout.strip()
    email = run_git(repo, "config", "--get", "user.email").stdout.strip()
    if name and email:
        return [f"{name} <{email}>"]
    if email:
        return [email]
    return []


def identity_matches(
    commits_author_strings: Iterable[str], identities: list[str]
) -> bool:
    """Whether any commit was authored by any of *identities*.

    Substring, not equality: the user configures a fragment such as an email
    address and it must match the full ``Name <email>`` git records.
    """
    if not identities:
        return False
    authors = list(commits_author_strings)
    return any(
        identity in author
        for identity in identities
        for author in authors
    )
