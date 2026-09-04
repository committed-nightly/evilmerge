"""Work out what a merge commit did that merging its parents would not.

The idea is small enough to state in one paragraph. A merge commit records a
tree and a set of parents. Ask git to merge those same parents again, right
now, and you get the tree the merge *should* have had. Anything that differs
between the two trees was put there by whoever ran the merge -- by resolving a
conflict, or by editing during the merge, or by throwing one side away.

Most of that is legitimate. Conflicts have to be resolved by hand, and a
resolved conflict differs from the recomputed tree by definition (the
recomputed tree has conflict markers in it). So conflicted paths are excluded,
and what is left is the interesting part: places where nothing forced anyone's
hand and the merge came out different anyway.

The failure this is built to catch is the quiet one. A merge that takes one
side of a non-conflicting file wholesale is invisible to every normal view of
history: ``git show --cc`` prints nothing at all, because it only shows hunks
that differ from *every* parent, and this hunk matches one of them exactly.
``git diff MERGE^1 MERGE`` is likewise empty. The change is gone and the
history looks fine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import gitcmd
from .gitcmd import GitError

# What a single path had done to it.
DROPPED = "dropped"
FOREIGN = "foreign"

# What the merge as a whole comes to.
CLEAN = "clean"
RESOLVED = "resolved"
EVIL = "evil"
WHOLESALE = "wholesale"
SKIPPED = "skipped"

#: Verdicts that mean somebody should look at this merge.
FINDINGS = (EVIL, WHOLESALE)


@dataclass(frozen=True)
class PathFinding:
    """One path the merge got different to what git would have produced."""

    path: str
    kind: str
    #: 1-based index of the parent whose version the merge kept, for DROPPED.
    kept_parent: int | None = None
    #: 1-based index of the parent whose changes went missing, for DROPPED.
    lost_parent: int | None = None


@dataclass
class MergeReport:
    """The verdict on one merge commit."""

    subject: gitcmd.Subject
    parents: list[str]
    verdict: str
    findings: list[PathFinding] = field(default_factory=list)
    #: Paths git reported as conflicted when the merge was recomputed.
    conflicted: frozenset[str] = frozenset()
    #: Paths inside a conflict that the resolution changed. Not a finding.
    resolved_paths: frozenset[str] = frozenset()
    #: For WHOLESALE: the parent whose tree the merge reproduces exactly.
    kept_parent: int | None = None
    #: For WHOLESALE: how many paths a real merge would have come out with.
    wholesale_paths: int = 0
    #: For SKIPPED: why this merge could not be checked.
    reason: str = ""
    #: The tree git produces for these parents today. Used by --explain.
    clean_tree: str = ""

    @property
    def is_finding(self) -> bool:
        return self.verdict in FINDINGS


@dataclass
class Report:
    """Every merge looked at, in the order they were looked at."""

    merges: list[MergeReport] = field(default_factory=list)

    @property
    def findings(self) -> list[MergeReport]:
        return [m for m in self.merges if m.is_finding]

    @property
    def skipped(self) -> list[MergeReport]:
        return [m for m in self.merges if m.verdict == SKIPPED]

    @property
    def resolutions(self) -> list[MergeReport]:
        return [m for m in self.merges if m.verdict == RESOLVED]


def check_merge(sha: str, cwd: str) -> MergeReport:
    """Judge one merge commit against a freshly recomputed merge of its parents."""
    subject = gitcmd.describe(sha, cwd)
    parents = gitcmd.parents(sha, cwd)

    if len(parents) > 2:
        # git merge-tree takes exactly two commits. An octopus could be
        # unrolled pairwise, but the order it was originally merged in is not
        # recorded, and a wrong order would produce confident nonsense. Saying
        # "not checked" is worth more than a guess.
        return MergeReport(
            subject,
            parents,
            SKIPPED,
            reason=f"octopus merge with {len(parents)} parents",
        )
    if len(parents) < 2:  # pragma: no cover - rev-list --merges cannot yield this
        return MergeReport(
            subject, parents, SKIPPED, reason="fewer than two parents"
        )

    try:
        recomputed = gitcmd.merge_tree(parents[0], parents[1], cwd)
    except GitError as exc:
        # Unrelated histories are the usual cause: git refuses to merge them
        # without --allow-unrelated-histories, so there is no tree to compare.
        return MergeReport(
            subject, parents, SKIPPED, reason=_one_line(str(exc))
        )

    deviation = gitcmd.changed_paths(recomputed.tree, sha, cwd)
    resolved_paths = deviation & recomputed.conflicted
    unforced = sorted(deviation - recomputed.conflicted)

    if not unforced:
        verdict = RESOLVED if resolved_paths else CLEAN
        return MergeReport(
            subject,
            parents,
            verdict,
            conflicted=recomputed.conflicted,
            resolved_paths=resolved_paths,
            clean_tree=recomputed.tree,
        )

    # Which parents does the recorded merge agree with, path by path? An empty
    # diff against a parent means the merge reproduces that parent exactly.
    against = [gitcmd.changed_paths(parent, sha, cwd) for parent in parents]

    for index, differing in enumerate(against):
        if not differing:
            # The merge's tree *is* this parent's tree. Listing every path
            # individually would be a wall of noise saying one thing.
            return MergeReport(
                subject,
                parents,
                WHOLESALE,
                conflicted=recomputed.conflicted,
                resolved_paths=resolved_paths,
                kept_parent=index + 1,
                wholesale_paths=len(deviation),
                clean_tree=recomputed.tree,
            )

    findings = [_classify(path, against) for path in unforced]
    return MergeReport(
        subject,
        parents,
        EVIL,
        findings=findings,
        conflicted=recomputed.conflicted,
        resolved_paths=resolved_paths,
        clean_tree=recomputed.tree,
    )


def _classify(path: str, against: list[frozenset[str]]) -> PathFinding:
    """Decide what happened to one path that the merge changed on its own."""
    matched = [i for i, differing in enumerate(against) if path not in differing]
    if len(matched) == 1:
        kept = matched[0]
        return PathFinding(
            path, DROPPED, kept_parent=kept + 1, lost_parent=2 - kept
        )
    # Nothing to match against, or -- via git's rename following -- both. In
    # either case the merge's version of this path is not the one merging
    # produces, and no parent alone explains it.
    return PathFinding(path, FOREIGN)


def check(cwd: str, range_args: list[str]) -> Report:
    """Check every merge commit in a revision range."""
    return Report(
        [check_merge(sha, cwd) for sha in gitcmd.merge_commits(cwd, range_args)]
    )


def _one_line(message: str) -> str:
    """git's fatals are often two lines, the second of which is advice."""
    first = message.strip().split("\n")[0]
    return first.removeprefix("fatal: ").strip()
