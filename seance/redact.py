"""Secret guard.

Deleted files are where leaked credentials hide: a key gets committed, someone
notices, the file is deleted, and the key lives on in history. Everything that
leaves the repository (to the model, to the browser) goes through `redact`
first. We report *that* a ghost carried secrets, never the secrets themselves.
"""

from __future__ import annotations

import re
from collections import Counter

# (kind, pattern, group to mask; 0 = whole match)
_PATTERNS: list[tuple[str, re.Pattern, int]] = [
    ("private key", re.compile(
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|\Z)"), 0),
    ("aws access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0),
    ("github token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"), 0),
    ("slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"), 0),
    ("stripe key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"), 0),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), 0),
    ("openai-style key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}\b"), 0),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), 0),
    ("url credentials", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^/\s:@'\"]+:([^@\s'\"/]{3,})@"), 1),
    ("assigned secret", re.compile(
        r"(?i)\b[\w.\-]*(?:api[_\-]?key|secret|token|passw(?:or)?d|pwd|auth[_\-]?key|access[_\-]?key|private[_\-]?key)"
        r"[\w.\-]*\b[\"']?\s*(?:=|:|=>)\s*[\"']([^\"'\s]{8,})[\"']"), 1),
]

_PLACEHOLDER = re.compile(
    r"(?i)^(?:x+|\*+|\.+|<.*>|\$\{.*\}|\{\{.*\}\}|%\(.*\)s|changeme|example|placeholder|your[_\-].*|.*xxxx.*|"
    r"dummy.*|test.*|fake.*|none|null|undefined|secret|password|token|redacted.*)$")


def redact(text: str) -> tuple[str, Counter]:
    """Return (text with secrets masked, Counter of secret kinds found)."""
    found: Counter = Counter()
    if not text:
        return text, found
    for kind, pattern, group in _PATTERNS:
        def _mask(m: re.Match, kind=kind, group=group) -> str:
            value = m.group(group)
            if group and _PLACEHOLDER.match(value):
                return m.group(0)
            found[kind] += 1
            label = f"[REDACTED {kind}]"
            if group == 0:
                return label
            start, end = m.start(group) - m.start(0), m.end(group) - m.start(0)
            whole = m.group(0)
            return whole[:start] + label + whole[end:]
        text = pattern.sub(_mask, text)
    return text, found
