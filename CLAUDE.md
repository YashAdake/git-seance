# git-seance: notes for agents

Hacktoberfest 2026 DEV challenge entry (Week 4, "Ghost in the Machine"). Deadline: Oct 31 2026 11:59 PM PDT.
Commits after the deadline must be listed in the README.

- Python standard library only (the dev laptop runs Python 3.15 with no wheels). Do not add dependencies.
- Tests: `python -m unittest discover -s tests -t .` (no pytest).
- The model is any OpenAI-compatible endpoint; the default is Jarvislabs Gemma 4 (`gemma-4-26b-a4b-it`). Keys live only in `.env`.
- Everything from a repo or a model is untrusted: redact it (seance/redact.py) before it reaches the model, and escape it (web/app.js `esc`) before it reaches the DOM.
- The model only sees `render_records()` output. Any new fact the ghost should know must be added there.
- Run locally: `python -m seance serve <repo-path> --open` (port 8666).
