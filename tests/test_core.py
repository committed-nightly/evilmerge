"""What each shape of merge comes out as."""

from __future__ import annotations

import pytest

from evilmerge.core import (
    CLEAN,
    DROPPED,
    EVIL,
    FOREIGN,
    RESOLVED,
    SKIPPED,
    WHOLESALE,
    check,
    check_merge,
)


def only(repo):
    """The single merge in the repo, checked."""
    report = check(str(repo.path), ["HEAD"])
    assert len(report.merges) == 1, [m.verdict for m in report.merges]
    return report.merges[0]


def two_branches(repo):
    """A base, a feature branch touching a/c, main touching b/d.

    The two never overlap, so merging them can never conflict: anything a
    merge of these does was done deliberately. Two files changed per side
    rather than one, so that reverting a single file leaves a tree that is
    still nobody's -- otherwise every one-file revert is indistinguishable
    from taking a parent wholesale, which is a different (and louder) finding.
    """
    repo.commit(
        "base", **{"a.txt": "A\n", "b.txt": "B\n", "c.txt": "C\n", "d.txt": "D\n"}
    )
    repo.branch("feature")
    repo.commit("feat: change a and c", **{"a.txt": "A-feature\n", "c.txt": "C-feature\n"})
    repo.checkout("main")
    repo.commit("main: change b and d", **{"b.txt": "B-main\n", "d.txt": "D-main\n"})


def test_an_honest_merge_is_clean(repo):
    two_branches(repo)
    repo.merge("feature", "Merge branch 'feature'")

    merge = only(repo)
    assert merge.verdict == CLEAN
    assert merge.findings == []


def test_dropping_one_side_without_a_conflict(repo):
    two_branches(repo)
    # The merger takes main's a.txt, throwing away the whole feature branch's
    # only change. Nothing conflicted; nothing forced this.
    repo.merge_but("feature", "Merge branch 'feature'", **{"a.txt": "A\n"})

    merge = only(repo)
    assert merge.verdict == EVIL
    assert len(merge.findings) == 1
    finding = merge.findings[0]
    assert (finding.path, finding.kind) == ("a.txt", DROPPED)
    assert finding.kept_parent == 1
    assert finding.lost_parent == 2


def test_dropping_the_first_parents_side(repo):
    two_branches(repo)
    # This time it is main's own change that goes missing.
    repo.merge_but("feature", "Merge branch 'feature'", **{"b.txt": "B\n"})

    finding = only(repo).findings[0]
    assert (finding.path, finding.kind) == ("b.txt", DROPPED)
    assert finding.kept_parent == 2
    assert finding.lost_parent == 1


def test_content_invented_during_the_merge(repo):
    two_branches(repo)
    repo.merge_but(
        "feature", "Merge branch 'feature'", **{"a.txt": "A-something-else\n"}
    )

    merge = only(repo)
    assert merge.verdict == EVIL
    finding = merge.findings[0]
    assert (finding.path, finding.kind) == ("a.txt", FOREIGN)
    assert finding.kept_parent is None


def test_a_file_added_during_the_merge_is_foreign(repo):
    two_branches(repo)
    repo.merge_but(
        "feature", "Merge branch 'feature'", **{"notes.md": "written mid-merge\n"}
    )

    finding = only(repo).findings[0]
    assert (finding.path, finding.kind) == ("notes.md", FOREIGN)


def test_a_file_deleted_during_the_merge_is_dropped(repo):
    """Deleting a file only one parent added is that parent's change, gone."""
    repo.commit("base", **{"keep.txt": "K\n"})
    repo.branch("feature")
    repo.commit("feat: add two files", **{"new.txt": "N\n", "also.txt": "A\n"})
    repo.checkout("main")
    repo.commit("main: unrelated", **{"keep.txt": "K2\n"})
    # also.txt survives, so the tree is not simply main's tree.
    repo.merge_but("feature", "Merge branch 'feature'", **{"new.txt": None})

    merge = only(repo)
    assert merge.verdict == EVIL
    finding = merge.findings[0]
    assert (finding.path, finding.kind) == ("new.txt", DROPPED)
    assert finding.kept_parent == 1


def test_resolving_a_conflict_is_not_a_finding(repo):
    repo.commit("base", **{"f.txt": "1\n2\n3\n"})
    repo.branch("feature")
    repo.commit("feat", **{"f.txt": "1\nFEATURE\n3\n"})
    repo.checkout("main")
    repo.commit("main", **{"f.txt": "1\nMAIN\n3\n"})
    repo.merge_but("feature", "Merge branch 'feature'", **{"f.txt": "1\nBOTH\n3\n"})

    merge = only(repo)
    assert merge.verdict == RESOLVED
    assert merge.findings == []
    assert merge.conflicted == frozenset({"f.txt"})
    assert merge.resolved_paths == frozenset({"f.txt"})


def test_a_conflict_is_no_cover_for_changing_something_else(repo):
    """The case a per-merge "was it conflicted?" check would wave through."""
    repo.commit("base", **{"f.txt": "1\n2\n3\n", "other.txt": "O\n"})
    repo.branch("feature")
    repo.commit("feat", **{"f.txt": "1\nFEATURE\n3\n", "other.txt": "O-feature\n"})
    repo.checkout("main")
    repo.commit("main", **{"f.txt": "1\nMAIN\n3\n"})
    # f.txt genuinely conflicted. other.txt did not, and was reverted anyway.
    repo.merge_but(
        "feature",
        "Merge branch 'feature'",
        **{"f.txt": "1\nBOTH\n3\n", "other.txt": "O\n"},
    )

    merge = only(repo)
    assert merge.verdict == EVIL
    assert merge.conflicted == frozenset({"f.txt"})
    assert [(f.path, f.kind) for f in merge.findings] == [("other.txt", DROPPED)]


def test_merge_s_ours_is_reported_once_not_per_file(repo):
    repo.commit("base", **{"a.txt": "A\n"})
    repo.branch("feature")
    repo.commit(
        "feat", **{"a.txt": "A-feature\n", "new.txt": "N\n", "more.txt": "M\n"}
    )
    repo.checkout("main")
    repo.commit("main", **{"b.txt": "B\n"})
    repo.merge("feature", "Merge branch 'feature'", "-s", "ours")

    merge = only(repo)
    assert merge.verdict == WHOLESALE
    assert merge.kept_parent == 1
    assert merge.wholesale_paths == 3
    assert merge.findings == []


def test_a_merge_that_keeps_the_second_parent_wholesale(repo):
    two_branches(repo)
    # "Take theirs entirely": start the merge, then reset the whole tree to
    # the other parent. main's own change to b.txt goes with it.
    repo.git("merge", "--no-commit", "--no-ff", "feature", check=False)
    repo.git("read-tree", "-u", "--reset", "feature")
    repo.git("commit", "-q", "-m", "Merge branch 'feature'")

    merge = only(repo)
    assert merge.verdict == WHOLESALE
    assert merge.kept_parent == 2


def test_an_octopus_is_skipped_loudly(repo):
    repo.commit("base", **{"x.txt": "x\n"})
    repo.branch("f1")
    repo.commit("f1", **{"f1.txt": "1\n"})
    repo.branch("f2", "main")
    repo.commit("f2", **{"f2.txt": "2\n"})
    repo.checkout("main")
    repo.commit("main", **{"m.txt": "m\n"})
    repo.git("merge", "-q", "--no-ff", "-m", "octopus", "f1", "f2")

    merge = only(repo)
    assert merge.verdict == SKIPPED
    assert "octopus" in merge.reason
    assert "3 parents" in merge.reason


def test_unrelated_histories_are_skipped_not_guessed(repo):
    repo.commit("base", **{"a.txt": "A\n"})
    repo.git("checkout", "-q", "--orphan", "other")
    repo.git("rm", "-rqf", ".", check=False)
    repo.commit("orphan root", **{"z.txt": "Z\n"})
    repo.checkout("main")
    repo.git(
        "merge", "-q", "--no-ff", "--allow-unrelated-histories", "-m", "join", "other"
    )

    merge = only(repo)
    assert merge.verdict == SKIPPED
    assert "unrelated histories" in merge.reason
    assert not merge.reason.startswith("fatal:")


def test_non_merge_commits_are_not_looked_at(repo):
    repo.commit("one", **{"a.txt": "A\n"})
    repo.commit("two", **{"a.txt": "AA\n"})

    report = check(str(repo.path), ["HEAD"])
    assert report.merges == []
    assert report.findings == []


def test_a_range_limits_what_is_checked(repo):
    two_branches(repo)
    repo.merge_but("feature", "first merge", **{"a.txt": "A\n"})
    before = repo.head
    repo.branch("second")
    repo.commit("more", **{"c.txt": "C\n"})
    repo.checkout("main")
    repo.commit("main again", **{"d.txt": "D\n"})
    repo.merge("second", "second merge")

    assert len(check(str(repo.path), ["HEAD"]).merges) == 2
    assert len(check(str(repo.path), [f"{before}..HEAD"]).merges) == 1


def test_check_merge_takes_a_named_revision(repo):
    two_branches(repo)
    sha = repo.merge_but("feature", "Merge branch 'feature'", **{"a.txt": "A\n"})

    merge = check_merge(sha, str(repo.path))
    assert merge.verdict == EVIL
    assert merge.subject.sha == sha
    assert merge.parents == repo.git("rev-list", "-1", "--parents", sha).split()[1:]
