# git-ferret Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only CLI that audits many local git repositories at once and reports stale, unpushed, diverged, deleted-upstream, and merged branches as markdown — never deleting anything.

**Architecture:** Three subcommands (`local`, `remote`, `merged`) share one repository walk, one git subprocess layer, and one markdown report assembler. Every subcommand optionally fetches (`fetch --all --prune`) then analyses each repository fully before moving on, streaming output as it goes. Merged-ness is decided by a real in-memory three-way merge (`git merge-tree --write-tree`) compared against the base ref's tree, never by commit ancestry.

**Tech Stack:** Python 3.11+, `typer`, `rich`, `tomllib` (stdlib), `pytest`, `subprocess`.

**Spec:** `docs/superpowers/specs/2026-10-16-git-ferret-design.md`

## Global Constraints

- Python 3.11 or newer. `tomllib` is stdlib; do not add a TOML-writing dependency.
- Shell out to `git` via `subprocess`. Do not add GitPython.
- **Report-only.** No code path may delete, stash, commit, merge non-fast-forward, reset, or rewrite. The sole permitted write is `git merge --ff-only`, reachable only via `local --update`.
- Sequential processing with streamed output. No parallelism.
- No tables in the output. Markdown must stay valid when piped.
- Emoji are decorative only; the capitalized `[STATUS]` marker carries the meaning alone.
- Commit messages: conventional commits, imperative mood (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`).
- Python git fixtures in tests must set `user.name`/`user.email` via `git -c` or `git config`; a bare `git commit` fails with "Author identity unknown" in a container.

### Verified git behaviour this plan depends on

Probed empirically; do not re-derive, do not change the assumptions:

- `git merge-tree --write-tree <base> <branch>` exits **0** and prints the resulting tree hash on success.
- Exit **1** plus the tree hash and a list of `100644 <hash> <stage>\t<path>` lines means **conflicts**.
- Exit **128** with `fatal: refusing to merge unrelated histories` means **no merge base** (unknown).
- Comparing stdout's first line to `git rev-parse <base>^{tree}` decides merged (equal) vs not-merged (differs).
- `git branch -vv` marks a deleted upstream as `[origin/keep: gone]`.
- `git rev-parse --is-shallow-repository` prints `true`/`false`.
- `git worktree list --porcelain` emits `worktree <path>` / `HEAD <sha>` / `branch <ref>` blocks.
- `git status --porcelain` lines starting `??` are untracked; the rest are modifications.
- `git log -1 --format=%cr` gives the relative last-commit date.
- `.git/FETCH_HEAD` mtime is the last successful fetch time.
- **A merged-then-reverted branch IS reported as merged.** Reverting does not remove ancestry, so the merge is a genuine no-op and trees match. This is a documented limitation, not a bug to fix.

## Review Focus

Failure modes the spec implies that are easy to miss. Each has a test pinned to the task that owns the code.

1. **Squash-merged branch** — ancestry says "not merged", tree test says "merged". A `merged` run that uses ancestry silently reports live work as deletable. *(Task 7)*
2. **Stale local `main`** — comparing against a local base 40 commits behind `origin/main` marks a live remote branch as safe to delete. *(Task 5)*
3. **Unpushed work on a same-named local branch** — remote branch is merged, but the user's local copy holds commits that exist nowhere else. Must withhold the safe-to-delete annotation. *(Task 8)*
4. **Shallow clone** — merge base absent locally, `merge-tree` fails in a way resembling a conflict. Must report `unknown`, never `merged`. *(Task 7)*
5. **Unrelated histories** — exit 128 is *unknown*, not *conflicts*. Collapsing non-zero exits into "conflicts" confidently mislabels the repo. *(Task 7)*
6. **Empty `--author`** — git's own `--author=""` matches *every* commit, so an unset identity silently reports all branches as the user's. *(Task 3)*
7. **Whitelisted branch with unpushed commits** — the whitelist must suppress "exists and is fine" locally, not suppress a real problem. *(Task 6)*
8. **Fetch failure** — must analyse against stale local state and say so, not crash and not claim freshness. *(Task 5)*
9. **Nested repository** — a repo inside a repo must not be fetched and reported twice. *(Task 4)*
10. **Piped output** — ANSI colour or box-drawing characters reaching a pipe corrupts the markdown file. *(Task 9)*

---

## File Structure

```
pyproject.toml              packaging, deps, console entry point
git_ferret/__init__.py      version
git_ferret/gitcmd.py        all git subprocess calls; the only module that shells out
git_ferret/config.py        ~/.git-ferret.toml loading, identity resolution, whitelist globbing
git_ferret/discovery.py     recursive repo + worktree detection
git_ferret/models.py        Finding/RepoReport dataclasses shared by analysis and report
git_ferret/analysis/local.py     local branches, worktrees, dirt, stashes
git_ferret/analysis/remote.py    remote branch enumeration with author/branch filters
git_ferret/analysis/merged.py    merge-tree based merged/conflicts/unknown verdicts
git_ferret/report.py        markdown assembly, TTY vs pipe
git_ferret/cli.py           typer app, three subcommands
tests/__init__.py           empty; makes tests.helpers importable
tests/helpers.py            importable git fixture builders (make_repo, commit, run, clone_local)
tests/conftest.py           pytest fixtures wrapping tests.helpers
tests/test_config.py
tests/test_gitcmd.py
tests/test_discovery.py
tests/test_local.py
tests/test_remote.py
tests/test_merged.py
tests/test_report.py
```

`gitcmd.py` is the single seam for git. No other module imports `subprocess`. This is what makes "read-only" auditable in one place.

---

## Task 1: Packaging and git subprocess seam

The foundation. Every later task depends on `run_git` and the repo fixture helpers, so this lands first.

**Files:**
- Create: `pyproject.toml`, `git_ferret/__init__.py`, `git_ferret/gitcmd.py`, `tests/__init__.py`, `tests/helpers.py`, `tests/conftest.py`, `tests/test_gitcmd.py`

**Interfaces:**
- Produces:
  - `run_git(repo: Path, *args: str, check: bool = False) -> CompletedProcess` — never raises on non-zero when `check=False`; returns the process with `.returncode`, `.stdout`, `.stderr` as `str`.
  - `run_git_text(repo: Path, *args: str) -> str` — stdout stripped; raises `GitError` when `returncode != 0`.
  - `git_available() -> bool`
  - `GitError(Exception)` with `.returncode`, `.stderr`.
Test helpers in `tests/conftest.py`. Because every later task imports them, they live in a normal
importable module `tests/helpers.py`, and `conftest.py` re-exports them as fixtures. Add
`tests/__init__.py` (empty) so `from tests.helpers import ...` resolves under pytest's default
import mode.

- `tests/helpers.py` (importable from any test module):
  - `make_repo(path: Path) -> Path` — `git init -b main`, sets local `user.name`/`user.email` to `Dev`/`dev@example.com`.
  - `commit(repo: Path, message: str, author: str = "Dev <dev@example.com>") -> None` — `git add -A`, then commit with explicit `--author` so fixtures can commit as other identities.
  - `run(repo: Path, *args: str) -> subprocess.CompletedProcess` — thin wrapper over `run_git` returning `.stdout` as text.
  - `clone_local(src: Path, dest: Path, depth: int | None = None) -> Path` — file-protocol clone so the remote is real and fetchable; `depth` produces a shallow clone for the shallow test.
- `tests/conftest.py` — fixtures `make_repo`, `clone_local` wrapping the helpers as pytest fixtures. A `make_repo` **fixture** takes no `path` argument and returns a `tmp_path`-based path; tests needing a specific path call the helper directly from `tests.helpers`.

All test modules import helpers as `from tests.helpers import commit, make_repo, run` — never
`from tests.conftest import ...`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gitcmd.py
from pathlib import Path
import pytest
from git_ferret.gitcmd import GitError, run_git, run_git_text, git_available


def test_run_git_returns_stdout(make_repo):
    repo = make_repo(Path("/tmp/x"))          # fixture creates and returns tmp path
    (repo / "f.txt").write_text("a\n")
    run(repo, "add", "-A")
    run(repo, "commit", "-m", "base")
    assert run_git_text(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_run_git_text_raises_on_error(make_repo):
    repo = make_repo(Path("/tmp/x"))
    with pytest.raises(GitError) as exc:
        run_git_text(repo, "rev-parse", "does-not-exist")
    assert exc.value.returncode != 0


def test_run_git_check_false_does_not_raise(make_repo):
    repo = make_repo(Path("/tmp/x"))
    proc = run_git(repo, "rev-parse", "does-not-exist")
    assert proc.returncode != 0


def test_git_available():
    assert git_available() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gitcmd.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'git_ferret'`

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "git-ferret"
version = "0.1.0"
description = "Read-only reporting tool for auditing many local git repositories"
requires-python = ">=3.11"
dependencies = ["typer>=0.12", "rich>=13"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
git-ferret = "git_ferret.cli:main"

[tool.setuptools.packages.find]
include = ["git_ferret*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 4: Implement `gitcmd.py`**

`git_ferret/__init__.py`: set `__version__ = "0.1.0"`.

`git_ferret/gitcmd.py` — every subprocess call in the project goes here:

```python
class GitError(Exception):
    def __init__(self, args: list[str], returncode: int, stderr: str):
        super().__init__(f"git {' '.join(args)} failed ({returncode}): {stderr}")
        self.returncode = returncode
        self.stderr = stderr


def run_git(repo, *args, check=False) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=120,
    )
    if check and proc.returncode != 0:
        raise GitError(list(args), proc.returncode, proc.stderr)
    return proc
```

`run_git_text` calls `run_git(..., check=True)` and returns `proc.stdout.strip()`.
`git_available()` runs `["git", "--version"]` and returns `returncode == 0`.

Always pass `-C <repo>` rather than `cwd`, so no caller can accidentally depend on process state.

- [ ] **Step 5: Create `tests/__init__.py`, `tests/helpers.py`, and `tests/conftest.py`**

`tests/__init__.py` is empty.

`tests/helpers.py` defines the four helpers named in Interfaces. `make_repo` must use
`git init -b main` (not the default branch name) and set local `user.name`/`user.email`, or every
later commit fails with "Author identity unknown" in a container. `commit()` passes `--author`
explicitly so fixtures can commit as other identities. `clone_local` uses the `file://` protocol so
the clone has a genuinely fetchable remote; its `depth` argument produces the shallow clone Task 7
needs.

`tests/conftest.py` exposes `make_repo` and `clone_local` as pytest fixtures over those helpers, so
a test can request `make_repo` and get a fresh repository under `tmp_path`.

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_gitcmd.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml git_ferret tests
git commit -m "feat: add packaging and git subprocess seam"
```

---

## Task 2: Findings and report models

Shared dataclasses. Locking these now prevents the type drift that bites in Task 6-9.

**Files:**
- Create: `git_ferret/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `Status` — `StrEnum`: `OK`, `NOT_PUSHED`, `DIVERGED`, `UPSTREAM_DELETED`, `CONFLICTS`, `UNKNOWN`, `DIRTY`, `DETACHED`, `UNREACHABLE`, `NO_BASE`.
  - `Finding` — frozen dataclass: `branch: str`, `status: Status`, `detail: str = ""`, `emoji: str = ""`, `safe_to_delete: bool = False`.
  - `BranchInfo` — dataclass: `name: str`, `remote: str | None`, `upstream: str | None`, `upstream_gone: bool`, `ahead: int`, `behind: int`, `has_upstream: bool`, `last_commit_rel: str`.
  - `WorktreeInfo` — dataclass: `path: Path`, `branch: str | None`, `is_bare: bool`, `dirty: bool`, `dirty_detail: str`.
  - `RepoReport` — dataclass: `path: Path`, `base_ref: str | None`, `fetched_at: datetime | None`, `fetch_failed: bool`, `skipped_reason: str | None`, `worktrees: list[WorktreeInfo]`, `findings: list[Finding]`, `notes: list[str]`.
  - `MERGED_EXIT = 0`, `CONFLICT_EXIT = 1` module constants.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from pathlib import Path
from git_ferret.models import Finding, RepoReport, Status

def test_finding_defaults():
    f = Finding(branch="feat/x", status=Status.NOT_PUSHED)
    assert f.detail == "" and f.emoji == "" and f.safe_to_delete is False

def test_repo_report_is_empty_by_default():
    r = RepoReport(path=Path("/tmp/r"))
    assert r.findings == [] and r.worktrees == [] and r.skipped_reason is None

def test_status_values_are_exactly_the_documented_set():
    assert {s.name for s in Status} == {
        "OK", "NOT_PUSHED", "DIVERGED", "UPSTREAM_DELETED", "CONFLICTS",
        "UNKNOWN", "DIRTY", "DETACHED", "UNREACHABLE", "NO_BASE",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'git_ferret.models'`

- [ ] **Step 3: Implement `models.py`**

`Status(StrEnum)` with the ten members above and a `MARKER` mapping to the bracketed strings from the spec (`[NOT PUSHED]`, `[DIVERGED]`, `[UPSTREAM DELETED]`, `[CONFLICTS]`, `[UNKNOWN]`, `[DIRTY]`, `[DETACHED]`, `[UNREACHABLE]`, `[OK]`, `[NO BASE]`).

Use `StrEnum` (3.11+) so markers and status compare as strings without conversion at format time.

`RepoReport` gets `property needs_base(self) -> bool` returning `base_ref is None` — Task 6 and 8 both branch on it.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add git_ferret/models.py tests/test_models.py
git commit -m "feat: add shared finding and report models"
```

---

## Task 3: Config, identity resolution, whitelist

Identity precedence is the spec's `--author` > TOML > `git config`, with the empty-match trap called out in Review Focus.

**Files:**
- Create: `git_ferret/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `run_git_text`, `run_git` from Task 1.
- Produces:
  - `Config.load(path: Path | None = None) -> Config` — defaults to `Path.home() / ".git-ferret.toml"`.
  - `Config.author: list[str]`, `Config.whitelist: list[str]`.
  - `Config.is_whitelisted(branch: str) -> bool` — `fnmatch.fnmatchcase`, so `*` and `?` work and matching is case-sensitive.
  - `Config.is_excluded(branch: str, base_ref: str | None) -> bool` — true for `main`, `master`, the base ref, or any whitelist match.
  - `resolve_identities(cli_author: list[str], config: Config, repo: Path) -> list[str]` — precedence per the spec.
  - `identity_matches(commits_author_strings: Iterable[str], identities: list[str]) -> bool` — any-identity, any-commit, OR semantics.
  - `DEFAULT_CONFIG_PATH: Path`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from pathlib import Path
from git_ferret.config import Config, identity_matches, resolve_identities


def test_whitelist_supports_glob_star():
    c = Config(whitelist=["release/*"])
    assert c.is_whitelisted("release/2.1") is True
    assert c.is_whitelisted("feat/release") is False


def test_whitelist_supports_glob_question_mark():
    c = Config(whitelist=["hotfix-?"])
    assert c.is_whitelisted("hotfix-1") is True
    assert c.is_whitelisted("hotfix-10") is False


def test_excluded_always_covers_main_and_master():
    c = Config(whitelist=[])
    assert c.is_excluded("main", None) is True
    assert c.is_excluded("master", None) is True
    assert c.is_excluded("feat/x", "origin/main") is False


def test_identity_precedence_cli_beats_toml(make_repo):
    repo = make_repo(Path("/tmp/x"))
    cfg = Config(author=["toml@example.com"])
    got = resolve_identities(["cli@example.com"], cfg, repo)
    assert got == ["cli@example.com"]


def test_identity_precedence_toml_beats_git_config(make_repo):
    repo = make_repo(Path("/tmp/x"))   # git config is Dev <dev@example.com>
    cfg = Config(author=["toml@example.com"])
    assert resolve_identities([], cfg, repo) == ["toml@example.com"]


def test_identity_falls_back_to_git_config(make_repo):
    repo = make_repo(Path("/tmp/x"))
    assert resolve_identities([], Config(author=[]), repo) == ["Dev <dev@example.com>"]


def test_empty_identity_never_matches_everything():
    # Review Focus #6: an unset identity must not match every commit.
    assert identity_matches(["Dev <dev@example.com>"], []) is False


def test_identity_matches_any_commit_any_identity():
    commits = ["Someone Else <x@y.com>", "Fran <fran@z.com>"]
    assert identity_matches(commits, ["fran@z.com"]) is True
    assert identity_matches(commits, ["nobody@nowhere"]) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'git_ferret.config'`

- [ ] **Step 3: Implement `config.py`**

`Config.load` reads TOML via `tomllib.loads(path.read_text())`. A missing file is not an error — return an empty `Config`. `author` accepts either a bare string or a list; normalise both to `list[str]`.

`resolve_identities`:
1. non-empty `cli_author` → return it
2. non-empty `config.author` → return it
3. else read `user.name` and `user.email` **per repository** via `run_git(repo, "config", "--get", ...)` with `check=False`; return `[f"{name} <{email}>"]` if both resolve, else `[]`

`identity_matches` returns `False` immediately when `identities` is empty. Never pass an empty string to git's `--author`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add git_ferret/config.py tests/test_config.py
git commit -m "feat: add config loading, identity resolution, and whitelist globbing"
```

---

## Task 4: Repository and worktree discovery

Nested-repo handling is Review Focus #9 and needs a test here, not in the CLI.

**Files:**
- Create: `git_ferret/discovery.py`
- Test: `tests/test_discovery.py`

**Interfaces:**
- Consumes: `run_git_text` from Task 1.
- Produces:
  - `is_git_dir(path: Path) -> bool`
  - `discover_repos(root: Path) -> list[Path]` — sorted, stops descending at a repo, skips `.git` internals.
  - `is_submodule(path: Path) -> bool` — true when `.git` is a file whose target lives under `.git/modules/`.
  - `is_bare(repo: Path) -> bool`
  - `list_worktrees(repo: Path) -> list[tuple[Path, str | None]]` — `(worktree_path, branch_or_None)`; `None` for detached HEAD. Uses `git worktree list --porcelain`.
  - `list_linked_worktree_dirs(repo: Path) -> list[Path]` — secondary worktrees only, excluding the main one.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_discovery.py
from pathlib import Path
from git_ferret.discovery import discover_repos, is_bare, is_submodule, list_worktrees


def test_discovers_recursively(tmp_path, make_repo):
    make_repo(tmp_path / "a")
    make_repo(tmp_path / "nested" / "b")
    found = discover_repos(tmp_path)
    assert {p.name for p in found} == {"a", "b"}


def test_does_not_descend_into_a_repo(make_repo, tmp_path):
    # Review Focus #9: nested repo must not be reported separately.
    outer = make_repo(tmp_path / "outer")
    make_repo(outer / "vendor" / "inner")
    assert discover_repos(tmp_path) == [outer]


def test_ignores_non_repo_dirs(tmp_path):
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "file.txt").write_text("x")
    assert discover_repos(tmp_path) == []


def test_is_bare_false_for_normal_repo(make_repo, tmp_path):
    assert is_bare(make_repo(tmp_path / "r")) is False


def test_is_submodule_false_for_real_repo(make_repo, tmp_path):
    assert is_submodule(make_repo(tmp_path / "r")) is False


def test_list_worktrees_returns_main_with_branch(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r")
    wts = list_worktrees(repo)
    assert len(wts) == 1
    assert wts[0][1] == "main"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'git_ferret.discovery'`

- [ ] **Step 3: Implement `discovery.py`**

`discover_repos` walks with `os.walk`, prunes `dirnames` in place: remove `.git` always, and remove any directory that `is_git_dir` reports as a repository **before** recursing into it. Return sorted paths so output is deterministic across runs.

`is_git_dir`: `.git` is a directory (real repo) or `.git` is a file (worktree/submodule); exclude submodules via `is_submodule`.

`is_submodule`: read the `.git` file, resolve its `gitdir:` path, return True when it contains `/.git/modules/` or starts with a `.git/modules/` prefix.

`list_worktrees`: parse `git worktree list --porcelain` in blocks. A block has `worktree <path>`; `branch refs/heads/<name>` when attached, absent when detached → `None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_discovery.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add git_ferret/discovery.py tests/test_discovery.py
git commit -m "feat: add repository and worktree discovery"
```

---

## Task 5: Fetch, base ref resolution, staleness

Owns Review Focus #2 (stale local main) and #8 (fetch failure).

**Files:**
- Create: `git_ferret/gitops.py` — fetch, base resolution, tree hashes, ahead/behind, gone detection
- Test: `tests/test_gitops.py`

**Interfaces:**
- Consumes: `run_git`, `run_git_text` (Task 1); `Config` (Task 3); `BranchInfo`, `RepoReport` (Task 2).
- Produces:
  - `fetch_all(repo: Path) -> tuple[bool, Path | None]` — `(success, fetched_at)`. Runs `git fetch --all --prune` with `check=False`. On success returns `repo/.git/FETCH_HEAD` mtime as a `datetime`; on failure returns `(False, None)` and never raises.
  - `fetch_head_mtime(repo: Path) -> datetime | None`
  - `resolve_base_ref(repo: Path) -> str | None` — `origin/main`, else `origin/master`, else local `main`, else local `master`, else `None`. Returns the full ref name as git knows it (`origin/main` or `main`).
  - `base_ref_is_remote(base_ref: str | None) -> bool`
  - `tree_hash(repo: Path, ref: str) -> str | None`
  - `list_branches(repo: Path) -> list[BranchInfo]` — local branches via `for-each-ref`, with `upstream_gone` from the `[origin/x: gone]` marker and `ahead`/`behind` from `rev-list --left-right --count`.
  - `is_shallow(repo: Path) -> bool`
  - `stash_entries(repo: Path) -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gitops.py
from pathlib import Path
from git_ferret.gitops import (
    fetch_all, is_shallow, list_branches, resolve_base_ref, tree_hash,
)


def test_resolve_base_ref_prefers_origin(make_repo, clone_local, tmp_path):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    clone = clone_local(src, tmp_path / "clone")
    assert resolve_base_ref(clone) == "origin/main"


def test_resolve_base_ref_falls_back_to_local(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r")
    assert resolve_base_ref(repo) == "main"


def test_resolve_base_ref_none_when_absent(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "f.txt").write_text("a\n")
    commit(repo, "base")
    run = __import__("git_ferret.gitcmd", fromlist=["run_git"])
    run.run(repo, "branch", "-m", "main", "trunk")
    assert resolve_base_ref(repo) is None


def test_fetch_failure_is_not_fatal(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r")
    run(repo, "remote", "add", "origin", "/nonexistent/path.git")
    ok, when = fetch_all(repo)
    assert ok is False and when is None


def test_stale_local_main_would_be_wrong_but_origin_is_used(make_repo, clone_local, tmp_path):
    # Review Focus #2: the base must be origin/main, never a stale local main.
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n")
    commit(src, "base")
    clone = clone_local(src, tmp_path / "clone")
    run_git(src, "fetch")            # src advances; clone's local main is now stale
    fetch_all(clone)
    assert resolve_base_ref(clone) == "origin/main"
    assert tree_hash(clone, "origin/main") != tree_hash(clone, "main")


def test_list_branches_marks_deleted_upstream(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "f.txt").write_text("a\n")
    commit(repo, "base")
    run(repo, "branch", "gone-branch")
    run(repo, "update-ref", "refs/remotes/origin/keep", "main")
    run(repo, "config", "branch.gone-branch.remote", "origin")
    run(repo, "config", "branch.gone-branch.merge", "refs/heads/gone-branch")
    run(repo, "update-ref", "-d", "refs/remotes/origin/keep")
    branches = {b.name: b for b in list_branches(repo)}
    assert branches["gone-branch"].upstream_gone is True


def test_is_shallow_false_for_full_clone(make_repo, tmp_path):
    assert is_shallow(make_repo(tmp_path / "r")) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gitops.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'git_ferret.gitops'`

- [ ] **Step 3: Implement `gitops.py`**

`resolve_base_ref` tries each candidate with `git rev-parse --verify --quiet <cand>` and returns the first that resolves. Order is `origin/main`, `origin/master`, `main`, `master`. Never guess `develop` or `trunk` — `None` means skip with a warning.

`list_branches`: `git for-each-ref --format='%(refname:short)|%(upstream:short)|%(upstream:track)' refs/heads/`. The `%(upstream:track)` value contains `[gone]` when the upstream ref is missing — map that to `upstream_gone=True`. Ahead/behind come from `git rev-list --left-right --count <branch>...<upstream>` when an upstream resolves; both are 0 when it does not. Last commit via `git log -1 --format=%cr <branch>`.

`fetch_all` must never raise. Capture `FETCH_HEAD` mtime only on success.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gitops.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add git_ferret/gitops.py tests/test_gitops.py
git commit -m "feat: add fetch, base ref resolution, and branch listing"
```

---

## Task 6: `local` analysis

The largest task. Worktree state plus per-branch status, with the asymmetric whitelist rule.

**Files:**
- Create: `git_ferret/analysis/__init__.py`, `git_ferret/analysis/local.py`
- Test: `tests/test_local.py`

**Interfaces:**
- Consumes: `gitops` (Task 5), `list_worktrees` (Task 4), `Config` (Task 3), models (Task 2).
- Produces:
  - `analyse_local(repo: Path, config: Config, base_ref: str | None, do_fetch: bool) -> RepoReport`
  - `worktree_status(repo: Path, worktree: Path) -> tuple[bool, str]` — `(dirty, detail)`
  - `branch_finding(branch: BranchInfo, config: Config) -> Finding | None` — primary status by precedence: upstream deleted > diverged > not pushed.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_local.py
from pathlib import Path
from git_ferret.analysis.local import analyse_local
from git_ferret.config import Config
from git_ferret.models import Status
from git_ferret.gitops import resolve_base_ref
from tests.helpers import commit, make_repo, run


def _report(repo, config=None, do_fetch=False):
    return analyse_local(repo, config or Config(), resolve_base_ref(repo), do_fetch)


def test_reports_clean_branch_as_ok(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "f.txt").write_text("a\n"); commit(repo, "base")
    run(repo, "branch", "clean-branch")
    rep = _report(repo)
    f = next(x for x in rep.findings if x.branch == "clean-branch")
    assert f.status is Status.OK


def test_whitelisted_clean_branch_is_hidden(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "f.txt").write_text("a\n"); commit(repo, "base")
    run(repo, "branch", "release/1.0")
    rep = _report(repo, Config(whitelist=["release/*"]))
    assert "release/1.0" not in {f.branch for f in rep.findings}


def test_whitelisted_dirty_branch_is_still_reported(make_repo, tmp_path):
    # Review Focus #7: whitelist must not suppress a real problem.
    repo = make_repo(tmp_path / "r")
    (repo / "f.txt").write_text("a\n"); commit(repo, "base")
    run(repo, "branch", "release/1.0")
    run(repo, "checkout", "release/1.0")
    (repo / "f.txt").write_text("dirty\n")
    rep = _report(repo, Config(whitelist=["release/*"]))
    names = {f.branch for f in rep.findings} | {f.status.value for f in rep.findings}
    assert Status.DIRTY.value in names or "release/1.0" in {f.branch for f in rep.findings}


def test_reports_dirty_worktree(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "f.txt").write_text("a\n"); commit(repo, "base")
    (repo / "untracked.txt").write_text("x\n")
    rep = _report(repo)
    assert any(f.status is Status.DIRTY for f in rep.findings)


def test_reports_unpushed_commits(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "f.txt").write_text("a\n"); commit(repo, "base")
    run(repo, "checkout", "-b", "feat")
    (repo / "f.txt").write_text("a\nb\n"); commit(repo, "work")
    rep = _report(repo)
    f = next(x for x in rep.findings if x.branch == "feat")
    assert f.status is Status.NOT_PUSHED


def test_reports_detached_head(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "f.txt").write_text("a\n"); commit(repo, "base")
    sha = run(repo, "rev-parse", "HEAD").stdout.strip()
    run(repo, "checkout", sha)
    rep = _report(repo)
    assert any(f.status is Status.DETACHED for f in rep.findings)


def test_no_base_branch_skips_with_reason(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / "f.txt").write_text("a\n"); commit(repo, "base")
    run(repo, "branch", "-m", "main", "trunk")
    rep = analyse_local(repo, Config(), None, False)
    assert rep.skipped_reason is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_local.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'git_ferret.analysis.local'`

- [ ] **Step 3: Implement `analysis/local.py`**

Fetch first when `do_fetch` is true, recording success and `FETCH_HEAD` mtime on the report. Resolve worktrees via `list_worktrees`. For each worktree run `git status --porcelain`; non-empty means dirty, and untracked (`??`) files are counted separately in the detail string.

Per branch, precedence for the single primary status: `UPSTREAM_DELETED` > `DIVERGED` (>0 ahead and >0 behind) > `NOT_PUSHED` (no upstream, or ahead>0) > `OK`.

**Whitelist asymmetry:** a whitelisted branch with status `OK` is omitted entirely; a whitelisted branch with any other status is still included. Non-whitelisted branches are always listed, including `OK` ones — the report is a census.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_local.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add git_ferret/analysis tests/test_local.py
git commit -m "feat: add local branch and worktree analysis"
```

---

## Task 7: Merge test and `merged` analysis

The technically hardest part, and where the verified exit codes earn their keep. Owns Review Focus #1, #4, #5.

**Files:**
- Create: `git_ferret/analysis/merged.py`
- Test: `tests/test_merged.py`

**Interfaces:**
- Consumes: `gitops` (Task 5), `Config` (Task 3), models (Task 2).
- Produces:
  - `MergeVerdict` — `StrEnum`: `MERGED`, `NOT_MERGED`, `CONFLICTS`, `UNKNOWN`.
  - `merge_verdict(repo: Path, base_ref: str, branch_ref: str) -> MergeVerdict`
  - `remote_branches(repo: Path) -> list[str]` — `origin/...` style refs across all remotes.
  - `commits_in_range(repo: Path, base_ref: str, branch_ref: str) -> list[str]` — `%an <%ae>` for `base_ref..branch_ref`.
  - `analyse_merged(repo: Path, config: Config, base_ref: str | None, identities: list[str], branch_filter: str | None, do_fetch: bool) -> RepoReport`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_merged.py
from pathlib import Path
from git_ferret.analysis.merged import MergeVerdict, merge_verdict, remote_branches
from git_ferret.gitops import is_shallow
from tests.helpers import clone_local, commit, make_repo, run


def _clone_with_feature(tmp_path):
    """src: main + feature branch. clone: both refs visible as remote branches."""
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\nb\nc\n")
    commit(src, "base")
    run(src, "checkout", "-b", "feature")
    (src / "f.txt").write_text("a\nFEATURE\nc\n")
    commit(src, "feature work")
    run(src, "checkout", "main")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch", "--all", "--prune")
    return src, clone


def test_squash_merged_branch_is_merged(tmp_path):
    # Review Focus #1: ancestry lies here; the tree test does not.
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\nb\nc\n"); commit(src, "base")
    run(src, "checkout", "-b", "feature")
    (src / "f.txt").write_text("a\nFEATURE\nc\n"); commit(src, "feature work")
    run(src, "checkout", "main")
    run(src, "merge", "--squash", "feature")
    commit(src, "squash merge")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch", "--all", "--prune")
    # ancestry says NOT merged:
    assert run(clone, "branch", "-r", "--merged", "origin/main").stdout.strip() == ""
    # tree test says merged:
    assert merge_verdict(clone, "origin/main", "origin/feature") is MergeVerdict.MERGED


def test_unmerged_branch_is_not_merged(tmp_path):
    src, clone = _clone_with_feature(tmp_path)
    assert merge_verdict(clone, "origin/main", "origin/feature") is MergeVerdict.NOT_MERGED


def test_conflicting_branch_reports_conflicts(tmp_path):
    # Review Focus #5: exit 1 is conflicts, and must differ from unknown.
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\nb\nc\n"); commit(src, "base")
    run(src, "checkout", "-b", "conflict")
    (src / "f.txt").write_text("SIDE-A\nb\nc\n"); commit(src, "side a")
    run(src, "checkout", "main")
    (src / "f.txt").write_text("SIDE-B\nb\nc\n"); commit(src, "side b")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch", "--all", "--prune")
    assert merge_verdict(clone, "origin/main", "origin/conflict") is MergeVerdict.CONFLICTS


def test_unrelated_histories_report_unknown_not_conflicts(tmp_path):
    # Review Focus #5: exit 128 is unknown.
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n"); commit(src, "base")
    run(src, "checkout", "--orphan", "orphan")
    (src / "f.txt").write_text("zzz\n")
    run(src, "add", "-A"); commit(src, "orphan")
    run(src, "checkout", "main")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch", "--all", "--prune")
    assert merge_verdict(clone, "origin/main", "origin/orphan") is MergeVerdict.UNKNOWN


def test_shallow_repo_is_unknown_not_merged(tmp_path):
    # Review Focus #4: no merge base locally must never read as merged.
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n"); commit(src, "base")
    run(src, "checkout", "-b", "feature")
    (src / "f.txt").write_text("a\nb\n"); commit(src, "feature")
    run(src, "checkout", "main")
    clone = clone_local(src, tmp_path / "shallow", depth=1)
    assert is_shallow(clone) is True
    assert merge_verdict(clone, "origin/main", "origin/feature") is not MergeVerdict.MERGED


def test_remote_branches_lists_origin_refs(tmp_path):
    src, clone = _clone_with_feature(tmp_path)
    refs = remote_branches(clone)
    assert "origin/feature" in refs
    assert "origin/main" in refs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_merged.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'git_ferret.analysis.merged'`

- [ ] **Step 3: Implement `analysis/merged.py`**

`merge_verdict` — the core, exactly as verified:

```python
def merge_verdict(repo: Path, base_ref: str, branch_ref: str) -> MergeVerdict:
    proc = run_git(repo, "merge-tree", "--write-tree", base_ref, branch_ref, check=False)
    if proc.returncode == 0:
        result_tree = proc.stdout.strip().split("\n")[0].strip()
        base_tree = tree_hash(repo, base_ref)
        return MergeVerdict.MERGED if result_tree == base_tree else MergeVerdict.NOT_MERGED
    if proc.returncode == CONFLICT_EXIT:
        return MergeVerdict.CONFLICTS
    return MergeVerdict.UNKNOWN   # 128: refusing to merge unrelated histories
```

Check `is_shallow(repo)` before any merge test and return `UNKNOWN` for every branch when true — that is the design's explicit pre-check. Per-branch unknown still takes precedence over the repo-wide flag in the output.

`remote_branches` uses `for-each-ref --format='%(refname:short)' refs/remotes/` and drops `origin/HEAD`.

`commits_in_range` runs `git log --format=%an <%ae> <base_ref>..<branch_ref>`. **The range is essential** — scanning full reachability would match nearly every branch, because the user's commits sit in the base branch's history and are therefore reachable from all of them.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_merged.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add git_ferret/analysis/merged.py tests/test_merged.py
git commit -m "feat: add tree-based merge verdict analysis"
```

---

## Task 8: `remote` analysis and safe-to-delete gating

Owns Review Focus #3 — the gate that stops the tool recommending deletion of a branch with unpushed local work.

**Files:**
- Create: `git_ferret/analysis/remote.py`
- Test: `tests/test_remote.py`

**Interfaces:**
- Consumes: `merged` (Task 7), `gitops` (Task 5), `Config` (Task 3), models (Task 2).
- Produces:
  - `local_branch_unpushed(repo: Path, branch: str) -> bool` — true when a local branch of this name has unpushed commits or a gone upstream.
  - `analyse_remote(repo, config, base_ref, identities, branch_filter, do_fetch) -> RepoReport`
  - `analyse_merged_candidates(repo, config, base_ref, identities, branch_filter, do_fetch) -> RepoReport` — the `merged` subcommand.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_remote.py
from pathlib import Path
from git_ferret.analysis.merged import analyse_merged_candidates
from git_ferret.analysis.remote import analyse_remote, local_branch_unpushed
from git_ferret.config import Config
from git_ferret.models import Status
from git_ferret.gitops import resolve_base_ref
from tests.helpers import clone_local, commit, make_repo, run


def test_remote_filters_by_branch_name(tmp_path, make_repo):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n"); commit(src, "base")
    run(src, "checkout", "-b", "ABC-123-add-thing"); commit(src, "w")
    run(src, "checkout", "main")
    run(src, "branch", "ZZZ-other")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch", "--all", "--prune")
    rep = analyse_remote(clone, Config(), resolve_base_ref(clone), [], "ABC-123", False)
    names = {f.branch for f in rep.findings}
    assert "origin/ABC-123-add-thing" in names
    assert "origin/ZZZ-other" not in names


def test_remote_filters_by_author(tmp_path, make_repo):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n"); commit(src, "base")
    run(src, "checkout", "-b", "by-fran")
    (src / "f.txt").write_text("fran\n"); commit(src, "w", author="Fran <fran@z.com>")
    run(src, "checkout", "main")
    run(src, "checkout", "-b", "by-someone")
    (src / "f.txt").write_text("other\n"); commit(src, "w", author="Other <o@x.com>")
    run(src, "checkout", "main")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch", "--all", "--prune")
    rep = analyse_remote(clone, Config(), resolve_base_ref(clone), ["fran@z.com"], None, False)
    names = {f.branch for f in rep.findings}
    assert "origin/by-fran" in names and "origin/by-someone" not in names


def test_remote_ignores_whitelisted_branches(tmp_path, make_repo):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n"); commit(src, "base")
    run(src, "checkout", "-b", "release/9"); commit(src, "w")
    run(src, "checkout", "main")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch", "--all", "--prune")
    rep = analyse_remote(clone, Config(whitelist=["release/*"]), resolve_base_ref(clone), [], None, False)
    assert "origin/release/9" not in {f.branch for f in rep.findings}


def test_merged_branch_annotated_safe_when_no_local_unpushed(tmp_path, make_repo):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\nb\n"); commit(src, "base")
    run(src, "checkout", "-b", "done"); (src / "f.txt").write_text("a\nDONE\n"); commit(src, "w")
    run(src, "checkout", "main"); run(src, "merge", "--squash", "done"); commit(src, "merge")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch", "--all", "--prune")
    rep = analyse_merged_candidates(clone, Config(), resolve_base_ref(clone), [], None, False)
    f = next(x for x in rep.findings if x.branch == "origin/done")
    assert f.safe_to_delete is True


def test_merged_branch_withheld_when_local_unpushed(tmp_path, make_repo):
    # Review Focus #3: the gate that matters most.
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\nb\n"); commit(src, "base")
    run(src, "checkout", "-b", "done"); (src / "f.txt").write_text("a\nDONE\n"); commit(src, "w")
    run(src, "checkout", "main"); run(src, "merge", "--squash", "done"); commit(src, "merge")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch", "--all", "--prune")
    # local work exists that is nowhere on the remote
    run(clone, "checkout", "-b", "done")
    (clone / "local-only.txt").write_text("mine\n")
    run(clone, "add", "-A"); commit(clone, "local only")
    assert local_branch_unpushed(clone, "done") is True
    rep = analyse_merged_candidates(clone, Config(), resolve_base_ref(clone), [], None, False)
    f = next(x for x in rep.findings if x.branch == "origin/done")
    assert f.safe_to_delete is False


def test_empty_identities_lists_all_remote_branches(tmp_path, make_repo):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n"); commit(src, "base")
    run(src, "checkout", "-b", "someone-elses"); commit(src, "w")
    run(src, "checkout", "main")
    clone = clone_local(src, tmp_path / "clone")
    run(clone, "fetch", "--all", "--prune")
    rep = analyse_remote(clone, Config(), resolve_base_ref(clone), [], None, False)
    assert "origin/someone-elses" in {f.branch for f in rep.findings}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_remote.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'git_ferret.analysis.remote'`

- [ ] **Step 3: Implement `analysis/remote.py`**

`local_branch_unpushed`: `git rev-parse --verify --quiet refs/heads/<name>`; if absent return `False`. Then reuse `list_branches` and treat `ahead > 0` or `upstream_gone` as unpushed.

`analyse_remote`: enumerate `remote_branches`, drop the base ref, `main`, `master`, and whitelist matches **entirely**. When `identities` is non-empty, keep a branch only if some commit in `base..branch` matches (via `identity_matches` from Task 3). When `branch_filter` is set, keep only names containing it. Both filters combine with AND.

`analyse_merged_candidates`: same enumeration and filters, then `merge_verdict` per branch. Attach `safe_to_delete=True` only when the verdict is `MERGED` **and** `local_branch_unpushed(repo, branch_name_without_remote_prefix)` is `False`. Set `status=UNKNOWN` for `UNKNOWN` and `CONFLICTS` verdicts — never `MERGED`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_remote.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add git_ferret/analysis/remote.py tests/test_remote.py
git commit -m "feat: add remote branch filtering and safe-to-delete gating"
```

---

## Task 9: Markdown report and CLI

Owns Review Focus #10 (piped output must be clean markdown).

**Files:**
- Create: `git_ferret/report.py`, `git_ferret/cli.py`
- Test: `tests/test_report.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: models (Task 2), `Config` (Task 3), discovery (Task 4), analyses (Tasks 6-8).
- Produces:
  - `render_report(reports: list[RepoReport], stale_count: int) -> str` — pure markdown text, no ANSI.
  - `emit(text: str, stream=None) -> None` — renders through `rich.markdown.Markdown` only when `stream.isatty()`; writes raw text otherwise.
  - `main() -> None` — typer entry point.
  - `build_parser() -> typer.Typer` — three subcommands: `local`, `remote`, `merged`, each with `path`, `--no-fetch`, `--author`, `--branch`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report.py
from datetime import datetime
from pathlib import Path
from git_ferret.models import Finding, RepoReport, Status
from git_ferret.report import render_report


def test_render_includes_repo_heading_and_findings():
    rep = RepoReport(path=Path("/tmp/api"), base_ref="origin/main")
    rep.findings.append(Finding(branch="feat/x", status=Status.NOT_PUSHED,
                                detail="3 commits ahead"))
    out = render_report([rep], stale_count=0)
    assert "## /tmp/api" in out
    assert "[NOT PUSHED]" in out
    assert "feat/x" in out


def test_render_uses_absolute_fetch_timestamp():
    rep = RepoReport(path=Path("/tmp/api"), base_ref="origin/main",
                     fetched_at=datetime(2026, 10, 16, 9, 41, 2))
    out = render_report([rep], stale_count=0)
    assert "2026-10-16" in out


def test_render_contains_no_ansi_escapes():
    rep = RepoReport(path=Path("/tmp/api"), base_ref="origin/main")
    rep.findings.append(Finding(branch="b", status=Status.OK))
    out = render_report([rep], stale_count=0)
    assert "\x1b[" not in out


def test_render_emits_stale_warning_once():
    out = render_report([RepoReport(path=Path("/tmp/a"), base_ref="origin/main")], stale_count=2)
    assert "stale" in out.lower()
    assert out.count("stale") == 1


def test_render_has_no_markdown_tables():
    rep = RepoReport(path=Path("/tmp/api"), base_ref="origin/main")
    rep.findings.append(Finding(branch="b", status=Status.OK))
    out = render_report([rep], stale_count=0)
    assert "|" not in out


def test_marker_survives_without_emoji():
    # Decorative-only rule: dropping emoji must not lose the status.
    rep = RepoReport(path=Path("/tmp/api"), base_ref="origin/main")
    rep.findings.append(Finding(branch="b", status=Status.CONFLICTS, emoji="❌"))
    out = render_report([rep], stale_count=0)
    assert "[CONFLICTS]" in out
```

```python
# tests/test_cli.py
from typer.testing import CliRunner
from git_ferret.cli import build_parser

runner = CliRunner()


def test_local_help_lists_no_fetch():
    res = runner.invoke(build_parser(), ["local", "--help"])
    assert res.exit_code == 0
    assert "--no-fetch" in res.stdout


def test_has_three_subcommands():
    res = runner.invoke(build_parser(), ["--help"])
    for cmd in ("local", "remote", "merged"):
        assert cmd in res.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_report.py tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'git_ferret.report'`

- [ ] **Step 3: Implement `report.py`**

`render_report` is pure text and emits no escape codes. Structure:

```
# git-ferret

> ⚠️  N of M repositories analysed from stale local state (no fetch this run)

## <path>
fetched <YYYY-MM-DD HH:MM:SS> · base: origin/main

- ⚠️ `branch` — [NOT PUSHED] 3 commits ahead · last commit 4 months ago
```

When `skipped_reason` is set, render it in place of the base line. `stale_count > 0` emits the blockquote warning exactly once at the top. Status markers come from `Status.MARKER`; emoji from `Finding.emoji` are appended but never replace the marker.

`emit` checks `stream.isatty()`; TTY renders `rich.markdown.Markdown(text)`, otherwise `stream.write(text)`.

- [ ] **Step 4: Implement `cli.py`**

Three subcommands, each: resolve `path` (default `"."`), resolve the base ref, optionally fetch, run the matching analysis, then `emit`. `--update` on `local` only, default off, and it must be the sole code path that writes: for each clean worktree with an attached branch, run `git merge --ff-only`, recording a skip reason for dirty trees, detached HEAD, missing upstream, non-fast-forward, and branches checked out in another worktree.

Print progress to **stderr** so stdout stays pure markdown when piped.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/ -v`
Expected: all passed

- [ ] **Step 6: Verify piped output is clean markdown**

Run: `git-ferret local . --no-fetch > /tmp/out.md` then `grep -c $'\x1b' /tmp/out.md; cat /tmp/out.md`
Expected: grep count is 0; file starts with `# git-ferret`

- [ ] **Step 7: Commit**

```bash
git add git_ferret/report.py git_ferret/cli.py tests
git commit -m "feat: add markdown report and CLI entry point"
```

---

## Task 10: End-to-end smoke test against real repositories

Proves the three subcommands run against a realistic multi-repo tree and emit valid markdown.

**Files:**
- Create: `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: `build_parser` (Task 9) and every layer beneath it.
- Produces: no new code. Verifies integration only.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_end_to_end.py
from typer.testing import CliRunner
from git_ferret.cli import build_parser
from tests.helpers import clone_local, commit, make_repo, run

runner = CliRunner()


def _tree(tmp_path, make_repo):
    src = make_repo(tmp_path / "src")
    (src / "f.txt").write_text("a\n"); commit(src, "base")
    run(src, "checkout", "-b", "stale-work"); (src / "f.txt").write_text("a\ns\n"); commit(src, "w")
    run(src, "checkout", "main")
    clone = clone_local(src, tmp_path / "project")
    run(clone, "fetch", "--all", "--prune")
    make_repo(tmp_path / "solo")           # repo with no remote at all
    return tmp_path


def test_local_end_to_end_emits_valid_markdown(tmp_path, make_repo):
    root = _tree(tmp_path, make_repo)
    res = runner.invoke(build_parser(), ["local", str(root), "--no-fetch"])
    assert res.exit_code == 0, res.output
    assert res.stdout.startswith("# git-ferret")
    assert "\x1b[" not in res.stdout


def test_all_three_subcommands_succeed(tmp_path, make_repo):
    root = _tree(tmp_path, make_repo)
    for cmd in ("local", "remote", "merged"):
        res = runner.invoke(build_parser(), [cmd, str(root), "--no-fetch"])
        assert res.exit_code == 0, f"{cmd} failed: {res.output}"


def test_reports_every_discovered_repo(tmp_path, make_repo):
    root = _tree(tmp_path, make_repo)
    res = runner.invoke(build_parser(), ["local", str(root), "--no-fetch"])
    assert "project" in res.stdout
    assert "solo" in res.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_end_to_end.py -v`
Expected: FAIL or error, revealing whichever layer is not yet wired end to end.

- [ ] **Step 3: Fix whatever the test exposes**

Resolve only the wiring gaps the test names. Do not add features here.

- [ ] **Step 4: Run full suite**

Run: `pytest tests/ -v`
Expected: all passed

- [ ] **Step 5: Manual verification against the real repo collection**

Run: `git-ferret local ~/fran-Repositories --no-fetch | head -40`
Expected: one heading per discovered repository, markers on every branch line, no tracebacks.

- [ ] **Step 6: Commit**

```bash
git add tests/test_end_to_end.py
git commit -m "test: add end-to-end coverage across all subcommands"
```

---

## Self-Review Notes

**Spec coverage.** Intent/why → Global Constraints. Goals and anti-goals → the report-only constraint and Review Focus #1/#3. Commands → Tasks 6-9. Fetching → Task 5. Identity → Task 3. Discovery → Task 4. Base branch → Task 5. `local` → Task 6. `remote` → Task 8. `merged` and the merge test → Task 7. Known limitation → recorded in Global Constraints, not implemented. Shallow → Task 7. Unreachable remotes → Task 5 (`fetch_failed`) and Task 9 (`[UNREACHABLE]`). Updating → Task 9. Output → Task 9. Config → Task 3. Libraries → Task 1.

**Not implemented, by design:** IntelliJ shelves (explicitly out of scope), parallelism (out of scope), commitlint enforcement (deferred).

**Type consistency.** `Finding.branch` carries the *display* ref — a local name for `local`, `origin/x` for `remote`/`merged`. `merge_verdict` takes full refs; `analyse_*` strip the remote prefix only when calling `local_branch_unpushed`. `Status` members are referenced by identity (`is Status.OK`) everywhere, never by string comparison.
