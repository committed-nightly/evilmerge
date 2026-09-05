"""Exit codes and what the user actually sees.

The exit codes carry the most weight here, because the main use is a CI gate
and a gate is only worth having if 0 really does mean "looked, found nothing".
"""

from __future__ import annotations

import json

import pytest

from evilmerge.cli import main


def run(capsys, *argv) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def evil_repo(repo):
    """One evil merge and one honest one."""
    repo.commit("base", **{"a.txt": "A\n", "b.txt": "B\n", "c.txt": "C\n"})
    repo.branch("feature")
    repo.commit("feat", **{"a.txt": "A-feature\n", "c.txt": "C-feature\n"})
    repo.checkout("main")
    repo.commit("main", **{"b.txt": "B-main\n"})
    repo.merge_but("feature", "Merge branch 'feature'", **{"a.txt": "A\n"})

    repo.branch("docs")
    repo.commit("docs", **{"README.md": "docs\n"})
    repo.checkout("main")
    repo.commit("more", **{"b.txt": "B-main-2\n"})
    repo.merge("docs", "Merge branch 'docs'")
    return repo


def test_a_clean_history_is_zero(repo, capsys):
    repo.commit("base", **{"a.txt": "A\n"})
    repo.branch("feature")
    repo.commit("feat", **{"f.txt": "F\n"})
    repo.checkout("main")
    repo.commit("main", **{"b.txt": "B\n"})
    repo.merge("feature", "Merge branch 'feature'")

    code, out, _ = run(capsys, "-C", str(repo.path))
    assert code == 0
    assert "out of 1 merge checked" in out


def test_a_dropped_change_is_one(repo, capsys):
    evil_repo(repo)

    code, out, _ = run(capsys, "-C", str(repo.path))
    assert code == 1
    assert "a.txt" in out
    assert "dropped" in out
    assert "1 merge changed something on its own, of 2 checked." in out
    # The honest merge is not mentioned at all.
    assert "Merge branch 'docs'" not in out


def test_no_merges_at_all_says_so_rather_than_claiming_a_pass(repo, capsys):
    """A rebase-only repo is a real 0, but it must not read as "all clear"."""
    repo.commit("one", **{"a.txt": "A\n"})
    repo.commit("two", **{"a.txt": "AA\n"})

    code, out, _ = run(capsys, "-C", str(repo.path))
    assert code == 0
    assert out.strip() == "No merge commits in this range. Nothing to check."


def test_not_a_repository_is_two(tmp_path, capsys):
    code, _, err = run(capsys, "-C", str(tmp_path))
    assert code == 2
    assert "not a git repository" in err


def test_an_unknown_revision_is_two(repo, capsys):
    evil_repo(repo)

    code, _, err = run(capsys, "-C", str(repo.path), "no-such-branch")
    assert code == 2
    assert "no such revision: no-such-branch" in err


def test_explain_on_an_ordinary_commit_is_two(repo, capsys):
    """Not 0. Nobody looked at a merge, because that wasn't one."""
    repo.commit("one", **{"a.txt": "A\n"})
    repo.commit("two", **{"a.txt": "AA\n"})

    code, _, err = run(capsys, "-C", str(repo.path), "--explain", "HEAD")
    assert code == 2
    assert "not a merge commit" in err
    assert "1 parent" in err


def test_explain_on_a_root_commit_is_two(repo, capsys):
    """The root commit has no parents at all, and says "0 parents", not "0 parent"."""
    repo.commit("one", **{"a.txt": "A\n"})

    code, _, err = run(capsys, "-C", str(repo.path), "--explain", "HEAD")
    assert code == 2
    assert "has 0 parents" in err


def test_explain_prints_the_diff_against_the_recomputed_merge(repo, capsys):
    evil_repo(repo)
    sha = repo.git("rev-list", "--merges", "HEAD").split()[-1]

    code, out, _ = run(capsys, "-C", str(repo.path), "--explain", sha)
    assert code == 1
    assert "merged again:" in out
    assert "diff --git a/a.txt b/a.txt" in out
    # The dropped line itself, so the reader can see what went missing.
    assert "-A-feature" in out


def test_a_range_is_passed_through_to_git(repo, capsys):
    evil_repo(repo)
    first_merge = repo.git("rev-list", "--merges", "HEAD").split()[-1]

    code, out, _ = run(capsys, "-C", str(repo.path), f"{first_merge}..HEAD")
    assert code == 0
    assert "out of 1 merge checked" in out


def test_resolutions_are_hidden_by_default_and_shown_with_all(repo, capsys):
    repo.commit("base", **{"f.txt": "1\n2\n3\n"})
    repo.branch("feature")
    repo.commit("feat", **{"f.txt": "1\nFEATURE\n3\n"})
    repo.checkout("main")
    repo.commit("main", **{"f.txt": "1\nMAIN\n3\n"})
    repo.merge_but("feature", "Merge branch 'feature'", **{"f.txt": "1\nBOTH\n3\n"})

    code, out, _ = run(capsys, "-C", str(repo.path))
    assert code == 0
    assert "f.txt" not in out
    assert "1 conflict resolution not shown" in out

    code, out, _ = run(capsys, "-C", str(repo.path), "--all")
    assert code == 0
    assert "resolved  f.txt" in out


def test_skipped_merges_are_counted_in_the_summary(repo, capsys):
    repo.commit("base", **{"x.txt": "x\n"})
    repo.branch("f1")
    repo.commit("f1", **{"f1.txt": "1\n"})
    repo.branch("f2", "main")
    repo.commit("f2", **{"f2.txt": "2\n"})
    repo.checkout("main")
    repo.commit("main", **{"m.txt": "m\n"})
    repo.git("merge", "-q", "--no-ff", "-m", "octopus", "f1", "f2")

    code, out, _ = run(capsys, "-C", str(repo.path))
    # An unchecked merge is not a failure, but it is never silent.
    assert code == 0
    assert "1 not checkable" in out


def test_json_is_machine_readable(repo, capsys):
    evil_repo(repo)

    code, out, _ = run(capsys, "-C", str(repo.path), "--json")
    assert code == 1
    payload = json.loads(out)
    assert payload["checked"] == 2
    assert payload["found"] == 1
    # Clean merges are left out; there is nothing to say about them.
    assert len(payload["merges"]) == 1
    merge = payload["merges"][0]
    assert merge["verdict"] == "evil"
    assert merge["findings"] == [
        {
            "path": "a.txt",
            "kind": "dropped",
            "status": "M",
            "kept_parent": 1,
            "lost_parent": 2,
        }
    ]
    assert len(merge["parents"]) == 2


def test_json_reports_a_skip_with_its_reason(repo, capsys):
    repo.commit("base", **{"a.txt": "A\n"})
    repo.git("checkout", "-q", "--orphan", "other")
    repo.git("rm", "-rqf", ".", check=False)
    repo.commit("orphan", **{"z.txt": "Z\n"})
    repo.checkout("main")
    repo.git(
        "merge", "-q", "--no-ff", "--allow-unrelated-histories", "-m", "join", "other"
    )

    code, out, _ = run(capsys, "-C", str(repo.path), "--json")
    assert code == 0
    merge = json.loads(out)["merges"][0]
    assert merge["verdict"] == "skipped"
    assert "unrelated histories" in merge["reason"]


def test_wholesale_names_the_parent_and_the_count(repo, capsys):
    repo.commit("base", **{"a.txt": "A\n"})
    repo.branch("feature")
    repo.commit("feat", **{"a.txt": "A2\n", "new.txt": "N\n"})
    repo.checkout("main")
    repo.commit("main", **{"b.txt": "B\n"})
    repo.merge("feature", "Merge branch 'feature'", "-s", "ours")

    code, out, _ = run(capsys, "-C", str(repo.path))
    assert code == 1
    assert "takes parent 1 and nothing else" in out
    assert "2 paths a real merge would have kept" in out


def test_a_deletion_is_not_described_as_written_content(repo, capsys):
    """"Content written during the merge" is the wrong news for a delete."""
    repo.commit("base", **{"a.txt": "A\n", "gone.txt": "G\n"})
    repo.branch("feature")
    repo.commit("feat", **{"a.txt": "A-feature\n"})
    repo.checkout("main")
    repo.commit("main", **{"b.txt": "B\n"})
    # Neither side deletes gone.txt. The merge does.
    repo.merge_but("feature", "Merge branch 'feature'", **{"gone.txt": None})

    code, out, _ = run(capsys, "-C", str(repo.path))
    assert code == 1
    assert "foreign  gone.txt" in out
    assert "Deleted by the merge" in out
    assert "written during the merge itself" not in out


def test_a_summary_with_an_aside_is_punctuated_once(repo, capsys):
    repo.commit("base", **{"f.txt": "1\n2\n3\n"})
    repo.branch("feature")
    repo.commit("feat", **{"f.txt": "1\nFEATURE\n3\n"})
    repo.checkout("main")
    repo.commit("main", **{"f.txt": "1\nMAIN\n3\n"})
    repo.merge_but("feature", "Merge", **{"f.txt": "1\nBOTH\n3\n"})

    _, out, _ = run(capsys, "-C", str(repo.path))
    assert "checked. (" not in out
    assert out.strip().endswith("(1 conflict resolution not shown).")
