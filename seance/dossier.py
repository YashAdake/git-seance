"""Build a ghost's dossier: everything git remembers about one deleted file.

The dossier is the only thing the model ever sees. If a fact is not in here,
the ghost does not know it.
"""

from __future__ import annotations

import difflib
import posixpath
import re
from collections import Counter
from dataclasses import asdict, dataclass, field

from .gitio import GitError, run_git
from .history import LOG_FORMAT_BODY, Ghost, parse_log
from .redact import redact

MAX_LIFE_COMMITS = 400
MAX_BLOB_BYTES = 2_000_000
MAX_BODY_CHARS = 60_000


@dataclass
class LifeEvent:
    sha: str
    short: str
    author: str
    at: int
    subject: str
    body: str
    status: str            # A born, M touched, R renamed, D died
    path: str              # the file's name at that moment
    old_path: str | None = None


@dataclass
class Dossier:
    ghost: Ghost
    repo_label: str
    events: list[LifeEvent]                    # oldest first, ends with the death
    contributors: list[dict]
    last_body: str | None
    binary: bool
    size_bytes: int
    line_count: int
    truncated: bool
    language: str
    buried_with: list[str]
    buried_with_total: int
    successors: list[dict]
    secrets: dict[str, int]
    commit_url: str | None                     # template with {sha}
    resurrect_cmd: str
    known_shas: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ghost"] = self.ghost.to_dict()
        return d


_LANG_BY_EXT = {
    "py": "python", "js": "javascript", "mjs": "javascript", "cjs": "javascript", "jsx": "javascript",
    "ts": "typescript", "tsx": "typescript", "rb": "ruby", "go": "go", "rs": "rust", "java": "java",
    "kt": "kotlin", "c": "c", "h": "c", "cc": "cpp", "cpp": "cpp", "hpp": "cpp", "cs": "csharp",
    "php": "php", "sh": "bash", "bash": "bash", "zsh": "bash", "ps1": "powershell", "md": "markdown",
    "json": "json", "yml": "yaml", "yaml": "yaml", "toml": "toml", "html": "html", "css": "css",
    "scss": "scss", "sql": "sql", "swift": "swift", "lua": "lua", "pl": "perl", "r": "r",
}


def language_for(path: str) -> str:
    name = posixpath.basename(path).lower()
    return _LANG_BY_EXT.get(name.rsplit(".", 1)[-1], "text") if "." in name else "text"


def commit_url_template(remote: str | None) -> str | None:
    """Turn a git remote into a commit link template, for GitHub and GitLab."""
    if not remote:
        return None
    remote = remote.strip()
    m = re.match(r"^(?:git@|ssh://git@)([^:/]+)[:/](.+?)(?:\.git)?/?$", remote) \
        or re.match(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$", remote)
    if not m:
        return None
    host, slug = m.group(1).lower(), m.group(2)
    if host == "github.com":
        return f"https://github.com/{slug}/commit/{{sha}}"
    if host == "gitlab.com":
        return f"https://gitlab.com/{slug}/-/commit/{{sha}}"
    return None


def _remote(repo: str) -> str | None:
    try:
        return run_git(repo, ["remote", "get-url", "origin"]).strip() or None
    except GitError:
        return None


def _rank_successors(ghost: Ghost, added: list[str]) -> list[dict]:
    """Files born in the commit that killed the ghost, most name-alike first."""
    stem = ghost.name.rsplit(".", 1)[0].lower()
    ext = ghost.name.rsplit(".", 1)[-1].lower() if "." in ghost.name else ""
    ranked = []
    for p in added:
        name = posixpath.basename(p).lower()
        score = difflib.SequenceMatcher(None, stem, name.rsplit(".", 1)[0]).ratio()
        if ext and name.endswith("." + ext):
            score += 0.15
        if posixpath.dirname(p) == ghost.dir:
            score += 0.1
        ranked.append({"path": p, "score": round(min(score, 1.0), 2)})
    ranked.sort(key=lambda r: r["score"], reverse=True)
    return [r for r in ranked if r["score"] >= 0.45][:3]


def build_dossier(repo: str, ghost: Ghost, repo_label: str) -> Dossier:
    secrets: Counter = Counter()

    def clean(text: str) -> str:
        out, found = redact(text)
        secrets.update(found)
        return out

    # The life story, following renames back to birth.
    life_raw = run_git(repo, [
        "log", "--follow", "-M", "--name-status", f"--max-count={MAX_LIFE_COMMITS}",
        f"--format={LOG_FORMAT_BODY}", f"{ghost.died_sha}^", "--", ghost.path,
    ])
    events: list[LifeEvent] = []
    for c in reversed(parse_log(life_raw)):
        ch = c.changes[0] if c.changes else None
        events.append(LifeEvent(
            sha=c.sha, short=c.short, author=c.author, at=c.at,
            subject=clean(c.subject), body=clean(c.body)[:1500],
            status=(ch.status if ch else "M"), path=(ch.path if ch else ghost.path),
            old_path=(ch.old_path if ch else None),
        ))

    # The death, and who was buried alongside.
    death = parse_log(run_git(repo, [
        "show", "-M", "--name-status", f"--format={LOG_FORMAT_BODY}", ghost.died_sha,
    ]))[0]
    deleted = [ch.path for ch in death.changes if ch.status == "D" and ch.path != ghost.path]
    added = [ch.path for ch in death.changes if ch.status in "AR" and ch.path != ghost.path]
    events.append(LifeEvent(
        sha=death.sha, short=death.short, author=death.author, at=death.at,
        subject=clean(death.subject), body=clean(death.body)[:3000],
        status="D", path=ghost.path,
    ))

    # The last body it had before it died.
    last_body, binary, size, truncated = None, False, 0, False
    spec = f"{ghost.died_sha}^:{ghost.path}"
    try:
        size = int(run_git(repo, ["cat-file", "-s", spec]).strip())
        if size <= MAX_BLOB_BYTES:
            raw = run_git(repo, ["cat-file", "blob", spec], binary=True)
            binary = b"\0" in raw[:8000]
            if not binary:
                text = raw.decode("utf-8", "replace")
                truncated = len(text) > MAX_BODY_CHARS
                last_body = clean(text[:MAX_BODY_CHARS])
        else:
            truncated = True
    except GitError:
        pass

    authors = Counter(e.author for e in events if e.status != "D")
    remote = _remote(repo)
    return Dossier(
        ghost=ghost,
        repo_label=repo_label,
        events=events,
        contributors=[{"name": n, "commits": k} for n, k in authors.most_common(8)],
        last_body=last_body,
        binary=binary,
        size_bytes=size,
        line_count=last_body.count("\n") + 1 if last_body else 0,
        truncated=truncated,
        language=language_for(ghost.path),
        buried_with=deleted[:40],
        buried_with_total=len(deleted),
        successors=_rank_successors(ghost, added),
        secrets=dict(secrets),
        commit_url=commit_url_template(remote),
        resurrect_cmd=f'git checkout {ghost.died_sha[:12]}^ -- "{ghost.path}"',
        known_shas=[e.sha for e in events],
    )
