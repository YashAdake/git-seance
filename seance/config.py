"""Settings, read from the environment and an optional .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://models.jarvislabs.net/v1"
DEFAULT_MODEL = "gemma-4-26b-a4b-it"

# Repositories offered on the landing page. Small enough to clone in seconds,
# old enough to have a proper graveyard.
DEFAULT_DEMO_REPOS = "psf/requests,pallets/flask,expressjs/express,jquery/jquery"


def load_dotenv(path: Path) -> None:
    """Tiny .env reader: KEY=VALUE lines; real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def default_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") \
        or str(Path.home() / ".cache")
    return Path(base) / "git-seance"


@dataclass
class Settings:
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    cache_dir: Path
    demo_repos: list[str]
    allow_remote: bool
    max_repo_mb: int
    llm_calls_per_ip_hour: int
    llm_calls_per_day: int
    trust_proxy: bool

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(Path.cwd() / ".env")
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
        env = os.environ.get
        return cls(
            llm_base_url=env("SEANCE_LLM_BASE_URL", DEFAULT_BASE_URL),
            llm_api_key=env("SEANCE_LLM_API_KEY", ""),
            llm_model=env("SEANCE_LLM_MODEL", DEFAULT_MODEL),
            cache_dir=Path(env("SEANCE_CACHE_DIR", "") or default_cache_dir()),
            demo_repos=[r.strip() for r in env("SEANCE_DEMO_REPOS", DEFAULT_DEMO_REPOS).split(",") if r.strip()],
            allow_remote=env("SEANCE_ALLOW_REMOTE", "1") == "1",
            max_repo_mb=int(env("SEANCE_MAX_REPO_MB", "300")),
            llm_calls_per_ip_hour=int(env("SEANCE_LLM_CALLS_PER_IP_HOUR", "60")),
            llm_calls_per_day=int(env("SEANCE_LLM_CALLS_PER_DAY", "3000")),
            trust_proxy=env("SEANCE_TRUST_PROXY", "0") == "1",
        )

    @property
    def llm_ready(self) -> bool:
        local = "localhost" in self.llm_base_url or "127.0.0.1" in self.llm_base_url
        return bool(self.llm_base_url and self.llm_model and (self.llm_api_key or local))
