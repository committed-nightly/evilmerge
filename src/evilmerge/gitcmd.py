"""Thin wrappers over the ``git`` binary.

Everything here shells out. There is no libgit2, no dulwich, no reimplemented
merge algorithm -- the entire point of this tool is to ask *git* what it would
have done and compare that to what is recorded, so reimplementing the merge
would defeat it. If your git merges differently to mine, evilmerge should say
what your git says.

``-z`` is used for every list of paths. A path containing a newline is legal
in git, rare, and precisely the kind of thing that would make a checker
silently miscount.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

# git merge-tree's own exit codes, which are not the usual 0/1.
MERGE_TREE_CLEAN = 0
MERGE_TREE_CONFLICT = 1


class GitError(RuntimeError):
    """git was missing, unhappy, or pointed at something that isn't a repo."""


@dataclass(frozen=True)
class Completed:
    """A finished git process, for the callers that care about the code."""

    returncode: int
    stdout: str
    stderr: str


def _spawn(args: list[str], cwd: str) -> Completed:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, check=False
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on the box
        raise GitError("git is not on PATH") from exc
    return Completed(
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


def run_git(args: list[str], cwd: str) -> str:
    """Run git and return stdout, or raise GitError."""
    done = _spawn(args, cwd)
    if done.returncode != 0:
        raise GitError(done.stderr.strip() or f"git {' '.join(args)} failed")
    return done.stdout


def repo_root(cwd: str) -> str:
    """Absolute path of the working tree containing ``cwd``."""
    return run_git(["rev-parse", "--show-toplevel"], cwd).strip()


def resolve(rev: str, cwd: str) -> str:
    """Resolve a revision to a full commit sha, raising GitError if unknown.

    ``--quiet`` means git says nothing at all when the name is unknown, so
    letting run_git raise would report the failed command line rather than the
    problem. The message people need is the name they typed.
    """
    try:
        out = run_git(
            ["rev-parse", "--verify", "--quiet", rev + "^{commit}"], cwd
        ).strip()
    except GitError as exc:
        if str(exc).startswith("git "):
            raise GitError(f"no such revision: {rev}") from exc
        raise
    if not out:
        raise GitError(f"no such revision: {rev}")
    return out


def merge_commits(cwd: str, range_args: list[str]) -> list[str]:
    """Merge commits in a revision range, oldest last (rev-list order)."""
    out = run_git(["rev-list", "--merges", *range_args], cwd)
    return [line for line in out.split("\n") if line]


def parents(rev: str, cwd: str) -> list[str]:
    out = run_git(["rev-list", "-1", "--parents", rev], cwd).strip()
    return out.split()[1:]


@dataclass(frozen=True)
class Subject:
    """Just enough of a commit to print a recognisable line for it."""

    sha: str
    short: str
    date: str
    author: str
    subject: str


def describe(rev: str, cwd: str) -> Subject:
    fmt = "%H%x00%h%x00%as%x00%an%x00%s"
    out = run_git(["show", "-s", f"--format={fmt}", rev], cwd)
    sha, short, date, author, subject = out.split("\0", 4)
    return Subject(sha, short, date, author, subject.rstrip("\n"))


@dataclass(frozen=True)
class MergeTree:
    """What git would produce for a merge, and what fought back on the way."""

    tree: str
    conflicted: frozenset[str]

    @property
    def had_conflicts(self) -> bool:
        return bool(self.conflicted)


def merge_tree(parent_a: str, parent_b: str, cwd: str) -> MergeTree:
    """Re-run the merge of two commits and return the tree it produces.

    ``--write-tree --name-only -z`` gives a NUL-separated stream of: the tree
    object name, then one entry per conflicted path, then an empty field, then
    informational messages we don't need. Conflicted files land in the tree
    with conflict markers in them, which is why the caller has to know which
    paths they are -- a conflicted path *always* differs from the recorded
    merge, and that difference means nothing.

    Raises GitError for the merges git refuses to attempt at all: unrelated
    histories, most obviously.
    """
    done = _spawn(
        ["merge-tree", "--write-tree", "--name-only", "-z", parent_a, parent_b],
        cwd,
    )
    if done.returncode not in (MERGE_TREE_CLEAN, MERGE_TREE_CONFLICT):
        raise GitError(done.stderr.strip() or "git merge-tree failed")

    fields = done.stdout.split("\0")
    tree = fields[0].strip()
    if not tree:  # pragma: no cover - would mean git changed its output format
        raise GitError("git merge-tree produced no tree")

    conflicted: list[str] = []
    for field in fields[1:]:
        if field == "":
            break
        conflicted.append(field)
    return MergeTree(tree, frozenset(conflicted))


def changed_paths(tree_a: str, tree_b: str, cwd: str) -> frozenset[str]:
    """Paths that differ between two trees or commits.

    ``--no-renames`` on purpose. A rename detected here would be reported as
    one entry covering two paths, and both paths are individually interesting:
    the question is always "did this path come out different", never "what does
    this change mean".
    """
    out = run_git(
        [
            "diff-tree",
            "-r",
            "-z",
            "--no-renames",
            "--name-only",
            tree_a,
            tree_b,
        ],
        cwd,
    )
    return frozenset(path for path in out.split("\0") if path)


def diff_paths(tree_a: str, tree_b: str, paths: list[str], cwd: str) -> str:
    """A human-readable diff between two trees, limited to some paths."""
    return run_git(
        ["diff", "--no-renames", tree_a, tree_b, "--", *paths], cwd
    )
