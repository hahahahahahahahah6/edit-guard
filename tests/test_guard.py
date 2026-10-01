"""Smoke tests for edit-guard: staleness detection across fake sessions."""

import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from edit_guard import guard, cli  # noqa: E402


def fresh_env():
    tmp = tempfile.mkdtemp()
    os.environ["EDIT_GUARD_DIR"] = os.path.join(tmp, "state")
    return tmp


def write_file(path, text):
    with open(path, "w") as fh:
        fh.write(text)


def hook_stdin(session, tool, path):
    return json.dumps({"session_id": session, "tool_name": tool,
                       "tool_input": {"file_path": path}})


def run_hook(stdin_text, observe=False, post_write=False):
    old_stdin, old_stderr = sys.stdin, sys.stderr
    sys.stdin = io.StringIO(stdin_text)
    sys.stderr = io.StringIO()
    try:
        rc = cli.cmd_hook(
            type("A", (), {"observe": observe, "post_write": post_write})())
        err = sys.stderr.getvalue()
    finally:
        sys.stdin, sys.stderr = old_stdin, old_stderr
    return rc, err


def do_edit(session, f, new_text):
    """Simulate the full hook cycle: PreToolUse -> write -> PostToolUse."""
    rc, err = run_hook(hook_stdin(session, "Edit", f))
    assert rc == 0, "pre-edit unexpectedly blocked: %s" % err
    write_file(f, new_text)
    rc, _ = run_hook(hook_stdin(session, "Edit", f), post_write=True)
    assert rc == 0, "post-write hook should never block"
    return rc


def test_cross_session_stale_write_blocked():
    tmp = fresh_env()
    f = os.path.join(tmp, "shared.py")
    write_file(f, "v1")
    assert run_hook(hook_stdin("sess-A", "Read", f), observe=True)[0] == 0
    do_edit("sess-B", f, "v2")  # B's edit lands via the full hook cycle
    # A now tries to edit without re-reading: B's write is newer than A's read.
    rc, err = run_hook(hook_stdin("sess-A", "Edit", f))
    assert rc == 2, "expected block, got %d" % rc
    assert "Stale edit blocked" in err
    assert "sess-B" in err  # the writer is correctly blamed
    print("cross-session stale blocked ok")


def test_same_session_rewrite_allowed():
    tmp = fresh_env()
    f = os.path.join(tmp, "mine.py")
    write_file(f, "v1")
    assert run_hook(hook_stdin("sess-A", "Read", f), observe=True)[0] == 0
    do_edit("sess-A", f, "v2")
    # Second edit: my post-write digest matches current -> allowed directly.
    rc, err = run_hook(hook_stdin("sess-A", "Edit", f))
    assert rc == 0, "own second edit blocked: %s" % err
    print("same-session rewrite ok")


def test_read_then_write_no_external_change():
    tmp = fresh_env()
    f = os.path.join(tmp, "calm.py")
    write_file(f, "v1")
    assert run_hook(hook_stdin("sess-A", "Read", f), observe=True)[0] == 0
    rc, _ = run_hook(hook_stdin("sess-A", "Edit", f))
    assert rc == 0
    print("read-then-write ok")


def test_new_file_write_allowed():
    tmp = fresh_env()
    f = os.path.join(tmp, "brand-new.py")  # does not exist
    rc, _ = run_hook(hook_stdin("sess-A", "Write", f))
    assert rc == 0
    print("new file ok")


def test_corrupt_log_fails_open():
    tmp = fresh_env()
    os.makedirs(os.environ["EDIT_GUARD_DIR"], exist_ok=True)
    with open(guard.log_path(), "w") as fh:
        fh.write("garbage{{{\nnot json\n")
    f = os.path.join(tmp, "x.py")
    write_file(f, "v1")
    rc, _ = run_hook(hook_stdin("sess-A", "Edit", f))
    assert rc == 0
    print("corrupt log fail-open ok")


def test_malformed_stdin_fails_open():
    fresh_env()
    rc, _ = run_hook("this is not json at all")
    assert rc == 0
    rc, _ = run_hook("{}")
    assert rc == 0
    print("malformed stdin ok")


def test_install_merges_without_clobbering():
    tmp = fresh_env()
    fake_home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(fake_home, ".claude"))
    sp = os.path.join(fake_home, ".claude", "settings.json")
    with open(sp, "w") as fh:
        json.dump({"theme": "dark", "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-check"}]}
        ]}}, fh)
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = fake_home
    try:
        rc = cli.cmd_install(type("A", (), {})())
        assert rc == 0
        settings = json.load(open(sp))
        assert settings["theme"] == "dark"  # untouched
        matchers = [h["matcher"] for h in settings["hooks"]["PreToolUse"]]
        assert "Bash" in matchers
        assert "Edit|Write|MultiEdit|NotebookEdit" in matchers
        assert "Read" in matchers
        # idempotent: second install adds nothing
        rc = cli.cmd_install(type("A", (), {})())
        assert rc == 0
        settings = json.load(open(sp))
        # PreToolUse: original Bash entry + edit-guard Edit + edit-guard Read
        assert len(settings["hooks"]["PreToolUse"]) == 3
        # PostToolUse: edit-guard post-write entry
        assert len(settings["hooks"]["PostToolUse"]) == 1
        assert "--post-write" in settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
    print("install merge ok")


def test_other_session_read_never_blocks():
    # Regression: session B only READS the file. Session A edits twice in a
    # row. The second edit must be allowed -- a Read cannot modify the file,
    # so B must never be blamed for a stale edit.
    tmp = fresh_env()
    f = os.path.join(tmp, "shared2.py")
    write_file(f, "v1")
    assert run_hook(hook_stdin("sess-B", "Read", f), observe=True)[0] == 0
    do_edit("sess-A", f, "v2")  # A's first edit lands via the full cycle
    assert run_hook(hook_stdin("sess-B", "Read", f), observe=True)[0] == 0
    # A's post-write digest matches current -> allowed, B not blamed.
    rc, err = run_hook(hook_stdin("sess-A", "Edit", f))
    assert rc == 0, "second edit by same session wrongly blocked: %s" % err
    assert "sess-B" not in err
    print("other-session read never blocks ok")


def test_post_write_hook_never_blocks_and_records():
    # The PostToolUse hook records the post-write digest and always exits 0.
    tmp = fresh_env()
    f = os.path.join(tmp, "post.py")
    write_file(f, "v1")
    rc, _ = run_hook(hook_stdin("sess-A", "Edit", f), post_write=True)
    assert rc == 0
    entries = [e for e in guard._read_log()
               if e.get("session") == "sess-A" and e.get("path") == f]
    assert len(entries) == 1
    assert entries[0]["digest"] == guard.digest_of(f)
    print("post-write hook ok")


if __name__ == "__main__":
    test_other_session_read_never_blocks()
    test_cross_session_stale_write_blocked()
    test_same_session_rewrite_allowed()
    test_read_then_write_no_external_change()
    test_new_file_write_allowed()
    test_corrupt_log_fails_open()
    test_malformed_stdin_fails_open()
    test_install_merges_without_clobbering()
    test_post_write_hook_never_blocks_and_records()
    print("ALL EDIT-GUARD TESTS PASSED")
