# edit-guard

A tiny [Claude Code](https://docs.anthropic.com/en/docs/claude-code) `PreToolUse`
hook that stops one agent session from editing a file another session changed
underneath it.

The failure mode, quoted from a real-world report: *"With several agent
sessions on one repository, session A reads `createSession`, session B
changes it, and A then edits on the old assumption."* edit-guard watches every
`Read`/`Edit`/`Write` through the hook, remembers what each session last saw,
and blocks the write when the file moved on without you — telling the agent to
re-read first.

Zero dependencies. Python standard library only. Everything stays local.

## Install

```bash
pip install edit-guard
edit-guard install
```

`install` merges two hook entries into `~/.claude/settings.json` (backing it up
first, never clobbering your existing settings):

- `Edit|Write|MultiEdit|NotebookEdit` → `edit-guard hook` (may block)
- `Read` → `edit-guard hook --observe` (records what you saw, never blocks)

Restart Claude Code afterwards. That's it — no MCP server, no daemon, no
accounts.

## How it works

Each hook invocation appends one line to `~/.config/edit-guard/writes.jsonl`:
`{session_id, tool, path, digest_seen, timestamp}`. When a session tries to
write a file:

1. Hash the file's current content (sha256; mtime+size for files over 50 MB).
2. Compare against the digest **that session** last saw for that path.
3. If it changed **and** another session touched the file in between → block
   (exit 2) with: *"Stale edit blocked: '…' changed since you last saw it
   (last modified by session '…'). Re-read the file before editing."*

Same-session rewrites, new files, and files only you touched are always
allowed. The hook **fails open**: any internal error (corrupt log, unreadable
file, malformed input) means "allow".

Inspect what's being tracked:

```bash
edit-guard log            # recent guarded file events
edit-guard status         # state dir / log / settings paths
```

## Honest limitations

- **Write-after-write staleness only.** The guard compares against what your
  session last *saw through the hook*. Edits made outside any hooked session
  (your editor, a script) are treated as your own session's drift and allowed.
- **Not a lock.** This is advisory staleness detection, not mutual exclusion.
  Two sessions can still race within the same second; the loser is blocked,
  the winner wins.
- **Digest, not semantics.** A reformat that changes bytes but not meaning
  still counts as "changed" and can block you. Re-read and retry.
- **Hook protocol is undocumented.** The PreToolUse stdin shape (`session_id`,
  `tool_name`, `tool_input`) and the exit-2-blocks convention come from
  community documentation, not a stable API. If Claude Code changes the
  protocol, the hook degrades to fail-open allow.
- **Per-machine only.** Sessions on different machines don't share the log.
- The log is append-only with light pruning (keeps the last 10k of 20k lines);
  it is not a backup or audit trail.

## Development

```bash
python3 tests/test_guard.py
```

## License

MIT
