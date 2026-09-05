"""The claim this tool is built on, checked against the git that is installed.

evilmerge exists because a merge can throw away one side of a file that never
conflicted, and no ordinary view of history shows it. The README says that in
so many words. If a future git starts showing it, the README is wrong and this
file is where that gets noticed -- not in a bug report from someone who read
the pitch and couldn't reproduce it.

These tests assert about git's output, not about evilmerge's.
"""

from __future__ import annotations

from evilmerge.core import DROPPED, EVIL, check


def build_a_quiet_drop(repo) -> str:
    """A merge that discards a change to a file that never conflicted."""
    repo.commit("base", **{"a.txt": "A\n", "b.txt": "B\n", "c.txt": "C\n"})
    repo.branch("feature")
    repo.commit("feat", **{"a.txt": "A-feature\n", "c.txt": "C-feature\n"})
    repo.checkout("main")
    repo.commit("main", **{"b.txt": "B-main\n"})
    repo.git("merge", "--no-commit", "--no-ff", "feature", check=False)
    repo.write("a.txt", "A\n")  # the feature branch's work on a.txt, undone
    repo.git("add", "-A")
    repo.git("commit", "-q", "-m", "Merge branch 'feature'")
    return repo.head


def test_git_show_cc_says_nothing_about_it(repo):
    sha = build_a_quiet_drop(repo)

    shown = repo.git("show", "--cc", sha)
    assert "a.txt" not in shown, (
        "git show --cc now reports the dropped file; the README's central "
        "claim needs rewriting.\n" + shown
    )
    # Not merely that a.txt is absent -- there is no diff at all.
    assert "diff --" not in shown


def test_diff_against_the_first_parent_does_not_mention_it(repo):
    """From main's side the merge looks like an ordinary, honest merge.

    c.txt -- the feature branch's other change, which the merge did keep --
    shows up exactly as it should. a.txt does not show up at all, because the
    merge left it as main already had it. There is nothing here to notice.
    """
    sha = build_a_quiet_drop(repo)

    names = repo.git("diff", "--name-only", f"{sha}^1", sha).split()
    assert names == ["c.txt"]


def test_diff_against_the_second_parent_shows_it_but_not_as_wrong(repo):
    """The one ordinary view that does contain a.txt does not flag it.

    `git diff MERGE^2 MERGE` is "what did the other side bring in", and here
    it lists a.txt next to b.txt. b.txt is a real change main made. a.txt was
    invented during the merge. They are indistinguishable without going and
    reading what main actually did, which is the work evilmerge does for you.
    """
    sha = build_a_quiet_drop(repo)

    names = repo.git("diff", "--name-only", f"{sha}^2", sha).split()
    assert names == ["a.txt", "b.txt"]
    # And main never touched a.txt, so its presence here is entirely the
    # merge's doing.
    main_changes = repo.git("diff", "--name-only", f"{sha}^1~1", f"{sha}^1").split()
    assert main_changes == ["b.txt"]


def test_evilmerge_does_report_it(repo):
    sha = build_a_quiet_drop(repo)

    merge = check(str(repo.path), [sha]).merges[0]
    assert merge.verdict == EVIL
    assert [(f.path, f.kind) for f in merge.findings] == [("a.txt", DROPPED)]
