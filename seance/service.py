"""The seance itself: graveyards, dossiers, epitaphs and summonings, with caching
and the limits that keep a public demo from burning through model credits."""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict, defaultdict, deque
from dataclasses import asdict
from pathlib import Path

from .config import Settings
from .dossier import Dossier, build_dossier
from .gitio import GitError
from .history import Ghost, find_ghosts, head_sha
from .llm import LLM, LLMError
from .medium import Epitaph, fallback_epitaph, write_epitaph
from .repos import Repo, RepoRegistry


class NotFound(LookupError):
    pass


class RateLimited(RuntimeError):
    pass


class Limiter:
    """Sliding-window cap per client per hour, plus a global daily cap."""

    def __init__(self, per_ip_hour: int, per_day: int):
        self.per_ip_hour, self.per_day = per_ip_hour, per_day
        self._ip: dict[str, deque] = defaultdict(deque)
        self._day: deque = deque()
        self._lock = threading.Lock()

    def take(self, ip: str) -> None:
        now = time.time()
        with self._lock:
            q = self._ip[ip]
            while q and now - q[0] > 3600:
                q.popleft()
            while self._day and now - self._day[0] > 86400:
                self._day.popleft()
            if len(q) >= self.per_ip_hour:
                raise RateLimited("the spirits are weary of you; try again within the hour")
            if len(self._day) >= self.per_day:
                raise RateLimited("the veil is closed for today; the demo's daily budget is spent")
            q.append(now)
            self._day.append(now)


class Seance:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        self.registry = RepoRegistry(settings.cache_dir, max_repo_mb=settings.max_repo_mb)
        self.llm = LLM(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
        self.limiter = Limiter(settings.llm_calls_per_ip_hour, settings.llm_calls_per_day)
        self._graveyards: dict[str, dict] = {}
        self._dossiers: OrderedDict[tuple[str, str], Dossier] = OrderedDict()
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._epitaph_lock = threading.Lock()

    @property
    def llm_ready(self) -> bool:
        return self.settings.llm_ready

    # --- repositories ---------------------------------------------------

    def repo(self, repo_id: str) -> Repo:
        repo = self.registry.get(repo_id)
        if repo is None and repo_id.startswith("gh-") and self.settings.allow_remote:
            # A shared link to a GitHub graveyard: reopen a clone, or a demo repo.
            repo = self.registry.reopen(repo_id)
            for spec in self.settings.demo_repos:
                if repo is None and f"gh-{spec.replace('/', '-')}".lower() == repo_id:
                    repo = self.registry.open_github(spec)
        if repo is None:
            raise NotFound("unknown graveyard; open it again from the start page")
        return repo

    def open_repo(self, text: str) -> Repo:
        if not self.settings.allow_remote:
            raise GitError("this seance only visits its own repository")
        return self.registry.open_github(text)

    # --- graveyard ------------------------------------------------------

    def graveyard(self, repo_id: str) -> dict:
        repo = self.repo(repo_id)
        with self._locks[repo_id]:
            head = head_sha(repo.path)
            cached = self._graveyards.get(repo_id)
            if cached and cached["head"] == head:
                return cached
            disk = self.settings.cache_dir / "graveyards" / f"{repo_id}-{head[:12]}.json"
            if disk.is_file():
                data = json.loads(disk.read_text(encoding="utf-8"))
                ghosts = [Ghost(**g) for g in data["ghosts"]]
                stats = data["stats"]
            else:
                t0 = time.monotonic()
                ghosts, stats = find_ghosts(repo.path)
                stats["scan_ms"] = int((time.monotonic() - t0) * 1000)
                disk.parent.mkdir(parents=True, exist_ok=True)
                disk.write_text(json.dumps({"ghosts": [asdict(g) for g in ghosts], "stats": stats}),
                                encoding="utf-8")
            entry = {"head": head, "repo": repo, "ghosts": ghosts,
                     "by_id": {g.id: g for g in ghosts}, "stats": stats}
            self._graveyards[repo_id] = entry
            return entry

    def graveyard_payload(self, repo_id: str) -> dict:
        gy = self.graveyard(repo_id)
        repo: Repo = gy["repo"]
        epitaphs = self._load_epitaphs(repo_id)
        ghosts = []
        for g in gy["ghosts"]:
            d = g.to_dict()
            if g.id in epitaphs:
                d["epitaph"] = epitaphs[g.id]["epitaph"]
            ghosts.append(d)
        return {"repo": {"id": repo.id, "label": repo.label, "web_url": repo.web_url, "head": gy["head"]},
                "stats": gy["stats"], "ghosts": ghosts}

    # --- one ghost ------------------------------------------------------

    def dossier(self, repo_id: str, ghost_id: str) -> Dossier:
        key = (repo_id, ghost_id)
        if key in self._dossiers:
            self._dossiers.move_to_end(key)
            return self._dossiers[key]
        gy = self.graveyard(repo_id)
        ghost = gy["by_id"].get(ghost_id)
        if ghost is None:
            raise NotFound("no ghost by that name rests here")
        d = build_dossier(gy["repo"].path, ghost, gy["repo"].label)
        self._dossiers[key] = d
        while len(self._dossiers) > 128:
            self._dossiers.popitem(last=False)
        return d

    def ghost_payload(self, repo_id: str, ghost_id: str) -> dict:
        d = self.dossier(repo_id, ghost_id)
        cached = self._load_epitaphs(repo_id).get(ghost_id)
        out = d.to_dict()
        out["epitaph"] = cached or fallback_epitaph(d).to_dict()
        out["epitaph_pending"] = cached is None and self.llm_ready
        return out

    # --- epitaphs (cached on disk: a ghost is only mourned once) --------

    def _epitaph_file(self, repo_id: str) -> Path:
        return self.settings.cache_dir / "epitaphs" / f"{repo_id}-{self._model_key()}.json"

    def _model_key(self) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in self.settings.llm_model)[:40]

    def _load_epitaphs(self, repo_id: str) -> dict:
        f = self._epitaph_file(repo_id)
        if not f.is_file():
            return {}
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def epitaph(self, repo_id: str, ghost_id: str, client: str) -> dict:
        cached = self._load_epitaphs(repo_id).get(ghost_id)
        if cached:
            return cached
        d = self.dossier(repo_id, ghost_id)
        if not self.llm_ready:
            return fallback_epitaph(d).to_dict()
        self.limiter.take(client)
        try:
            ep: Epitaph = write_epitaph(self.llm, d)
        except LLMError:
            return fallback_epitaph(d).to_dict()
        with self._epitaph_lock:
            data = self._load_epitaphs(repo_id)
            data[ghost_id] = ep.to_dict()
            f = self._epitaph_file(repo_id)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(data), encoding="utf-8")
        return ep.to_dict()
