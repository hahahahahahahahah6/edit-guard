"""Core staleness-detection logic for edit-guard.

Model: every Read/Edit/Write observed through the hooks records
(session_id, tool, path, digest_seen, timestamp) in an append-only log.
PreToolUse records the digest *before* a write; PostToolUse records the
digest *after* the write lands. When a session attempts Edit/Write on a
path, we compare the file's current digest against the digest *that session*
last saw (usually its own post-write record). If the file changed since
then AND another session *wrote* to it in between, the edit is stale and
blocked. Another session's Read never counts as a modification.

Everything fails open: any unexpected error means "allow".
"""

import hashlib
import json
import os
import time

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read"}
WATCHED = WRITE_TOOLS | READ_TOOLS

MAX_LOG_LINES = 20000
PRUNE_KEEP = 10000
MAX_HASH_BYTES = 50 * 1024 * 1024


def state_dir():
    return os.environ.get(
        "EDIT_GUARD_DIR",
        os.path.join(os.path.expanduser("~"), ".config", "edit-guard"))


def log_path():
    return os.path.join(state_dir(), "writes.jsonl")


def digest_of(path):
    """sha256 of file contents; falls back to mtime+size for huge files."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    try:
        if size > MAX_HASH_BYTES:
            st = os.stat(path)
            return "stat:%d:%d" % (st.st_mtime_ns, st.st_size)
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return "sha256:" + h.hexdigest()
    except OSError:
        return None


def _read_log():
    """Return list of entry dicts; tolerate missing/corrupt log."""
    entries = []
    try:
        fh = open(log_path(), "r", encoding="utf-8", errors="replace")
    except OSError:
        return entries
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict) and obj.get("path"):
                entries.append(obj)
    return entries


def _prune_if_needed():
    p = log_path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    if len(lines) <= MAX_LOG_LINES:
        return
    try:
        with open(p, "w", encoding="utf-8") as fh:
            fh.writelines(lines[-PRUNE_KEEP:])
    except OSError:
        pass


def record(session_id, tool, path, digest):
    """Append an observation. Never raises."""
    try:
        os.makedirs(state_dir(), exist_ok=True)
        entry = {"ts": time.time(), "session": session_id,
                 "tool": tool, "path": path, "digest": digest}
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        _prune_if_needed()
    except OSError:
        pass


def check(session_id, path):
    """Decide whether session_id may write path right now.

    Returns (allowed: bool, reason: str). Never raises.
    """
    try:
        return _check_inner(session_id, path)
    except Exception:
        return True, ""  # fail open


def _check_inner(session_id, path):
    current = digest_of(path)
    if current is None:
        return True, ""  # new file or unreadable: nothing to be stale against

    entries = [e for e in _read_log() if e.get("path") == path]

    # Pass 1: my most recent observation of this path.
    mine = None
    for e in entries:
        if e.get("session") == session_id:
            if mine is None or e.get("ts", 0) > mine.get("ts", 0):
                mine = e

    if mine is None:
        # I have never seen this file; someone else may have. First write
        # is allowed (MVP does not track pre-read staleness for writers
        # who never read through the hook), but we record it.
        return True, ""

    if mine.get("digest") == current:
        return True, ""  # unchanged since I last saw it

    # Pass 2: another session's *write* newer than my last observation.
    # Reads can never modify the file, so they are not blame candidates.
    other_newer = None
    for e in entries:
        if e.get("session") == session_id:
            continue
        if e.get("tool") not in WRITE_TOOLS:
            continue
        if e.get("ts", 0) > mine.get("ts", 0):
            if other_newer is None or e.get("ts", 0) > other_newer.get("ts", 0):
                other_newer = e

    if other_newer is not None:
        other = other_newer.get("session", "another session")
        return (False,
                "Stale edit blocked: '%s' changed since you last saw it "
                "(last modified by session '%s'). Re-read the file before "
                "editing." % (path, other))
    # Changed, but only by me (e.g. external tool or my own earlier write
    # that raced the log): allow, this session's assumptions are its own.
    return True, ""
