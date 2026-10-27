"""Command line: `python -m seance serve|graveyard|summon`."""

from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser

from . import __version__
from .config import Settings
from .gitio import GitError
from .history import find_ghosts
from .llm import LLMError, Usage
from .medium import check_citations, fallback_epitaph, summon
from .server import serve
from .service import NotFound, Seance

# ANSI colours, quietly disabled when not writing to a terminal.
_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
MARIGOLD, BONE, MIST, BLOOD, VIOLET, RESET = (
    ("\033[38;5;214m", "\033[38;5;230m", "\033[38;5;246m", "\033[38;5;167m", "\033[38;5;141m", "\033[0m")
    if _TTY else ("",) * 6
)


def _days(ghost) -> str:
    if not ghost.born_at:
        return "?"
    return f"{(ghost.died_at - ghost.born_at) // 86400:,}d"


def cmd_graveyard(args) -> int:
    ghosts, stats = find_ghosts(args.repo)
    kinds = set(args.kind.split(",")) if args.kind else None
    rows = [g for g in ghosts if not kinds or g.kind in kinds][: args.limit]
    print(f"\n{MARIGOLD}  ✝  {stats['souls']:,} souls rest here{RESET}  "
          f"{MIST}({stats['living']:,} files still living){RESET}\n")
    for g in rows:
        died = time.strftime("%Y-%m-%d", time.gmtime(g.died_at))
        print(f"  {BONE}{g.path:<60.60}{RESET} {MIST}{g.kind:<7} lived {_days(g):>7}  died {died}"
              f"  by {g.killer[:18]:<18}{RESET} {VIOLET}{g.id}{RESET}")
    if stats["extinctions"]:
        e = stats["extinctions"][0]
        print(f"\n  {BLOOD}Deadliest commit:{RESET} {e['sha'][:7]} took {e['count']} souls: {e['subject'][:70]}")
    print(f"\n  {MIST}Summon one with:{RESET} python -m seance summon <id-or-path> {args.repo}\n")
    return 0


def cmd_summon(args) -> int:
    seance = Seance(Settings.from_env())
    repo = seance.registry.add_local(args.repo)
    gy = seance.graveyard(repo.id)
    ghost = gy["by_id"].get(args.ghost) or next(
        (g for g in gy["ghosts"] if g.path == args.ghost or g.path.endswith("/" + args.ghost)), None)
    if ghost is None:
        print(f"{BLOOD}No ghost named {args.ghost!r} rests in {repo.label}.{RESET}")
        return 1
    d = seance.dossier(repo.id, ghost.id)
    ep = fallback_epitaph(d)
    print(f"\n{MIST}  You light a candle over {RESET}{BONE}{ghost.path}{RESET}")
    print(f"{MIST}  {ep.epitaph}  {ep.cause_of_death}{RESET}\n")
    if not seance.llm_ready:
        print(f"{BLOOD}  The spirits are silent: set SEANCE_LLM_API_KEY (see .env.example).{RESET}\n")
        return 1
    history: list[dict] = []
    question = ""
    while True:
        usage = Usage()
        print(f"{VIOLET}  👻 {RESET}", end="", flush=True)
        reply = []
        try:
            for delta in summon(seance.llm, d, history, question, usage):
                reply.append(delta)
                print(f"{BONE}{delta}{RESET}", end="", flush=True)
        except LLMError as exc:
            print(f"\n{BLOOD}  the connection fades: {exc}{RESET}")
            return 1
        text = "".join(reply)
        cites = check_citations(text, d.known_shas)
        badge = f"{len(cites['verified'])} verified citations"
        if cites["invented"]:
            badge += f", {BLOOD}{len(cites['invented'])} unverified{MIST}"
        print(f"\n{MIST}     [{usage.model} · {usage.latency_ms} ms · {badge}]{RESET}\n")
        history += [{"role": "user", "content": question or "Introduce yourself."},
                    {"role": "assistant", "content": text}]
        try:
            question = input(f"{MARIGOLD}  you › {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            question = ""
        if question.lower() in ("", "bye", "exit", "quit", "goodbye"):
            print(f"{MIST}  The candle gutters. The ghost returns to the dark.{RESET}\n")
            return 0


def cmd_serve(args) -> int:
    settings = Settings.from_env()
    if args.local_only:
        settings.allow_remote = False
    seance = Seance(settings)
    for path in args.repos:
        repo = seance.registry.add_local(path)
        print(f"  ⚰  opened {repo.label}  ({repo.path})")
    if not seance.registry.all() and not settings.allow_remote:
        print("Nothing to haunt: pass a repository path.")
        return 1
    model = f"{settings.llm_model} via {settings.llm_base_url}" if seance.llm_ready else "none (records-only mode)"
    print(f"  🕯  model: {model}")
    if args.open:
        webbrowser.open(f"http://localhost:{args.port}/")
    serve(seance, args.host, args.port, warm=settings.demo_repos if args.warm else None)
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if os.name == "nt":
        os.system("")  # turn on ANSI colours in older Windows consoles
    p = argparse.ArgumentParser(prog="git-seance", description="Every deleted file is a ghost. Summon one.")
    p.add_argument("--version", action="version", version=f"git-seance {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="open the graveyard in your browser")
    s.add_argument("repos", nargs="*", help="local repositories to haunt (default: none, GitHub only)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8666")))
    s.add_argument("--open", action="store_true", help="open a browser tab")
    s.add_argument("--warm", action="store_true", help="pre-clone the demo repositories")
    s.add_argument("--local-only", action="store_true", help="do not allow opening GitHub repositories")
    s.set_defaults(fn=cmd_serve)

    g = sub.add_parser("graveyard", help="list the ghosts of a repository")
    g.add_argument("repo", nargs="?", default=".")
    g.add_argument("--limit", type=int, default=40)
    g.add_argument("--kind", help="comma list: code,test,docs,config,asset,vendor")
    g.set_defaults(fn=cmd_graveyard)

    m = sub.add_parser("summon", help="hold a seance with one ghost in the terminal")
    m.add_argument("ghost", help="ghost id or path")
    m.add_argument("repo", nargs="?", default=".")
    m.set_defaults(fn=cmd_summon)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (GitError, NotFound) as exc:
        print(f"{BLOOD}error:{RESET} {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
