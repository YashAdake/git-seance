"""Thin, safe wrapper around the git CLI.

Every call passes arguments as a list (never through a shell) and pins the
config that changes git's output format, so the parsers see the same text on
every machine.
"""

from __future__ import annotations

import os
import subprocess

# Config that keeps output parseable and identical everywhere.
_PINNED_CONFIG = (
    "-c", "core.quotePath=false",
    "-c", "color.ui=never",
    "-c", "diff.renameLimit=3000",
    "-c", "log.showSignature=false",
)


class GitError(RuntimeError):
    """A git command failed. The message is safe to show to a user."""


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # never hang waiting for credentials
    env["GIT_OPTIONAL_LOCKS"] = "0"   # read-only: do not touch the index
    env["LC_ALL"] = "C"
    return env


def run_git(repo: str, args: list[str], *, timeout: float = 180, binary: bool = False):
    """Run `git -C repo <args>` and return stdout (str, or bytes if binary)."""
    cmd = ["git", "-C", repo, *_PINNED_CONFIG, *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=_env())
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {timeout:.0f}s") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitError(err[-1] if err else f"git {args[0]} failed ({proc.returncode})")
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


def run_git_plain(args: list[str], *, timeout: float = 600) -> str:
    """Run git without -C (used for clone)."""
    try:
        proc = subprocess.run(["git", *args], capture_output=True, timeout=timeout, env=_env())
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {timeout:.0f}s") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitError(err[-1] if err else f"git {args[0]} failed")
    return proc.stdout.decode("utf-8", "replace")
