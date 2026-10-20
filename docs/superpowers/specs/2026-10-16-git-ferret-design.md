# git-ferret — design

A read-only reporting tool for auditing many local git repositories at once.

## Intent and why

A personal collection of roughly 20-30 cloned repositories accumulates invisible state: branches
pushed long ago and forgotten, upstream branches deleted server-side, work abandoned on a branch
four months back. Nothing surfaces this. Auditing repo-by-repo is too slow to do regularly, so the
state rots.

git-ferret answers "what's lying around, and what is safe to remove?" in one run, and never removes
anything itself. The user deletes; the tool only reports and justifies.

## Goals

- One command walks a directory tree, finds every real repository, and reports its branch state.
- Three subcommands matching three distinct questions, each independently runnable.
- Reports are trustworthy above all: no confident wrong answers. Unknown is reported as unknown.
- Never mutates a repository except an explicitly-requested fast-forward.
- Output is markdown — readable in a terminal, valid in a file.

## Non-goals

- Deleting anything. Ever. Not branches, not stashes, not repositories.
- Stashing, committing, merging non-fast-forward, rewriting history, or touching the index.
- Cleaning IDE state (IntelliJ shelves and similar are explicitly out of scope).
- Bare-repo and submodule support.
- Parallelism. 20-30 repos with streamed sequential output is the design point.

## Anti-goals

These would make the tool a failure even if every stated goal were met:

- **Reporting a branch as "merged" when it is not, or vice versa.** A wrong clean-up
  recommendation costs the user real work. Ambiguity must surface as *unknown*, never as a guess.
- **Silently reading stale state.** A conclusion drawn from last week's refs must be visibly
  labelled as such.
- **Mutating during a report.** Any write not explicitly requested by a flag is a bug.

## Constraints

- Python 3.11+ (`tomllib` is stdlib from 3.11).
- `pip install`-able, console entry point.
- Shell out to `git` via `subprocess`. Do not reimplement git behaviour in a library.
- Repository read-only except `merge --ff-only` behind an explicit flag.

---

## Commands

Three subcommands. Each walks the tree, analyses, and prints independently, so any one can be run
alone without remembering to run another first.

```
git-ferret local  [PATH]      # branches checked out locally, worktrees, dirty state, stashes
git-ferret remote [PATH]      # remote branches, filtered by author and branch name
git-ferret merged [PATH]      # remote branches whose changes are already in origin/<base>
```

`remote` is named for what it does — enumerate remote branches — not for who wrote them. It is not a
"my branches" command: with no `--author` it lists every remote branch, and because it filters by
branch name as well as author it also answers unrelated questions such as "which remote branches
mention ticket ABC-123". Naming it `remote` keeps that general purpose visible; `--author` is an
optional narrowing, not the command's purpose.

`PATH` defaults to the current directory. All three accept:

- `--no-fetch` — skip fetching entirely; work from whatever is already in `.git`.
- `--author SUBSTR` — substring match against `Name <email>`. Repeatable, or comma-separated.
- `--branch SUBSTR` — substring match against the branch name.

`--branch` rather than `--name` to remove ambiguity with git's `--name-rev` family and with the
TOML `whitelist` key; "branch" is unambiguous in both the CLI and the config.

`local` ignores `--author` and `--branch`; they are meaningless for local worktree state.

### Fetching

Default behaviour for all three is `git fetch --all --prune` on every repository before analysis.
This is the only mechanism for talking to a remote — there is no separate `ls-remote` path.

`--all` rather than a bare `fetch --prune` because `remote` and `merged` iterate remote branches
across **all** configured remotes. Fetching only `origin` would leave an `upstream` remote
permanently stale while the tool reported confident verdicts about its branches — and `--no-fetch`
is the intended mode for repeat runs, so that staleness would never self-correct. The repository
heading names which remotes were refreshed.

`--no-fetch` skips it. The intended usage is one fetching run to refresh state, then `--no-fetch`
for all subsequent runs.

Fetch failures are **non-fatal**: the repository is analysed against stale local state and the
staleness is recorded so it can be surfaced in that repository's heading.

Fetch age is the **modification time of `.git/FETCH_HEAD`**, not the duration of this run's fetch.
Only a cross-run timestamp can support a meaningful staleness warning; "time since this run" would
render as `fetched 0s ago` on every heading and reduce the field to decoration. On fetch failure the
stamp still reads `.git/FETCH_HEAD`'s mtime, so it correctly shows the age of the last *successful*
refresh.

### Identity

Resolved in this order, first match wins:

1. `--author` value on the command line (repeatable, or comma-separated)
2. `author` in `~/.git-ferret.toml`
3. `git config user.name` and `git config user.email`

Sources 1 and 2 may supply multiple identities (repeatable flag, comma-separated, or a list in
TOML). They combine with **OR** — the intent is "all of my addresses", not "a team".

`git config` is resolved **per repository**, since a collection of repos may legitimately carry
different local identities. If only `user.email` is set, the identity is that email alone.

If none resolve, emit a warning and skip author-filtered analysis entirely — do not fall back to
listing every remote branch. Matching is a **substring** test of each resolved identity against the
full `Name <email>` string of each commit, exactly as specified: one identity string tested against
one author string.

A branch counts as the user's own if **any** commit in its range matches. Not all of them —
requiring every commit would hide exactly the branches most likely forgotten, such as one started
in a hurry and later merged with a colleague's fix.

## Repository discovery

Walk `PATH` recursively. **Stop descending once a directory is identified as a repository**, and
skip `.git` internals entirely. Personal repo collections routinely contain nested checkouts
(dotfiles repos with cloned plugins, vendored clones); descending would fetch and report those as
top-level repositories and duplicate findings against their ancestor. There are no ignore rules.

A repository is a directory containing `.git`:

- `.git` as a **directory** — a real repository. Include.
- `.git` as a **file** — a linked worktree or a submodule. Enumerate via
  `git worktree list --porcelain`.
- Target of the `.git` file resolves under `.git/modules/` — a **submodule**. Skip; it is vendored
  content, not the user's own repository.

Bare repositories are skipped, detected via `git rev-parse --is-bare-repository` returning true.

Each repository is processed fully before moving to the next. Output is streamed per repository so
results appear as they are produced.

## Base branch

Per repository, resolved in this order:

1. `origin/main`, else `origin/master` — **preferred**, when the remote-tracking branch exists.
2. Local `main`, else local `master`.
3. Neither exists → report `[NO BASE]`. `remote` and `merged` then skip the repository's branch
   analysis, because their verdicts are measured against the base; `local` still lists the branches,
   because its findings are not. Do not guess at `develop`, `trunk`, or similar.

The remote-tracking ref is preferred because `merged` recommends deleting **remote** branches, so
the only question that matters is whether the branch is absorbed into the branch the remote actually
has. Comparing against a stale local `main` — say, 40 commits behind — would report a branch as
unmerged when the server already contains it, producing a confident recommendation to delete a live
branch. That is precisely the anti-goal, not a rounding error.

Each repository heading states which ref was used as the base, e.g. `base: origin/main`. When the
analysis falls back to a local ref because no remote-tracking branch exists, that fallback is
visible in the heading, because the confidence of a "safe to delete" verdict is lower without it.

## Per-command analysis

### `local`

Covers checked-out branches and local worktree state.

For each worktree in the repository (from `git worktree list --porcelain`):

- **Uncommitted changes** — reported per worktree, not per repository. A repository can be clean on
  `main` while a forgotten worktree holds an unfinished refactor.
- **Detached HEAD** — reported. A detached worktree is a common place to lose track of work.
- **Stashes** — `git stash list`, with age and the branch name recorded in each entry.

For each local branch other than the base branch, `main`, `master`, and anything matching the
configured whitelist:

- **Not pushed** — no upstream configured, or ahead of upstream. Include ahead count.
- **Upstream deleted** — upstream ref is gone (`[origin/x: gone]` in `git branch -vv`). This check
  depends on pruning having happened, which is why fetch-and-prune is the default.
- **Diverged** — has local commits the remote lacks *and* is behind. Cannot fast-forward in either
  direction; a fetch cannot reconcile it.
- **Last commit** — relative date (`git log -1 --format=%cr`). Turns "not pushed" into a decision.

Whitelist patterns are matched against branch names with glob semantics supporting `*` and `?`,
always in addition to the hardcoded `main` and `master` exclusion.

**The whitelist means different things for local and remote scans, deliberately.**

*Locally*, whitelisted branches are excluded from the listing of branches that merely exist — but
they are **still reported if they have any other issue**. The whitelist suppresses "this branch
exists and is fine", not "this branch has a problem". So a whitelisted `release/*` branch sitting
clean and pushed does not clutter the output, while a whitelisted branch with unpushed commits or a
dirty worktree still surfaces.

*For remote scans* (`remote` and `merged`), whitelisted branches are **ignored entirely** — never
listed and never analysed. There is no local-branch state to reason about, so there is nothing to
rescue them from exclusion.

`local` lists **all non-whitelisted local branches, including those with no findings**. The report
is a census of local branch state, not only a list of problems: a clean branch is useful
confirmation that it was looked at and found fine, and omitting it makes a clean repository
indistinguishable from an unexamined one. Clean branches are marked so they can be skipped by eye
rather than being confused with problems.

Overlapping findings collapse to **one line per branch** with a primary label, in precedence order
(upstream deleted > diverged > not pushed), followed by supporting detail:

```
- `wip/scratch` 🧹 [UPSTREAM DELETED] also 4 commits unpushed · last commit 11 months ago
```

A branch whose upstream was deleted is simultaneously ahead of an upstream that no longer exists;
listing it three times is noise, and the primary label is the one that determines the action.

### `remote`

Remote branches, for spotting cleanup candidates. General-purpose: "which remote branches match X",
where X is an author, a name fragment, or both.

Iterate remote branches across all configured remotes. For each, apply the filters:

- Excluded: base branch, `main`, `master`, and whitelist matches — **ignored entirely**, never
  listed and never analysed.
- If `--author` given (or resolved from config): keep branches with **any** commit in range whose
  `Name <email>` contains the identity substring.
- If `--branch` given: keep branches whose name contains the substring.
- Both filters optional and combined with AND when both are present.

With no filters, this reports all remote branches. With `--author` alone it answers "my branches";
with `--branch ABC-123` it answers "branches for this ticket", which is why the command is named
`remote` rather than `mine`.

**The commit range is `base..<remote-branch>`** — only the commits the branch introduces on top of
the base branch, never every commit reachable from the branch tip. This distinction decides whether
the command is useful at all: because the user's commits are almost always present in the base
branch's history, they are reachable from nearly every branch in the repository, so scanning the
full reachable history would match virtually all branches and degenerate into "list everything".

### `merged`

Remote branches whose changes are already fully present in the base branch, and can therefore be
deleted. Compares against the **base ref resolved above** — `origin/main` where available, so the
verdict reflects the branch the remote actually has.

For every remote branch considered (all remotes, minus base branch, `main`, `master`, and the
whitelist), the tool also checks whether a **local branch of the same name has unpushed work**. If
it does, the remote branch is still reported as merged — that fact is true — but it is **not**
annotated safe to delete, and the reason is shown. Merged-ness and safety are separate claims, and
only the first is answered by the merge test.

**This must not be decided by commit history.** Ancestry-based checks (`git branch --merged`,
`git log --author`) are wrong in both directions:

- A squash-merged or rebase-merged branch is not an ancestor of main, so ancestry reports
  **not merged** when it is in fact fully merged. *Verified experimentally: squash-merge case,
  ancestry false negative, tree test correct.*
- A branch merged and later reverted remains an ancestor, so ancestry reports **merged** — though
  the tree test reports merged too, because the merge is a genuine no-op. See the known limitation
  below. *Verified experimentally.*

The test used is a real three-way merge, which asks the behavioural question directly — does applying
this branch's changes to the base branch change anything?

```
git merge-tree --write-tree <base> <branch>
```

This performs the merge in memory and prints the resulting tree hash. It touches no worktree, no
index, and no ref, and works on bare repositories.

- Resulting tree hash **equals** the base branch's tree hash → the branch's changes are already
  fully absorbed → **merged**.
- Resulting hash **differs** → the branch would change the base branch → **not merged**.

Failure modes are distinguished rather than collapsed, because each is a different answer and two
of them are "cannot determine":

| Outcome | Condition | Reported as |
|---|---|---|
| merged | exit 0, tree equals base tree | merged |
| not merged | exit 0, tree differs | not merged |
| conflicts | merge reports conflicting hunks | conflicts |
| unknown | no merge base (unrelated histories, or shallow clone missing the base) | unknown |

Anything other than a clean merge is **never** reported as `merged`, and anything indeterminate is
reported as `unknown` rather than `conflicts`. Distinguishing these matters: the alternative design
maps every non-zero exit to "conflicts", which confidently mislabels an unrelated-history or
shallow repository as having a merge conflict.

### Shallow clones

If a repository is shallow (`git rev-parse --is-shallow-repository` is true), the merge base may not
exist locally, and `merge-tree` fails in a way that resembles a conflict without being one. Such
branches are reported as **unknown** and never folded in with the confident results.

The shallow rule applies to every command that performs merge tests, not just `merged`. Granularity
is per repository as a fast pre-check, but the **unknown verdict is recorded per branch** — a
shallow repository may still resolve for branches whose merge base happens to be present.

### Known limitation of the merge test

A branch merged into main and subsequently **reverted** will be reported as merged. Its changes are
no longer present in main's content, but the branch remains an ancestor, so the merge is a no-op
and the tree comparison correctly reports equality.

This is a deliberate, accepted limitation. The tree test's purpose is to be correct on
squash-and-rebase histories where ancestry fails outright; detecting merge-then-revert would
require a three-way comparison against the revert point rather than against the base tip. The tool
must not claim to detect this. Any branch recommended for deletion carries its reason inline
("merged, fully pushed") so the user can judge it themselves.

### Unreachable remotes

A repository with no configured remote, or whose fetch failed, is reported. These are the **same
finding**, surfaced through different evidence depending on mode:

- **With fetching** — a non-zero fetch exit is direct evidence the remote is unreachable, and covers
  both typo'd URLs and projects deleted server-side.
- **With `--no-fetch`** — no probe is made, so only statically detectable problems are reported: a
  missing remote, or a malformed remote URL. A server-deleted project is **undetectable** in this
  mode, and the tool must not imply otherwise.

When a repository's fetch fails, its heading states that it was analysed against stale state, and
its `remote`/`merged` findings — if any — derive from that stale state.

---

## Updating

`local` accepts `--update`, **default off**.

When enabled, for each worktree whose tree is clean and whose HEAD is a branch with an upstream:

```
git merge --ff-only
```

Skipped, each reported with its reason:

- Uncommitted changes in the worktree.
- Detached HEAD.
- No upstream configured.
- Merge is not a fast-forward.
- Branch checked out in another worktree — git refuses to update a ref checked out elsewhere.

With the flag off (the default), `local` performs no writes of any kind.

---

## Output

Markdown, streamed per repository, repository headings followed by a list of findings.

**No tables.** Wide tables do not wrap in a terminal, and this report is read on screen far more
often than in a rendered file. Use headings and nested lists.

Build the report as markdown text first, then either render through `rich.markdown` when stdout is a
TTY, or write the text verbatim when it is not. Box-drawing characters and ANSI colour from `rich`
must never reach a pipe.

Every repository gets a heading whether or not it has findings, so a clean repository still confirms
itself in the output.

### Status markers

Findings are marked with **capitalised bracketed status**, so they stand out when skimming a long
list and survive copy-paste into an issue tracker, a commit message, or a terminal that renders no
emoji:

| marker | meaning |
|---|---|
| `[NOT PUSHED]` | commits exist locally that are not on the upstream |
| `[DIVERGED]` | has both unpushed commits and missing upstream commits |
| `[UPSTREAM DELETED]` | the upstream branch no longer exists on the remote |
| `[CONFLICTS]` | cannot be applied to the base ref cleanly |
| `[UNKNOWN]` | cannot be determined — shallow clone, unrelated histories |
| `[DIRTY]` | uncommitted changes in this worktree |
| `[DETACHED]` | worktree HEAD is detached |
| `[UNREACHABLE]` | remote missing, or fetch failed |
| `[OK]` | no findings — the clean case, stated positively |
| `[SAFE TO DELETE]` | merged into the base branch, nothing local depends on it: a deletion candidate |
| `[UNSAFE TO DELETE]` | merged, but a same-named local branch has unpushed work: keep it |

Every branch line carries a marker, so a reader never has to infer "no marker means fine". The two
deletion markers are the point of `merged`: a branch that is merged and a branch that is merged *and
safe* are different answers, and both are reported rather than one being withheld.

### Emoji

Emoji are used as **decorative cues only, never load-bearing**. The bracketed marker above always
carries the full meaning on its own; if a terminal lacks a glyph and renders tofu, no information is
lost.

There is **one glyph per status**, not one per severity. Three symbols across twelve statuses told
the reader nothing at a glance, which is the only glance a long list gets; a distinct glyph each is
what makes skimming possible.

| emoji | used for | reads as |
|---|---|---|
| `✓` | `OK` | fine |
| `⬆️` | `NOT_PUSHED` | commits waiting to go up |
| `🔀` | `NOT_MERGED` | not in the base branch yet |
| `↔️` | `DIVERGED` | local and upstream pulled apart |
| `🧹` | `UPSTREAM_DELETED` | gone from the remote |
| `💥` | `CONFLICTS` | the merge genuinely breaks |
| `🔌` | `UNREACHABLE` | no route to the remote |
| `❓` | `UNKNOWN` | cannot be determined |
| `✏️` | `DIRTY` | uncommitted edits |
| `📦` | `STASH` | work put aside |
| `📍` | `DETACHED` | HEAD not on a branch |
| `🧭` | `NO_BASE` | nothing to compare against |
| `🗑️` | `SAFE_TO_DELETE` | a deletion candidate |
| `🚫` | `UNSAFE_TO_DELETE` | merged, but keep it |

One case carries a glyph other than its status would give it: a safe-to-delete claim resting on a
local base ref is marked `❓`, because the claim is weaker than the marker says and the detail text
says so too.

Constraints that follow from how these render, all verified rather than assumed:

- `✅` and `❌` are East-Asian **wide** (two cells); `⚠️` and `ℹ️` carry a variation selector and are
  two codepoints for one glyph. Neither matters for list-item output, and there are no tables in
  this report — but it is why emoji must not be used for column alignment.
- Emoji are valid UTF-8 and render correctly in markdown viewers, so they do not compromise the
  "valid markdown when piped" requirement.
- The failure mode is font fallback: bare SSH sessions and some Windows consoles render tofu boxes.
  Acceptable, because the text marker survives it.

### Timestamps

Fetch times are rendered as **absolute timestamps** (`fetched 2026-10-16 09:41:02`), not relative
("2s ago"). A fetch stamp is a record of provenance — when this data was last refreshed — and is
most useful when it can be compared precisely against a cron schedule, a CI run, or a colleague's
timestamp. A relative "2s ago" carries no information once the report has been sitting in a file.

Last-commit dates **are** relative (`last commit 4 months ago`), because the question they answer is
"how long has this been sitting here", and a relative age answers that at a glance where an absolute
date requires mental arithmetic. The two are different kinds of information and are formatted
differently on purpose.

Shape:

```markdown
# git-ferret

## api-server
fetched 2026-10-16 09:41:02 · base: origin/main

- ⬆️ `feature/new-auth` [NOT PUSHED] 3 commits ahead · last commit 4 months ago
- 🧹 `wip/scratch` [UPSTREAM DELETED] also 4 commits unpushed · last commit 11 months ago
- ✓ `fix/typo` [OK] safe to delete: merged into origin/main, no unpushed local changes
- ✓ `release/2.1` [OK] last commit 2 weeks ago
- 📍 `../api-server-hotfix` [DETACHED] clean

## web-app
never fetched

- 🧭 `web-app` [NO BASE] no base branch (neither origin/main nor origin/master)
- ✏️ `.` [DIRTY] 4 files modified, 1 untracked

> ⚠️  2 of 4 repositories analysed from stale local state (no fetch this run)
```

Headings are **relative to the scanned root**: the root the user typed is the part of the path they
already know, so repeating it above every heading is noise that wraps. A repository that *is* the
root is named `.`.

**A finding line carries no separator** between the branch and its marker: `\`branch\` [MARKER] detail`.
The marker delimits itself, so a dash in front of it is a third thing to read past.

**A heading and the line under it are one unit.** The fetch stamp says where that repository's refs
came from, so it sits directly beneath the name, with nothing between them, and a `skipped:` note
joins that same line, because a second line would be one paragraph with the first and the renderer
would glue them back together on screen. Repositories are separated from each other by one blank
line.

Spacing is a property of the report, not of the renderer: `rich` pads a heading with a blank line on
its way to a terminal and writes nothing when stdout is a pipe, so a layout left to the renderer is
formatted one way on screen and another in a file. The text therefore carries the blank lines
between sections, and the terminal is rendered so the heading does not pad itself.

Each repository heading carries **fetch age**. When a run reads entirely from stale local state,
a warning is printed once at the end of the report. A conclusion drawn from old refs must never be
mistaken for a current one.

### Stale-run warning

Printed **once at the end** when **any** repository in the run is being read from stale local state
(whether because `--no-fetch` was passed or because its fetch failed), naming the count. Not gated
on *every* repository being stale — a single stale repo among thirty is exactly the case worth
nagging about, and a warning that only fires when everything is stale would miss it. A repository
with no remote is excluded: there is nothing it could have refreshed.

Every repository heading additionally carries its own `fetched <age> ago`, so the per-repo detail is
always available even when the top-level warning is not triggered.

### Safe-to-delete annotations

The tool never deletes. It marks a remote branch `[SAFE TO DELETE]` only when **all** of these hold:

1. **Merged**, per the tree test (never ancestry), against the base ref resolved above.
2. **No unpushed local work on the branch.** If a local branch of the same name exists and its
   upstream is not in sync with the remote branch, the branch is marked `[UNSAFE TO DELETE]` and the
   reason is shown.

Condition 2 is what makes the annotation trustworthy for the deletion the user is actually
performing. `merged` analyses remote refs, and a remote branch tells you nothing about work sitting
in a local branch of the same name. Without this gate the tool would cheerfully recommend deleting
`origin/feature-x` while the user's local `feature-x` holds three uncommitted-into-the-remote
commits that exist nowhere else.

The two conditions surface in the two commands that respectively own them, so a branch under question
is visible from both directions:

- `local` tags the local branch `[NOT PUSHED]` / `[DIVERGED]`
- `merged` marks the corresponding remote branch `[UNSAFE TO DELETE]`

Both merged outcomes are reported, because a branch that is merged and must be kept is exactly the
one a reader needs to see and not delete. The marker carries the verdict, so it is readable without
reading a line of detail:

```markdown
- 🗑️ `origin/fix/typo` [SAFE TO DELETE] merged into origin/main, no unpushed local changes
- 🚫 `origin/feat/auth` [UNSAFE TO DELETE] merged into origin/main, but local 'feat/auth' has unpushed changes
```

This is the payoff of doing the merge test properly.

### Exit codes

- `0` — run completed; note that findings are not failures.
- `1` — usage or configuration error.
- `2` — run failed before completing.

Findings alone do not produce a non-zero exit. This is deliberate: a weekly cron job should be able
to alert on *new* findings via output diffing rather than on exit status.

---

## Configuration

Single file, `~/.git-ferret.toml`. Read-only (`tomllib` cannot write); the user edits it.

```toml
# One identity, or several — they combine with OR ("all of my addresses").
# Optional: falls back to git config user.name / user.email when absent.
author = ["dev@example.com", "personal@gmail.com"]

[whitelist]
patterns = [
  "release/*",
  "user/feature-*",   # glob, supports * and ?
]
```

`author` accepts either a single string or a list; both forms are supported so the file is not
punishing to write either way.

Flags override the file; the file overrides `git config`. `main` and `master` are always excluded
regardless of configuration.

## Additional features

Beyond the original scope, all report-only:

- **Last-activity dates** on branches, so findings carry a recency signal.
- **Safe-to-delete annotations** with reasons, gated on local/remote sync (above).
- **Unreachable / missing remotes**.
- **Stash reporting** in `local`.
- **Per-worktree dirty state**, so a forgotten worktree cannot hide behind a clean `main`.
- **Absolute fetch timestamps** and stale-run warnings.
- **Detached HEAD detection**.
- **`[OK]` positive markers** on clean branches, so a clean repository is affirmatively reported
  rather than merely silent.

Deliberately excluded: any form of deletion, IDE shelf support (IntelliJ shelves live outside the
repositories entirely), parallelism.

## Libraries

- `typer` — CLI, good `--help` and validation from type hints.
- `rich` — terminal rendering, progress, markdown rendering.
- `tomllib` — stdlib config parsing.
- `subprocess` — all git invocation.

No GitPython. `merge-tree --write-tree` has no usable library equivalent, so shelling out is
required regardless; shelling out also inherits the user's actual git version, configuration, and
credential helper rather than reimplementing and drifting from them.

## Python layout

```
git_ferret/
  __init__.py
  cli.py           # typer app, subcommand definitions
  config.py        # ~/.git-ferret.toml loading, identity resolution
  discovery.py     # recursive repo/worktree detection
  git.py           # subprocess wrappers for all git calls
  report.py        # findings model, markdown assembly
  analysis/
    local.py
    remote.py
    merged.py
```

## Left to the builder

- Internal dataclass shapes for findings; the report format above is the contract.
- Exact `rich` progress/spinner treatment during long fetches.
- Whether `local`'s stash output is grouped per-worktree or pooled per repository.
- Test fixture layout. All behaviours above are verifiable with scripted git repos; note that
  `git revert` rejects `-q`, which silently breaks scripted revert fixtures if stderr is hidden.
