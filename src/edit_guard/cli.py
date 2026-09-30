"""edit-guard: a PreToolUse hook that blocks stale concurrent edits."""

import argparse
import json
import os
import shutil
import sys

from . import guard


def _hook_input():
    """Read the hook's stdin JSON defensively."""
    try:
        raw = sys.stdin.read()
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _tool_path(tool_name, tool_input):
    if not isinstance(tool_input, dict):
        return None
    for key in ("file_path", "path", "file", "filename"):
        v = tool_input.get(key)
        if v:
            return os.path.abspath(os.path.expanduser(str(v)))
    return None


def cmd_hook(args):
    """Entry point for the PreToolUse hook. Exits 0 (allow) or 2 (block)."""
    try:
        data = _hook_input()
        tool = data.get("tool_name", "")
        session = str(data.get("session_id", "") or "unknown")
        if tool not in guard.WATCHED:
            return 0
        path = _tool_path(tool, data.get("tool_input"))
        if not path:
            return 0

        if tool in guard.READ_TOOLS or args.observe:
            # Observe-only: record what this session saw, never block.
            guard.record(session, tool, path, guard.digest_of(path))
            return 0

        allowed, reason = guard.check(session, path)
        guard.record(session, tool, path, guard.digest_of(path))
        if not allowed:
            sys.stderr.write(reason + "\n")
            return 2
        return 0
    except Exception:
        return 0  # fail open, always


def _settings_path():
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


HOOK_SNIPPET = {
    "matcher": "Edit|Write|MultiEdit|NotebookEdit",
    "hooks": [{"type": "command", "command": "edit-guard hook"}],
}
READ_SNIPPET = {
    "matcher": "Read",
    "hooks": [{"type": "command", "command": "edit-guard hook --observe"}],
}


def cmd_install(args):
    """Merge the hook into ~/.claude/settings.json (with backup)."""
    sp = _settings_path()
    settings = {}
    if os.path.exists(sp):
        try:
            with open(sp, "r", encoding="utf-8") as fh:
                settings = json.load(fh)
            if not isinstance(settings, dict):
                settings = {}
        except Exception as e:
            print("Could not parse %s: %s" % (sp, e), file=sys.stderr)
            return 1
        bak = sp + ".bak"
        try:
            shutil.copy2(sp, bak)
            print("Backed up %s -> %s" % (sp, bak))
        except OSError as e:
            print("Backup failed: %s" % e, file=sys.stderr)
            return 1

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
    pre = hooks.get("PreToolUse")
    if not isinstance(pre, list):
        pre = []
        hooks["PreToolUse"] = pre

    def present(snippet):
        return any(
            h.get("matcher") == snippet["matcher"]
            and any("edit-guard" in (x.get("command", "") or "")
                    for x in h.get("hooks", []))
            for h in pre if isinstance(h, dict))

    added = 0
    for snippet in (HOOK_SNIPPET, READ_SNIPPET):
        if not present(snippet):
            pre.append(snippet)
            added += 1

    os.makedirs(os.path.dirname(sp), exist_ok=True)
    with open(sp, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2)
        fh.write("\n")
    print("Installed edit-guard hooks into %s (%d added, %d already present)."
          % (sp, added, 2 - added))
    print("Restart Claude Code for the hooks to take effect.")
    return 0


def cmd_log(args):
    entries = guard._read_log()
    entries.sort(key=lambda e: e.get("ts", 0))
    for e in entries[-args.limit:]:
        import datetime
        ts = datetime.datetime.fromtimestamp(e.get("ts", 0)).strftime("%m-%d %H:%M:%S")
        print("%s  %-12s %-10s %s" % (
            ts, str(e.get("session", ""))[:12],
            e.get("tool", ""), e.get("path", "")))
    return 0


def cmd_status(args):
    print("state dir: %s" % guard.state_dir())
    print("log:       %s (%d entries)" % (guard.log_path(), len(guard._read_log())))
    print("settings:  %s" % _settings_path())
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="edit-guard",
        description="Block stale concurrent edits across agent sessions.")
    sub = p.add_subparsers(dest="cmd", required=True)

    ph = sub.add_parser("hook", help="Run as a Claude Code PreToolUse hook.")
    ph.add_argument("--observe", action="store_true",
                    help="Record only, never block (for Read).")
    ph.set_defaults(func=cmd_hook)

    pi = sub.add_parser("install", help="Install into ~/.claude/settings.json.")
    pi.set_defaults(func=cmd_install)

    pl = sub.add_parser("log", help="Show recent guarded file events.")
    pl.add_argument("--limit", type=int, default=20)
    pl.set_defaults(func=cmd_log)

    ps = sub.add_parser("status", help="Show state and config paths.")
    ps.set_defaults(func=cmd_status)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
