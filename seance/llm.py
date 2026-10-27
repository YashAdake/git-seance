"""Minimal OpenAI-compatible chat client (stdlib only).

Works with any server that speaks /v1/chat/completions: Jarvislabs Token
Factory serving Gemma, Ollama, llama.cpp's server, vLLM, LM Studio. Swapping
the model is a config change, which is the point of building on open weights.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Iterator

USER_AGENT = "git-seance/0.1 (+https://github.com/YashAdake/git-seance)"


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    model: str = ""
    latency_ms: int = 0
    first_token_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Completion:
    text: str
    usage: Usage = field(default_factory=Usage)


class LLM:
    def __init__(self, base_url: str, api_key: str, model: str, *, timeout: float = 90):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.model)

    def _request(self, payload: dict) -> urllib.request.Request:
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(), headers=headers, method="POST",
        )

    def _open(self, payload: dict):
        """POST with up to two retries on rate limits, 5xx and network errors."""
        delay = 1.5
        for attempt in range(3):
            try:
                return urllib.request.urlopen(self._request(payload), timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:300]
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise LLMError(f"model endpoint returned {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt < 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise LLMError(f"could not reach the model endpoint: {exc}") from exc
        raise LLMError("unreachable")

    def chat(self, messages: list[dict], *, max_tokens: int = 600, temperature: float = 0.7,
             json_mode: bool = False) -> Completion:
        payload = {"model": self.model, "messages": messages,
                   "max_tokens": max_tokens, "temperature": temperature}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        t0 = time.monotonic()
        with self._open(payload) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {str(data)[:200]}") from exc
        u = data.get("usage") or {}
        return Completion(text.strip(), Usage(
            model=data.get("model") or self.model,
            latency_ms=int((time.monotonic() - t0) * 1000),
            prompt_tokens=u.get("prompt_tokens"), completion_tokens=u.get("completion_tokens"),
        ))

    def stream(self, messages: list[dict], *, max_tokens: int = 500,
               temperature: float = 0.8, usage: Usage | None = None) -> Iterator[str]:
        """Yield text deltas. Fills `usage` in place when the stream ends."""
        usage = usage if usage is not None else Usage()
        usage.model = self.model
        payload = {"model": self.model, "messages": messages, "max_tokens": max_tokens,
                   "temperature": temperature, "stream": True,
                   "stream_options": {"include_usage": True}}
        t0 = time.monotonic()
        with self._open(payload) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage.prompt_tokens = chunk["usage"].get("prompt_tokens")
                    usage.completion_tokens = chunk["usage"].get("completion_tokens")
                for choice in chunk.get("choices") or []:
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        if usage.first_token_ms is None:
                            usage.first_token_ms = int((time.monotonic() - t0) * 1000)
                        yield delta
        usage.latency_ms = int((time.monotonic() - t0) * 1000)


def parse_json_reply(text: str) -> dict:
    """Parse a JSON object from a model reply, tolerating code fences and chatter."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("model did not return JSON")
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"model returned invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise LLMError("model returned JSON that is not an object")
    return obj
