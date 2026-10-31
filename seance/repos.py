"""Which graveyards we can visit: local repositories and public GitHub ones."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .gitio import GitError, run_git, run_git_plain
from .llm import USER_AGENT, tls_context

_GITHUB = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+?)(?:\.git)?/?(?:[#?].*)?$")
_SHORTHAND = re.compile(r"^([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)$")


@dataclass
class Repo:
    id: str          # stable key used in URLs: "local-<hash>" or "gh-owner-name"
    label: str       # what people see: folder name or owner/name
    path: str        # on-disk git directory
    web_url: str | None = None


def parse_github(text: str) -> tuple[str, str] | None:
    text = text.strip()
    m = _GITHUB.match(text) or _SHORTHAND.match(text)
    if not m or m.group(2) in (".", ".."):
        return None
    return m.group(1), m.group(2)


class RepoRegistry:
    def __init__(self, cache_dir: Path, *, max_repo_mb: int = 300):
        self.clone_dir = cache_dir / "clones"
        self.max_repo_mb = max_repo_mb
        self._repos: dict[str, Repo] = {}
        self._lock = threading.Lock()
        self._clone_locks: dict[str, threading.Lock] = {}

    def get(self, repo_id: str) -> Repo | None:
        return self._repos.get(repo_id)

    def all(self) -> list[Repo]:
        return list(self._repos.values())

    def add_local(self, path: str) -> Repo:
        try:
            if run_git(path, ["rev-parse", "--is-bare-repository"]).strip() == "true":
                top = run_git(path, ["rev-parse", "--absolute-git-dir"]).strip()
            else:
                top = run_git(path, ["rev-parse", "--show-toplevel"]).strip()
        except GitError as exc:
            raise GitError(f"{path} is not a git repository") from exc
        repo_id = "local-" + hashlib.sha1(top.encode()).hexdigest()[:8]
        label = Path(top).name
        repo = Repo(repo_id, label[:-4] if label.endswith(".git") else label, top)
        self._repos[repo_id] = repo
        return repo

    def open_github(self, text: str) -> Repo:
        parsed = parse_github(text)
        if not parsed:
            raise GitError("that does not look like a GitHub repository (owner/name or a github.com URL)")
        owner, name = parsed
        repo_id = f"gh-{owner}-{name}".lower()
        with self._lock:
            if repo_id in self._repos:
                return self._repos[repo_id]
            lock = self._clone_locks.setdefault(repo_id, threading.Lock())
        with lock:  # one clone per repo at a time; later callers reuse it
            if repo_id in self._repos:
                return self._repos[repo_id]
            dest = self.clone_dir / f"{owner}__{name}.git".lower()
            if not (dest / "HEAD").exists():
                self._check_size(owner, name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                run_git_plain(["clone", "--bare", "--single-branch", "--no-tags", "--quiet",
                               f"https://github.com/{owner}/{name}.git", str(dest)], timeout=600)
            repo = Repo(repo_id, f"{owner}/{name}", str(dest), f"https://github.com/{owner}/{name}")
            self._repos[repo_id] = repo
            return repo

    def reopen(self, repo_id: str) -> Repo | None:
        """Find an already-cloned GitHub repo by id (for shared links after a restart)."""
        if not self.clone_dir.is_dir():
            return None
        for entry in self.clone_dir.glob("*__*.git"):
            owner, _, name = entry.name[:-4].partition("__")
            if f"gh-{owner}-{name}" == repo_id and (entry / "HEAD").exists():
                return self.open_github(f"{owner}/{name}")
        return None

    def _check_size(self, owner: str, name: str) -> None:
        """Refuse giant repos before cloning. If the API is unreachable, let the clone timeout guard us."""
        req = urllib.request.Request(f"https://api.github.com/repos/{owner}/{name}",
                                     headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(req, timeout=10, context=tls_context()) as resp:
                info = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise GitError(f"github.com/{owner}/{name} was not found (or is private)") from exc
            return
        except (urllib.error.URLError, TimeoutError, ValueError):
            return
        size_mb = (info.get("size") or 0) / 1024
        if size_mb > self.max_repo_mb:
            raise GitError(f"{owner}/{name} is {size_mb:,.0f} MB; this graveyard only opens repos under "
                           f"{self.max_repo_mb} MB. Run git-seance locally for bigger ones.")
