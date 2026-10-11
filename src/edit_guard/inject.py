"""Session-start context injection for edit-guard.

The other half of the tool, complementing the pre-edit refusal hook in
guard.py:

- guard.py plays **defense**: block a session from writing a file another
  session changed underneath it.
- this module plays **offense**: at session start, re-read the project's
  recorded architecture decisions (``docs/adr/*.md``, ``adr/``, or
  ``DECISIONS.md``) so a fresh agent does not violate decisions made in a
  previous session — the idea popularized by uehara's ADR hook.

The command is meant to be called from a Claude Code ``SessionStart`` hook::

    edit-guard inject --session-start

Its stdout is plain markdown; Claude Code adds hook stdout to the session
context automatically. Everything is best-effort: no decision files means
empty output, any error means empty output, and the hook never fails.
"""

import glob
import os
import re

DEFAULT_MAX_CHARS = 4000
MAX_FILE_READ = 100 * 1024  # never read more than this per decision file
MIN_PER_FILE = 120          # each decision gets at least this many chars

DEFAULT_ADR_DIRS = ("docs/adr", "adr")
DEFAULT_DECISIONS_FILE = "DECISIONS.md"

HEADER = [
    "## Recorded project decisions (edit-guard session-start inject)",
    "",
    "These are decisions this project already recorded. Do not contradict",
    "them in a new session unless there is an explicit reason to revisit one:",
]


def discover(adr_dir=None, decisions_file=None):
    """Return [(kind, path)] decision sources, in display order.

    ADR files come first (sorted by filename), then DECISIONS.md.
    ``decisions_file`` may be an empty string to disable the fallback.
    """
    sources = []
    dirs = [adr_dir] if adr_dir else list(DEFAULT_ADR_DIRS)
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for path in sorted(glob.glob(os.path.join(d, "*.md"))):
            sources.append(("adr", path))
    if decisions_file is None:
        decisions_file = DEFAULT_DECISIONS_FILE
    if decisions_file and os.path.isfile(decisions_file):
        sources.append(("decisions", decisions_file))
    return sources


def _first_heading(text):
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return None


def _status(text):
    m = re.search(r"(?im)^\s*(?:status|state)\s*:\s*(.+?)\s*$", text)
    return m.group(1) if m else None


def _summary(text):
    """First paragraph of the ## Decision section, else first paragraph."""
    m = re.search(r"(?im)^#{1,3}\s*decision\s*$", text)
    body = text[m.end():] if m else text
    paras = []
    for chunk in re.split(r"\n\s*\n", body):
        chunk = chunk.strip()
        if chunk and not chunk.startswith("#"):
            paras.append(re.sub(r"\s+", " ", chunk))
    if paras:
        return paras[0]
    return ""


def summarize(path, budget):
    """One bullet for a decision file, capped at ``budget`` chars."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(MAX_FILE_READ)
    except OSError:
        return ""
    title = _first_heading(text) or os.path.basename(path)
    status = _status(text)
    summary = _summary(text)
    bullet = "- %s" % title
    if status:
        bullet += " (status: %s)" % status
    if summary:
        bullet += "\n  %s" % summary
    if len(bullet) > budget and budget > 1:
        bullet = bullet[:budget - 1].rstrip() + "\u2026"
    return bullet


def render(adr_dir=None, decisions_file=None, max_chars=DEFAULT_MAX_CHARS):
    """Build the inject snippet. Returns "" when there is nothing to inject."""
    try:
        return _render_inner(adr_dir, decisions_file, max_chars)
    except Exception:
        return ""  # fail silent: never break a session start


def _render_inner(adr_dir, decisions_file, max_chars):
    sources = discover(adr_dir, decisions_file)
    if not sources:
        return ""
    header = "\n".join(HEADER)
    per_file = max(MIN_PER_FILE, (max_chars - len(header)) // len(sources))
    bullets = [summarize(p, per_file) for _, p in sources]
    bullets = [b for b in bullets if b]
    if not bullets:
        return ""
    out = header + "\n\n" + "\n".join(bullets)
    if len(out) > max_chars and max_chars > 1:
        out = out[:max_chars - 1].rstrip() + "\u2026"
    return out
