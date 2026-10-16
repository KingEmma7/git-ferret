# git-wombat

Audit many local git repositories at once, and find the branches you can delete.

Keep twenty or thirty repositories in one directory and the state rots quietly: branches pushed
long ago and forgotten, upstream branches deleted server-side, work abandoned on a branch from four
months back. Nothing surfaces it. Auditing repo by repo is too slow to do regularly.

git-wombat answers *what is lying around, and what is safe to remove?* — and never removes anything
itself. You delete; it reports and explains.

```console
$ git-wombat merged ~/src
```

## It will not delete anything

The only write this tool performs is a fast-forward, and only when you explicitly ask:

```console
$ git-wombat local ~/src --update
```

Without that flag, every command is read-only. There is no code path that deletes, stashes,
commits, resets, or rewrites anything — you can audit a directory of real repositories without
touching them.

## Install

```console
$ pip install -e .
```

Needs Python 3.11 or newer, and `git` on your `PATH`.

## Three questions, three commands

### `local` — what am I sitting on?

Branches checked out locally, plus worktree state: uncommitted changes, detached HEADs, stashes.

```console
$ git-wombat local ~/src
```

```markdown
# git-wombat

## ~/src/api-server
fetched 2026-10-16 09:41:02 · base: origin/main

- ⚠️ `feat/new-auth` — [NOT PUSHED] 1 commits ahead · last commit 4 months ago
- ✓ `feature/done` — [OK] fully pushed · last commit 2 weeks ago
- ❌ `api-server` — [UNREACHABLE] fetch failed for origin
- ⚠️ `api-server` — [STASH] stash@{0}: On main: refactor storage layer
```

### `remote` — which of my remote branches can go?

Remote branches, filtered by author or by name. Useful well beyond "my branches" — filter by a
ticket number and you get every branch for that ticket, whoever wrote it.

```console
$ git-wombat remote ~/src --author you@example.com
$ git-wombat remote ~/src --branch ABC-123
```

### `merged` — which remote branches are already in main?

Remote branches whose changes are fully present in the base branch, so deleting them loses nothing.

```console
$ git-wombat merged ~/src
```

```markdown
# git-wombat

## ~/src/web-app
fetched 2026-10-16 09:41:05 · base: origin/main

- ⚠️ `origin/feat/new-auth` — [NOT MERGED] not merged
- ✓ `origin/fix/typo` — [OK] safe to delete: merged into origin/main, no unpushed local changes
```

## Why it does not trust commit history

Most tools answer "is this branch merged?" with `git branch --merged`, which only asks whether the
branch is an *ancestor* of main. That is wrong in a way that matters here:

- A **squash-merged** branch is not an ancestor. `git branch --merged` says "not merged" for a branch
  whose changes are sitting in main right now.
- A branch merged and later **reverted** stays an ancestor, so it reads as "merged" when its changes
  are gone.

git-wombat instead asks the behavioural question — *would applying this branch change anything?* —
by performing a real three-way merge in memory and comparing the result to the base tree:

```console
$ git merge-tree --write-tree origin/main origin/fix/typo
```

Nothing is checked out, no index or ref is touched, and squash-merged branches are recognised. Git
itself uses tree comparison for exactly this purpose in `git cherry`.

**Known limitation:** a branch that was merged and then reverted still reports as merged. Reverting
does not remove ancestry, so the merge genuinely is a no-op. Every safe-to-delete line states its
reason so you can judge it.

## Safe to delete means two things

A branch is only annotated `safe to delete` when **both** hold:

1. its changes are already in the base branch, **and**
2. no local branch of the same name has unpushed commits.

The second condition is the one that saves you. A remote branch tells you nothing about work sitting
in a local branch of the same name:

```markdown
- ⚠️ `origin/fix/typo` — [OK] merged into origin/main, but local 'fix/typo' has unpushed changes
```

`local` reports the same branch as `[NOT PUSHED]`, so the problem is visible from both directions.

## Stale answers are labelled

Conclusions drawn from old refs are the main way a tool like this could quietly mislead you, so
every repository carries the time of its last successful fetch:

```markdown
## ~/src/api-server
fetched 2026-10-16 09:41:02 · base: origin/main
```

When any repository was read without a successful fetch this run, a warning appears once at the top.

The base branch is always `origin/main` (or `origin/master`) when that exists, not your local copy.
A local `main` forty commits behind would report live branches as unmerged and send you deleting
live work. When no remote ref exists it falls back to the local one, and says so — that is a weaker
claim, and the output treats it as one.

## Fetching

All three commands fetch (`git fetch --all --prune`) before analysing, so the refs they read are
current. A failed fetch is never fatal: the repository is still analysed and the failure is reported.

Once you have a fresh set of refs, repeat runs are cheap and touch no network:

```console
$ git-wombat merged ~/src --no-fetch
```

A fetch failure never stops the run, and the staleness warning tells you what you are looking at.

## Configuration

`~/.git-wombat.toml`, optional:

```toml
# One identity, or several. They combine with OR: "all of my addresses".
author = ["you@example.com", "personal@gmail.com"]

[whitelist]
# Glob patterns. Suppresses "this branch exists and is fine" for local scans,
# and ignores the branch entirely for remote scans.
patterns = [
  "release/*",
  "user/feature-*",
]
```

A branch matching the whitelist is hidden from `local` **only when it is clean** — a whitelisted
branch with unpushed commits or a dirty worktree still shows up. For `remote` and `merged` it is
ignored outright.

Identity resolves in this order, first match wins:

1. `--author` on the command line (repeatable, or comma-separated)
2. `author` in this file
3. `git config user.name` / `user.email`, read per repository

## Understanding the output

Output is markdown. It prints nicely in a terminal and stays valid markdown when redirected:

```console
$ git-wombat local ~/src > report.md
```

No ANSI escapes and no tables ever reach the file. Emoji are decorative only — the bracketed
marker always carries the meaning on its own, so a terminal without an emoji font loses nothing.

| Marker | Meaning |
|---|---|
| `[OK]` | Nothing to see — fully pushed, or clean |
| `[NOT PUSHED]` | Commits here that are not on the upstream |
| `[NOT MERGED]` | Changes not yet in the base branch |
| `[DIVERGED]` | Has both unpushed commits *and* missing upstream commits |
| `[UPSTREAM DELETED]` | The upstream branch no longer exists on the remote |
| `[CONFLICTS]` | Cannot be applied to the base branch cleanly |
| `[UNKNOWN]` | Cannot be determined — shallow clone, unrelated histories |
| `[DIRTY]` | Uncommitted changes in this worktree |
| `[DETACHED]` | Worktree HEAD is detached |
| `[STASH]` | Stashed work, with its message |
| `[UNREACHABLE]` | No remote configured, or the fetch failed |
| `[NO BASE]` | No `main` or `master`; branch analysis skipped |

`[UNKNOWN]` is deliberately distinct from `[CONFLICTS]`. A shallow clone may not have the merge base
locally, and reporting that as a conflict would invent a problem you do not have.

## Using it regularly

Findings alone do not change the exit code, so a weekly cron job can alert on *new* findings by
diffing two reports rather than on exit status. A path that is not a directory exits 1; an
unrecognised command exits 2. A repository you cannot read is reported and skipped, not fatal.

```console
$ git-wombat local ~/src --no-fetch > ~/reports/$(date +%F).md
$ diff ~/reports/yesterday.md ~/reports/today.md
```

## Development

```console
$ pip install -e ".[dev]"
$ pytest
```

The test suite builds real git repositories and runs real `git` commands — no mocked git, because
the behaviour worth protecting here is git's own.

## Licence

GPL-3.0-only. See [LICENSE](LICENSE).
