"""A tiny git repository builder.

Every test here builds a real repository and runs real git against it. That is
slower than faking the plumbing, and it is the only way to test this tool
honestly: evilmerge's whole claim is "here is what your git does with these
parents", so a test against a mocked git would only prove that the mock agrees
with itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


class Repo:
    def __init__(self, path: Path):
        self.path = path

    # -- plumbing ---------------------------------------------------------

    def git(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.path,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and proc.returncode != 0:
            raise AssertionError(
                f"git {' '.join(args)} failed ({proc.returncode}):\n{proc.stderr}"
            )
        return proc.stdout

    def write(self, name: str, content: str) -> None:
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def remove(self, name: str) -> None:
        (self.path / name).unlink()

    # -- porcelain --------------------------------------------------------

    def commit(self, message: str, **files: str) -> str:
        for name, content in files.items():
            self.write(name.replace("__", "/"), content)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.head

    def branch(self, name: str, start: str | None = None) -> None:
        self.git("checkout", "-q", "-b", name, *( [start] if start else []))

    def checkout(self, name: str) -> None:
        self.git("checkout", "-q", name)

    @property
    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def merge(self, other: str, message: str, *flags: str) -> str:
        """A merge that goes through cleanly, or an exploded test."""
        self.git("merge", "-q", "--no-ff", *flags, "-m", message, other)
        return self.head

    def merge_but(self, other: str, message: str, **files: str) -> str:
        """Start a merge, overwrite the result by hand, then commit it.

        This is the evil merge: git works out an answer, a human replaces some
        of it, and the commit records the human's version with no sign that
        anything was overruled.
        """
        self.git("merge", "--no-commit", "--no-ff", other, check=False)
        for name, content in files.items():
            path = name.replace("__", "/")
            if content is None:
                self.git("rm", "-q", "-f", path)
            else:
                self.write(path, content)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.head


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    path = tmp_path / "repo"
    path.mkdir()
    r = Repo(path)
    r.git("init", "-q", "-b", "main", ".")
    r.git("config", "user.name", "Tester")
    r.git("config", "user.email", "tester@example.invalid")
    r.git("config", "commit.gpgsign", "false")
    return r
