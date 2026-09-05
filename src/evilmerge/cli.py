"""Command line entry point.

Exit codes, because the main use for this is a CI gate:

    0  no merge in the range changed anything on its own
    1  at least one did
    2  the check could not run at all

2 is deliberately not 1 and very deliberately not 0. Not a repository, a
revision range that doesn't resolve, no git on PATH: all of those mean nobody
looked, and "nobody looked" reported as a green tick is worse than no check.

A range containing no merge commits at all is a **0**, and says so in as many
words. Plenty of repositories rebase and genuinely have no merges; that is a
real pass and not a misconfiguration. The wording matters more than the code
here, which is why the summary line never says "clean" without also saying how
many merges that was out of.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import gitcmd
from .core import (
    CLEAN,
    DROPPED,
    EVIL,
    FOREIGN,
    RESOLVED,
    SKIPPED,
    WHOLESALE,
    MergeReport,
    Report,
    check,
    check_merge,
)
from .gitcmd import GitError

EXIT_OK = 0
EXIT_FOUND = 1
EXIT_ERROR = 2

# Keyed on git's status letter for the deviation, because "the merge wrote
# this" and "the merge deleted this" are not the same news and a checker that
# says the first when it means the second gets ignored, correctly.
DROPPED_DETAIL = {
    "M": (
        "{Comes} out exactly as parent {kept} had {it}. Parent {lost}'s "
        "changes to {it} are not in the merge and nothing conflicted."
    ),
    "D": (
        "Missing from the merge, exactly as in parent {kept}. Parent {lost} "
        "still had {it}, a merge of the two keeps {it}, and nothing "
        "conflicted."
    ),
    "A": (
        "In the merge exactly as parent {kept} had {it}, where a merge of "
        "the two leaves {it} out. Parent {lost} does not have {it}."
    ),
}

DROPPED_CODA = (
    "`git show --cc` on this merge shows nothing here -- it only prints hunks "
    "that differ from every parent, and {match_clause}."
)

FOREIGN_DETAIL = {
    "M": (
        "Not what merging the parents produces, and not what either parent "
        "had. Content written during the merge itself."
    ),
    "D": (
        "Deleted by the merge. Merging the parents keeps {it}, and this "
        "deletion is in neither parent -- it happened during the merge."
    ),
    "A": (
        "Added by the merge. Merging the parents does not produce {it} and "
        "neither parent has {it}; it was written during the merge."
    ),
}

WHOLESALE_DETAIL = (
    "The merge's tree is byte-identical to parent {kept}. Everything parent "
    "{lost} contributed is absent -- {n} path{s} a real merge would have kept. "
    "This is what `git merge -s ours` records, and what resolving a big "
    "conflict with \"just take ours\" records too."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evilmerge",
        description=(
            "Find merge commits that quietly changed something git would not "
            "have: changes dropped without a conflict, and content that came "
            "from neither side."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Every merge in the range is merged again, now, with your git. "
            "Anything the recorded merge does that the fresh one doesn't was "
            "put there by hand.\n"
        ),
    )
    parser.add_argument(
        "range",
        nargs="*",
        default=None,
        help="revision range, as git rev-list takes it (default: HEAD)",
    )
    parser.add_argument(
        "-C",
        "--repo",
        default=".",
        metavar="PATH",
        help="run as if evilmerge was started in PATH (default: .)",
    )
    parser.add_argument(
        "--since",
        metavar="DATE",
        help="only merges after DATE; the usual way to keep this quick",
    )
    parser.add_argument(
        "--explain",
        metavar="REV",
        help="one merge, in full, with the diff against the recomputed merge",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="also list conflict resolutions, which are not findings",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = sys.stdout

    try:
        cwd = gitcmd.repo_root(args.repo)
    except GitError as exc:
        return _fail(exc, args.repo)

    try:
        if args.explain:
            sha = gitcmd.resolve(args.explain, cwd)
            # Pointing --explain at an ordinary commit has to be an error.
            # Reported as "skipped" it would exit 0, and a checker that exits
            # 0 because it never ran is the thing this whole family is loud
            # about not doing.
            count = len(gitcmd.parents(sha, cwd))
            if count < 2:
                raise GitError(
                    f"not a merge commit: {args.explain} has "
                    f"{count} parent{'' if count == 1 else 's'}"
                )
            report = Report([check_merge(sha, cwd)])
        else:
            report = check(cwd, _range_args(args, cwd))
    except GitError as exc:
        return _fail(exc, args.repo)

    if args.json:
        json.dump(_as_json(report), out, indent=2)
        out.write("\n")
    elif args.explain:
        _print_explained(report.merges[0], cwd, out)
    else:
        _print_report(report, args.all, out)

    return EXIT_FOUND if report.findings else EXIT_OK


def _range_args(args: argparse.Namespace, cwd: str) -> list[str]:
    rev_args: list[str] = []
    if args.since:
        rev_args.append(f"--since={args.since}")
    rev_args.extend(args.range or ["HEAD"])
    # Resolve the plain names now so a typo is an error rather than an empty
    # list of merges reported as a pass. Ranges and options are left to git.
    for token in args.range or ["HEAD"]:
        if not token.startswith("-") and ".." not in token and "^" not in token:
            gitcmd.resolve(token, cwd)
    return rev_args


def _fail(exc: GitError, where: str) -> int:
    detail = str(exc).strip() or "git failed"
    if "not a git repository" in detail:
        detail = f"not a git repository: {os.path.abspath(where)}"
    print(f"evilmerge: {detail}", file=sys.stderr)
    return EXIT_ERROR


# --------------------------------------------------------------------------
# printing


def _header(merge: MergeReport) -> str:
    subject = merge.subject
    return f"{subject.short}  {subject.subject}"


def _byline(merge: MergeReport) -> str:
    return f"         {merge.subject.date}  {merge.subject.author}"


def _wrap(text: str, indent: str) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width=78 - len(indent), initial_indent=indent,
                         subsequent_indent=indent)


def _print_report(report: Report, show_all: bool, out) -> None:
    shown = [m for m in report.merges if m.is_finding]
    if show_all:
        shown = [m for m in report.merges if m.verdict != CLEAN]

    for merge in shown:
        _print_merge(merge, out)
        out.write("\n")

    out.write(_summary(report) + "\n")


def _print_merge(merge: MergeReport, out) -> None:
    out.write(_header(merge) + "\n")
    out.write(_byline(merge) + "\n")

    if merge.verdict == WHOLESALE:
        kept = merge.kept_parent
        lost = 3 - kept
        out.write(f"  wholesale  takes parent {kept} and nothing else\n")
        detail = WHOLESALE_DETAIL.format(
            kept=kept,
            lost=lost,
            n=merge.wholesale_paths,
            s="" if merge.wholesale_paths == 1 else "s",
        )
        for line in _wrap(detail, "    "):
            out.write(line + "\n")
        return

    if merge.verdict == SKIPPED:
        out.write(f"  skipped  {merge.reason}\n")
        for line in _wrap(
            "Not checked. This merge is neither clean nor evil as far as "
            "evilmerge is concerned; nobody looked at it.",
            "    ",
        ):
            out.write(line + "\n")
        return

    if merge.verdict == RESOLVED:
        paths = ", ".join(sorted(merge.resolved_paths))
        out.write(f"  resolved  {paths}\n")
        for line in _wrap(
            "Conflicted, and the resolution only touched the files that "
            "conflicted. This is what resolving a conflict looks like and it "
            "is not a finding.",
            "    ",
        ):
            out.write(line + "\n")
        return

    for kind, kept, status, paths in _group(merge):
        out.write(f"  {kind}  {', '.join(paths)}\n")
        many = len(paths) > 1
        words = {
            "kept": kept,
            "lost": 3 - kept if kept else None,
            "Comes": "Come" if many else "Comes",
            "it": "them" if many else "it",
        }
        if kind == DROPPED:
            detail = DROPPED_DETAIL[status].format(**words)
            detail += " " + DROPPED_CODA.format(
                match_clause=(
                    f"these match parent {kept} exactly"
                    if many
                    else f"this one matches parent {kept} exactly"
                )
            )
        else:
            detail = FOREIGN_DETAIL[status].format(**words)
        for line in _wrap(detail, "    "):
            out.write(line + "\n")

    if merge.conflicted:
        n = len(merge.conflicted)
        for line in _wrap(
            f"This merge did have {n} conflicted path{'' if n == 1 else 's'}, "
            "but not the one" + ("" if len(merge.findings) == 1 else "s")
            + " above.",
            "    ",
        ):
            out.write(line + "\n")


def _group(merge: MergeReport) -> list[tuple[str, int | None, str, list[str]]]:
    """Findings gathered so that one sentence can honestly cover many paths.

    Grouped by status as well as by kind and parent: two paths only share a
    description if the same thing happened to both.
    """
    buckets: dict[tuple[str, int | None, str], list[str]] = {}
    for finding in merge.findings:
        key = (finding.kind, finding.kept_parent, finding.status)
        buckets.setdefault(key, []).append(finding.path)
    order = {DROPPED: 0, FOREIGN: 1}
    return [
        (kind, kept, status, sorted(paths))
        for (kind, kept, status), paths in sorted(
            buckets.items(),
            key=lambda item: (order[item[0][0]], item[0][1] or 0, item[0][2]),
        )
    ]


def _summary(report: Report) -> str:
    total = len(report.merges)
    if total == 0:
        return "No merge commits in this range. Nothing to check."

    found = len(report.findings)
    merges = "merge" if total == 1 else "merges"
    if found == 0:
        head = f"No merge changed anything on its own, out of {total} {merges} checked."
    else:
        head = (
            f"{found} {'merge' if found == 1 else 'merges'} changed something "
            f"on {'its' if found == 1 else 'their'} own, of {total} checked."
        )

    asides = []
    resolutions = len(report.resolutions)
    if resolutions:
        asides.append(
            f"{resolutions} conflict resolution"
            f"{'' if resolutions == 1 else 's'} not shown"
        )
    skipped = len(report.skipped)
    if skipped:
        asides.append(f"{skipped} not checkable")
    if asides:
        head = head.rstrip(".") + " (" + ", ".join(asides) + ")."
    return head


def _print_explained(merge: MergeReport, cwd: str, out) -> None:
    _print_merge(merge, out)
    if merge.verdict == CLEAN:
        out.write("  clean  merging these parents again produces this tree\n")
    out.write("\n")
    out.write(f"parents:      {'  '.join(merge.parents)}\n")
    if merge.clean_tree:
        out.write(f"merged again: {merge.clean_tree}\n")
    if merge.conflicted:
        out.write(f"conflicted:   {', '.join(sorted(merge.conflicted))}\n")

    paths = [f.path for f in merge.findings]
    if not paths:
        return
    out.write("\nthe merge, against merging its parents again:\n\n")
    out.write(gitcmd.diff_paths(merge.clean_tree, merge.subject.sha, paths, cwd))


def _as_json(report: Report) -> dict:
    return {
        "checked": len(report.merges),
        "found": len(report.findings),
        "merges": [
            {
                "commit": merge.subject.sha,
                "subject": merge.subject.subject,
                "date": merge.subject.date,
                "author": merge.subject.author,
                "parents": merge.parents,
                "verdict": merge.verdict,
                "reason": merge.reason or None,
                "kept_parent": merge.kept_parent,
                "conflicted": sorted(merge.conflicted),
                "findings": [
                    {
                        "path": finding.path,
                        "kind": finding.kind,
                        "status": finding.status,
                        "kept_parent": finding.kept_parent,
                        "lost_parent": finding.lost_parent,
                    }
                    for finding in merge.findings
                ],
            }
            for merge in report.merges
            if merge.verdict != CLEAN
        ],
    }


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
