"""HTTP server: serves the graveyard UI and a small JSON + SSE API (stdlib only)."""

from __future__ import annotations

import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .gitio import GitError
from .llm import LLMError, Usage
from .medium import check_citations, summon, tidy
from .service import NotFound, RateLimited, Seance

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 64 * 1024

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
}


class BadRequest(ValueError):
    pass


def make_handler(seance: Seance):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"git-seance/{__version__}"
        protocol_version = "HTTP/1.1"

        # --- plumbing ---------------------------------------------------

        def log_message(self, fmt, *args):  # quieter than the default
            if not self.path.startswith(("/assets", "/healthz")):
                super().log_message(fmt, *args)

        def _client(self) -> str:
            if seance.settings.trust_proxy:
                fwd = self.headers.get("X-Forwarded-For", "")
                if fwd:
                    return fwd.split(",")[0].strip()
            return self.client_address[0]

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, obj) -> None:
            self._send(status, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8",
                       {"Cache-Control": "no-store"})

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise BadRequest("request too large")
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError as exc:
                raise BadRequest("invalid JSON") from exc
            if not isinstance(data, dict):
                raise BadRequest("expected a JSON object")
            return data

        def _guard(self, fn, *args):
            try:
                fn(*args)
            except BadRequest as exc:
                self._json(400, {"error": str(exc)})
            except NotFound as exc:
                self._json(404, {"error": str(exc)})
            except RateLimited as exc:
                self._json(429, {"error": str(exc)})
            except GitError as exc:
                self._json(422, {"error": str(exc)})
            except LLMError as exc:
                self._json(502, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:  # last resort: never leak a traceback to the page
                self.log_error("unhandled error: %r", exc)
                self._json(500, {"error": "something in the crypt broke; try again"})

        # --- routing ----------------------------------------------------

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            route = {
                "/api/config": self._config,
                "/api/graveyard": lambda: self._json(200, seance.graveyard_payload(_need(q, "repo"))),
                "/api/ghost": lambda: self._json(200, seance.ghost_payload(_need(q, "repo"), _need(q, "id"))),
                "/healthz": lambda: self._json(200, {"ok": True}),
            }.get(url.path)
            if route:
                return self._guard(route)
            self._static(url.path)

        def do_POST(self):
            route = {
                "/api/open": self._open,
                "/api/epitaph": self._epitaph,
                "/api/summon": self._summon,
            }.get(urlparse(self.path).path)
            if not route:
                return self._json(404, {"error": "not found"})
            self._guard(route)

        # --- endpoints --------------------------------------------------

        def _config(self):
            self._json(200, {
                "version": __version__,
                "model": seance.settings.llm_model,
                "llm_ready": seance.llm_ready,
                "allow_remote": seance.settings.allow_remote,
                "local_repos": [{"id": r.id, "label": r.label} for r in seance.registry.all()
                                if r.id.startswith("local-")],
                "demo_repos": seance.settings.demo_repos if seance.settings.allow_remote else [],
            })

        def _open(self):
            text = str(self._body().get("repo") or "").strip()
            if not text or len(text) > 300:
                raise BadRequest("give a GitHub repository like owner/name")
            repo = seance.open_repo(text)
            self._json(200, {"id": repo.id, "label": repo.label})

        def _epitaph(self):
            body = self._body()
            self._json(200, seance.epitaph(_need(body, "repo"), _need(body, "id"), self._client()))

        def _summon(self):
            body = self._body()
            repo_id, ghost_id = _need(body, "repo"), _need(body, "id")
            history = body.get("history") if isinstance(body.get("history"), list) else []
            question = str(body.get("question") or "")[:1000]
            dossier = seance.dossier(repo_id, ghost_id)
            if not seance.llm_ready:
                raise LLMError("no model is configured, so the spirits are silent")
            seance.limiter.take(self._client())

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.close_connection = True

            def emit(event: str, data: dict):
                self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8"))
                self.wfile.flush()

            usage, text = Usage(), []
            try:
                for delta in summon(seance.llm, dossier, history, question, usage):
                    text.append(delta)
                    emit("token", {"t": delta})
                reply = tidy("".join(text))
                emit("done", {"text": reply, "citations": check_citations(reply, dossier.known_shas),
                              "usage": usage.to_dict()})
            except LLMError as exc:
                emit("error", {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass  # the visitor left the room
            except Exception as exc:  # headers are already sent: report in-stream
                self.log_error("summon failed: %r", exc)
                emit("error", {"error": "the spirit was interrupted; try again"})

        # --- static files -----------------------------------------------

        def _static(self, path: str):
            if path in ("/", "/index.html"):
                target = WEB_DIR / "index.html"
            elif path.startswith("/assets/"):
                target = (WEB_DIR / path[len("/assets/"):]).resolve()
                if WEB_DIR not in target.parents:
                    return self._json(404, {"error": "not found"})
            else:
                return self._json(404, {"error": "not found"})
            if not target.is_file():
                return self._json(404, {"error": "not found"})
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    return Handler


def _need(src: dict, key: str) -> str:
    value = src.get(key)
    if not isinstance(value, str) or not value or len(value) > 200:
        raise BadRequest(f"missing '{key}'")
    return value


def serve(seance: Seance, host: str, port: int, *, warm: list[str] | None = None) -> None:
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    mimetypes.add_type("image/svg+xml", ".svg")
    if warm:
        def _warm():
            for spec in warm:
                try:
                    seance.graveyard(seance.open_repo(spec).id)
                    print(f"  ⚰  warmed {spec}")
                except Exception as exc:  # warming is best effort
                    print(f"  ⚰  could not warm {spec}: {exc}")
        threading.Thread(target=_warm, daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), make_handler(seance))
    httpd.daemon_threads = True
    print(f"  🕯  git-seance is listening on http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  the candles go out.")
