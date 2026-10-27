"""Build throwaway git repositories with a scripted history."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


class TempRepo:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="seance-test-")
        self.path = self._tmp.name
        self._clock = 1_500_000_000
        self.git("init", "-q", "-b", "main")

    def cleanup(self):
        self._tmp.cleanup()

    def git(self, *args: str) -> str:
        env = dict(os.environ, GIT_AUTHOR_NAME="Ada", GIT_AUTHOR_EMAIL="ada@example.com",
                   GIT_COMMITTER_NAME="Ada", GIT_COMMITTER_EMAIL="ada@example.com",
                   GIT_AUTHOR_DATE=f"{self._clock} +0000", GIT_COMMITTER_DATE=f"{self._clock} +0000")
        out = subprocess.run(["git", "-C", self.path, "-c", "commit.gpgsign=false", *args],
                             capture_output=True, env=env, check=True)
        return out.stdout.decode("utf-8", "replace")

    def write(self, rel: str, text: str) -> None:
        p = Path(self.path, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def commit(self, message: str, *, author: str = "Ada", days: int = 30) -> str:
        self._clock += days * 86400
        self.git("add", "-A")
        self.git("-c", f"user.name={author}", "commit", "-q", "--allow-empty", "-m", message,
                 f"--author={author} <{author.lower()}@example.com>")
        return self.git("rev-parse", "HEAD").strip()
