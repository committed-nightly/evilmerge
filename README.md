# evilmerge

Find merge commits that quietly changed something git would not have.

A merge commit records a tree and its parents. Merge those same parents again
and you get the tree the merge *should* have had. Anything that differs was
put there by whoever ran the merge — and if git never reported a conflict on
that file, nothing forced them to.

That is worth knowing because the ordinary views of history don't show it.
When a merge takes one side of a file wholesale, `git show --cc` prints
nothing at all — it only shows hunks that differ from *every* parent, and this
one matches a parent exactly. The change is gone and the history looks fine.

Useful if you have ever merged a long-lived release branch back into main and
wondered, afterwards, whether everything actually came across.

## Install

```
pip install git+https://github.com/committed-nightly/evilmerge
```

Python 3.10+, git 2.38+ (for `git merge-tree --write-tree`). No dependencies.

## Use

```
evilmerge                        # every merge reachable from HEAD
evilmerge --since=2024-01-01     # the usual way to keep it quick
evilmerge main..release
evilmerge --explain <merge>      # one merge, with the diff
evilmerge --json
```

## A real example

Build a repository where a merge quietly drops a change:

```bash
mkdir demo && cd demo && git init -q -b main

printf 'def widget():\n    return 1\n' > widget.py
printf '# Changelog\n'                 > CHANGELOG.md
git add -A && git commit -qm "initial"

git checkout -qb widgets
printf 'def widget():\n    return 1  # fixed\n' > widget.py
printf 'def extra():\n    return 3\n'           > extra.py
git add -A && git commit -qm "fix widget rounding"

git checkout -q main
printf '# Changelog\n\n- something\n' > CHANGELOG.md
git add -A && git commit -qm "changelog"

# Merge it. Nothing conflicts -- the two branches touch different files.
# But during the merge, someone puts widget.py back the way main had it.
git merge --no-commit --no-ff widgets
printf 'def widget():\n    return 1\n' > widget.py
git add -A && git commit -qm "Merge pull request #12 from acme/widgets"
```

git is entirely content with this:

```
$ git show --cc --stat HEAD
commit e2ea7c479640f843c9c213b1a0a989a635e235cf
Merge: e0c5f46 924f34a
Author: Priya Raman <priya@example.com>
Date:   Sat Sep 5 00:06:47 2026 +0000

    Merge pull request #12 from acme/widgets

 extra.py | 2 ++
 1 file changed, 2 insertions(+)
```

One file, two lines added: exactly what an honest merge of these two branches
looks like. `widget.py` is not mentioned. The full combined diff contains no
hunks whatsoever —

```
$ git show --cc HEAD | grep -c '^diff --git'
0
```

— and `git diff --name-only HEAD^1 HEAD` likewise prints only `extra.py`. The
fix to `widget.py` is gone and nothing in any of that hints at it.

```
$ evilmerge
0ce4325  Merge pull request #12 from acme/widgets
         2026-09-05  Priya Raman
  dropped  widget.py
    Comes out exactly as parent 1 had it. Parent 2's changes to it are not
    in the merge and nothing conflicted. `git show --cc` on this merge
    shows nothing here -- it only prints hunks that differ from every
    parent, and this one matches parent 1 exactly.

1 merge changed something on its own, of 1 checked.

$ echo $?
1
```

(The sha, date and author will be yours, not these.)

`--explain` shows what went missing:

```
$ evilmerge --explain HEAD
...
diff --git a/widget.py b/widget.py
@@ -1,2 +1,2 @@
 def widget():
-    return 1  # fixed
+    return 1
```

For the other kind of finding, add a line to `CHANGELOG.md` during the merge
instead of before it, and evilmerge calls it `foreign` — content that merging
the parents does not produce and that neither parent had. That one `git show
--cc` *does* display, because it differs from both parents; the `dropped` case
above is the one nothing else shows you.

## What it reports

Per path:

| | |
|---|---|
| `dropped` | The merge's version of this path is exactly one parent's version. The other parent's changes to it are not in the result. This is the one that hides from `git show --cc`. |
| `foreign` | The merge's version matches neither parent and isn't what merging produces. Content written — or a file deleted — during the merge itself. |

Per merge:

| | |
|---|---|
| `evil` | Deviates on paths that never conflicted. Reported. |
| `wholesale` | The merge's tree is byte-identical to one parent; the other contributed nothing. Reported once, not once per file. This is what `git merge -s ours` records. |
| `resolved` | Deviates only inside paths git reported as conflicted. That is what resolving a conflict *is*. Not a finding; counted in the summary, listed with `--all`. |
| `skipped` | Not checked: an octopus merge, or parents git refuses to merge at all. Never counted as a pass. |

**A finding is something to look at, not something that is wrong.** Plenty of
deliberate merges deviate: a maintainer fixing up an import during a
back-merge, a `-s ours` release merge. evilmerge's claim is only that it
happened and that nothing in the diff would have told you.

## Exit codes

```
0  no merge in the range changed anything on its own
1  at least one did
2  the check could not run at all
```

`2` is deliberately not `1` and very deliberately not `0`. Not a repository, a
revision range that doesn't resolve, `--explain` pointed at something that
isn't a merge: all of those mean nobody looked, and "nobody looked" reported as
a green tick is worse than no check at all.

A range with no merge commits in it is a `0` and says so in as many words —
plenty of repositories rebase and genuinely have none.

## In CI

Check what a pull request is about to add, rather than the whole history:

```yaml
- run: pip install git+https://github.com/committed-nightly/evilmerge
- run: evilmerge origin/main..HEAD
```

Whole-history runs are fine but not instant: about 10 seconds for 1,200
merges, most of it spent in git.

## Known limits

- **The merge is recomputed with *your* git, today.** A merge recorded in 2014
  by git 1.9 using the `recursive` strategy is being compared against what
  `ort` produces now. Where the two strategies would have merged differently —
  mainly rename detection — evilmerge reports a deviation that no human made.
  This is the main source of false positives and it gets worse the older the
  history is.
- **It cannot tell deliberate from accidental**, and does not try to. See
  above.
- **Octopus merges are not checked.** `git merge-tree` takes two commits. An
  octopus could be unrolled pairwise, but the order it was originally merged
  in is not recorded, and a wrong order would produce confident nonsense.
- **Squash merges are invisible to it.** A squash-merged pull request is an
  ordinary commit with one parent; there is no second parent to compare
  against and nothing here applies. If your repository squashes everything,
  this tool has nothing to say about it.
- **`.gitattributes` merge drivers are respected**, because git does the
  merging. A `merge=union` file will be recomputed with `union`.
- **There is no ignore list.** A repository that does routine `-s ours`
  release merges will get a finding for each one, forever. That is a real
  cost and the honest answer is that suppression should be earned by someone
  hitting the problem, not designed in advance.

## How noisy is it, really

Against two real repositories with long merge-heavy histories:

| repo | merges | findings | conflict resolutions excluded | time |
|---|---|---|---|---|
| `pallets/click` | 1,185 | 7 | 115 | 10s |
| `psf/requests` | 1,612 | 6 | 46 | 13s |

Thirteen findings out of 2,797 merges, few enough to read every one. Spot
checks: click's 2024 `Merge branch '8.1.x'` rewrites a type annotation in
`src/click/utils.py`, a file that did not conflict, to match main's style —
real, deliberate, and invisible in review. Its 2018 docs merge deletes four
logo PNGs that a merge of the parents keeps. requests' 2017
`Merge remote-tracking branch 'origin/master'` discards three paths from the
other branch entirely.

## Licence

MIT.
