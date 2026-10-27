"""Find a repository's ghosts.

A ghost is a file whose last life ended in a deletion and that is not present
at HEAD. We find them all with a single `git log` pass over add / rename /
delete events, replayed oldest-first, so renames carry a file's identity
forward instead of being mistaken for a death and a birth.
"""

from __future__ import annotations

import hashlib
import posixpath
import time
from collections import Counter
from dataclasses import asdict, dataclass, field

from .gitio import run_git

REC, FLD, END = "\x1e", "\x1f", "\x1d"
# One record per commit: header fields, END, then --name-status lines.
LOG_FORMAT = "%x1e%H%x1f%P%x1f%an%x1f%ae%x1f%at%x1f%s%x1d"
LOG_FORMAT_BODY = "%x1e%H%x1f%P%x1f%an%x1f%ae%x1f%at%x1f%s%x1f%b%x1d"

_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def unquote_path(raw: str) -> str:
    """Undo git's C-style quoting of unusual paths ("caf\\303\\251.py" -> "café.py")."""
    if len(raw) < 2 or raw[0] != '"' or raw[-1] != '"':
        return raw
    s, out, i = raw[1:-1], bytearray(), 0
    while i < len(s):
        ch = s[i]
        if ch != "\\":
            out += ch.encode("utf-8")
            i += 1
        elif s[i + 1 : i + 4].isdigit() and len(s[i + 1 : i + 4]) == 3:
            out.append(int(s[i + 1 : i + 4], 8))
            i += 4
        else:
            out.append(_ESCAPES.get(s[i + 1 : i + 2], ord(s[i + 1 : i + 2] or "\\")))
            i += 2
    return out.decode("utf-8", "replace")


@dataclass
class Change:
    status: str          # A, M, D, R, C, T ...
    path: str            # path after the change
    old_path: str | None = None  # source path for R / C


@dataclass
class Commit:
    sha: str
    parents: list[str]
    author: str
    email: str
    at: int
    subject: str
    body: str = ""
    changes: list[Change] = field(default_factory=list)

    @property
    def short(self) -> str:
        return self.sha[:7]


def parse_status_line(line: str) -> Change | None:
    parts = line.split("\t")
    if len(parts) < 2 or not parts[0]:
        return None
    letter = parts[0][0]
    if letter in "RC" and len(parts) >= 3:
        return Change(letter, unquote_path(parts[2]), unquote_path(parts[1]))
    return Change(letter, unquote_path(parts[1]))


def parse_log(text: str) -> list[Commit]:
    """Parse output produced with LOG_FORMAT / LOG_FORMAT_BODY and --name-status."""
    commits = []
    for rec in text.split(REC):
        if not rec.strip():
            continue
        head, _, tail = rec.partition(END)
        f = head.split(FLD)
        if len(f) < 6:
            continue
        commits.append(Commit(
            sha=f[0].strip(),
            parents=f[1].split(),
            author=f[2],
            email=f[3],
            at=int(f[4] or 0),
            subject=f[5],
            body=f[6].strip() if len(f) > 6 else "",
            changes=[c for c in map(parse_status_line, tail.splitlines()) if c],
        ))
    return commits


# --- classification -------------------------------------------------------

_ASSET_EXT = {
    "png", "jpg", "jpeg", "gif", "webp", "ico", "bmp", "tiff", "svg", "psd",
    "woff", "woff2", "ttf", "otf", "eot", "mp3", "mp4", "wav", "ogg", "webm", "mov",
    "pdf", "zip", "gz", "tgz", "jar", "exe", "dll", "so", "dylib", "bin", "pyc", "class",
}
_DOC_EXT = {"md", "rst", "txt", "adoc", "markdown", "rdoc", "textile"}
_CONFIG_EXT = {"json", "yml", "yaml", "toml", "ini", "cfg", "conf", "lock", "xml", "plist", "properties", "env"}
_CONFIG_NAMES = {"makefile", "dockerfile", "procfile", "gemfile", "rakefile", "pipfile", "vagrantfile",
                 "license", "authors", "contributors", "manifest.in", "tox.ini", "requirements.txt"}
_VENDOR_PARTS = {"vendor", "vendors", "node_modules", "third_party", "thirdparty", "external", "dist", "bower_components"}
_TEST_PARTS = {"test", "tests", "spec", "specs", "__tests__", "testing", "testdata", "fixtures"}


def classify(path: str) -> str:
    """Rough kind used for filtering and tombstone styling."""
    lower = path.lower()
    parts = lower.split("/")
    name = parts[-1]
    ext = name.rsplit(".", 1)[-1] if "." in name.lstrip(".") else ""
    if any(p in _VENDOR_PARTS for p in parts[:-1]) or ".min." in name:
        return "vendor"
    if ext in _ASSET_EXT:
        return "asset"
    if any(p in _TEST_PARTS for p in parts[:-1]) or name.startswith("test_") or "_test." in name \
            or ".test." in name or ".spec." in name:
        return "test"
    if ext in _DOC_EXT or "docs" in parts[:-1] or "doc" in parts[:-1]:
        return "docs"
    if ext in _CONFIG_EXT or name in _CONFIG_NAMES or any(p.startswith(".") for p in parts):
        return "config"
    return "code"


# --- replay ---------------------------------------------------------------

@dataclass
class _Life:
    born_sha: str | None
    born_at: int | None
    born_as: str | None
    former_names: list[str] = field(default_factory=list)


@dataclass
class Ghost:
    id: str
    path: str
    name: str
    dir: str
    kind: str
    born_sha: str | None
    born_at: int | None
    born_as: str | None
    former_names: list[str]
    died_sha: str
    died_at: int
    killer: str
    death_subject: str
    lives: int        # how many times this path has died
    siblings: int     # other files deleted in the same commit
    reborn_at: int | None = None  # start of the final life, when it died more than once

    def to_dict(self) -> dict:
        return asdict(self)


def ghost_id(path: str, died_sha: str) -> str:
    return hashlib.sha1(f"{path}\0{died_sha}".encode()).hexdigest()[:12]


def replay(commits_oldest_first: list[Commit], head_paths: set[str]) -> list[Ghost]:
    """Walk add/rename/delete events forward in time and collect final deaths."""
    alive: dict[str, _Life] = {}
    last_death: dict[str, tuple[_Life, Commit]] = {}
    first_life: dict[str, _Life] = {}   # earliest life that ended at each path
    deaths_per_path: Counter[str] = Counter()
    deletions_per_commit: Counter[str] = Counter()

    for c in commits_oldest_first:
        for ch in c.changes:
            if ch.status == "A" or ch.status == "C":
                alive.setdefault(ch.path, _Life(c.sha, c.at, ch.path))
            elif ch.status == "R" and ch.old_path:
                life = alive.pop(ch.old_path, None) or _Life(None, None, None)
                life.former_names.append(ch.old_path)
                alive[ch.path] = life
            elif ch.status == "D":
                life = alive.pop(ch.path, None) or _Life(None, None, None)
                last_death[ch.path] = (life, c)
                first_life.setdefault(ch.path, life)
                deaths_per_path[ch.path] += 1
                deletions_per_commit[c.sha] += 1

    ghosts = []
    for path, (life, c) in last_death.items():
        if path in alive or path in head_paths:
            continue  # it came back: a reincarnation, not a ghost
        first = first_life[path] if first_life[path].born_at else life
        ghosts.append(Ghost(
            id=ghost_id(path, c.sha),
            path=path,
            name=posixpath.basename(path),
            dir=posixpath.dirname(path),
            kind=classify(path),
            born_sha=first.born_sha,
            born_at=first.born_at,
            born_as=first.born_as if first.born_as != path else None,
            former_names=_dedupe(first.former_names + life.former_names),
            died_sha=c.sha,
            died_at=c.at,
            killer=c.author,
            death_subject=c.subject,
            lives=deaths_per_path[path],
            siblings=deletions_per_commit[c.sha] - 1,
            reborn_at=life.born_at if first is not life else None,
        ))
    ghosts.sort(key=lambda g: g.died_at, reverse=True)
    return ghosts


def _dedupe(names: list[str]) -> list[str]:
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


# --- stats ----------------------------------------------------------------

def compute_stats(ghosts: list[Ghost], living: int) -> dict:
    by_year: Counter[int] = Counter(time.gmtime(g.died_at).tm_year for g in ghosts)
    killers = Counter(g.killer for g in ghosts)
    mass: dict[str, dict] = {}
    for g in ghosts:
        m = mass.setdefault(g.died_sha, {"sha": g.died_sha, "subject": g.death_subject,
                                         "author": g.killer, "at": g.died_at, "count": 0})
        m["count"] += 1
    lived = [g for g in ghosts if g.born_at]
    eldest = max(lived, key=lambda g: g.died_at - g.born_at, default=None)
    reincarnated = max(ghosts, key=lambda g: g.lives, default=None)
    return {
        "souls": len(ghosts),
        "living": living,
        "deaths_by_year": dict(sorted(by_year.items())),
        "reapers": [{"name": n, "count": k} for n, k in killers.most_common(5)],
        "extinctions": sorted(mass.values(), key=lambda m: m["count"], reverse=True)[:3],
        "eldest": eldest.id if eldest else None,
        "eldest_days": (eldest.died_at - eldest.born_at) // 86400 if eldest else None,
        "most_reincarnated": reincarnated.id if reincarnated and reincarnated.lives > 1 else None,
        "kinds": dict(Counter(g.kind for g in ghosts)),
    }


# --- entry point ----------------------------------------------------------

def head_paths(repo: str) -> set[str]:
    out = run_git(repo, ["ls-tree", "-r", "--name-only", "-z", "HEAD"])
    return {p for p in out.split("\0") if p}


def head_sha(repo: str) -> str:
    return run_git(repo, ["rev-parse", "HEAD"]).strip()


def find_ghosts(repo: str, *, max_commits: int | None = None) -> tuple[list[Ghost], dict]:
    """Return (ghosts newest-death-first, stats) for the repository at HEAD."""
    args = ["log", "HEAD", "--reverse", "-M", "--diff-filter=ADRC", "--name-status",
            f"--format={LOG_FORMAT}"]
    if max_commits:
        args.insert(2, f"--max-count={max_commits}")
    commits = parse_log(run_git(repo, args, timeout=600))
    living = head_paths(repo)
    ghosts = replay(commits, living)
    return ghosts, compute_stats(ghosts, len(living))
