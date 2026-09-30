# Two Claude Code sessions edited the same file. One of them was wrong. I built a hook to stop that

I run multiple Claude Code sessions against the same repo — one refactoring, one fixing tests, sometimes one more exploring. Last week I watched session A carefully edit a function based on code that session B had already rewritten ten minutes earlier. The edit applied cleanly. It was also completely wrong: it reintroduced the old logic on top of the new. I only caught it in `git diff`.

There's a name for this failure mode. Dat Do's measurements post on agent memory put it plainly: *"With several agent sessions on one repository, session A reads `createSession`, session B changes it, and A then edits on the old assumption."* The existing answers are heavyweight: file-reservation MCP servers, session buses with path claims. I wanted something I could install in ten seconds.

So I built `edit-guard`: a single stdlib-only Python script that plugs into Claude Code's `PreToolUse` hook.

## What it does

```bash
pip install edit-guard
edit-guard install   # merges into ~/.claude/settings.json, backs it up first
```

From then on, every `Read` records what your session saw (file hash + timestamp), and every `Edit`/`Write` checks the file's current hash against what *your session* last saw. If the bytes changed and another session touched the file in between, the write is blocked and the agent is told to re-read first:

```
Stale edit blocked: 'src/auth.py' changed since you last saw it
(last modified by session 'a4c2e901'). Re-read the file before editing.
```

Same-session rewrites always pass. New files always pass. A corrupt state log, an unreadable file, anything unexpected — the hook fails open and allows the edit. A safety tool that can wedge your workflow is worse than no tool.

The state is one append-only JSONL file at `~/.config/edit-guard/writes.jsonl`. No daemon, no server, no network. `edit-guard log` shows you what's being tracked.

## The design tension: block vs. warn

I went back and forth on whether a stale write should block or just warn. Warning is friendlier, but in practice the agent reads the warning, says "noted", and edits anyway — I've watched it happen. Blocking forces the re-read, which is the actual fix. The failure mode of blocking is annoyance (a reformat by session B blocks session A); the failure mode of warning is silent corruption. For a tool whose whole job is preventing silent corruption, blocking is the honest default.

## Honest limitations

- It detects write-after-write staleness, not true locking — two sessions racing in the same second still race.
- Byte-level digests mean a pure reformat counts as "changed".
- The hook protocol (stdin shape, exit-2-blocks) is community-documented, not a stable API; if it changes, the hook degrades to allow.
- Per-machine only.

## Links

- GitHub: https://github.com/hahahahahahahahah6/edit-guard (MIT)
- PyPI: `pip install edit-guard`

7 smoke tests pass, including the exact A-reads/B-writes/A-blocked scenario and a fail-open test against a corrupted state log.

If you multi-session a single repo: how do you keep your agents from stepping on each other? I'm curious what breaks first at your scale.
