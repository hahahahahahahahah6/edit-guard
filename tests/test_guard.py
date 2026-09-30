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


def run_hook(stdin_text, observe=False):
    old_stdin, old_stderr = sys.stdin, sys.stderr
    sys.stdin = io.StringIO(stdin_text)
    sys.stderr = io.StringIO()
    try:
        rc = cli.cmd_hook(type("A", (), {"observe": observe})())
        err = sys.stderr.getvalue()
    finally:
        sys.stdin, sys.stderr = old_stdin, old_stderr
    return rc, err


def test_cross_session_stale_write_blocked():
    tmp = fresh_env()
    f = os.path.join(tmp, "shared.py")
    write_file(f, "v1")
    assert run_hook(hook_stdin("sess-A", "Read", f), observe=True)[0] == 0
    write_file(f, "v2")  # session B edits outside the hook (or via hook)
    assert run_hook(hook_stdin("sess-B", "Edit", f))[0] == 0  # B's first touch: allowed
    write_file(f, "v3")
    rc, err = run_hook(hook_stdin("sess-A", "Edit", f))
    assert rc == 2, "expected block, got %d" % rc
    assert "Stale edit blocked" in err
    print("cross-session stale blocked ok")


def test_same_session_rewrite_allowed():
    tmp = fresh_env()
    f = os.path.join(tmp, "mine.py")
    write_file(f, "v1")
    assert run_hook(hook_stdin("sess-A", "Read", f), observe=True)[0] == 0
    assert run_hook(hook_stdin("sess-A", "Edit", f))[0] == 0
    write_file(f, "v2")
    assert run_hook(hook_stdin("sess-A", "Edit", f))[0] == 0  # own change: allowed
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
        assert len(settings["hooks"]["PreToolUse"]) == 3
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
    print("install merge ok")


if __name__ == "__main__":
    test_cross_session_stale_write_blocked()
    test_same_session_rewrite_allowed()
    test_read_then_write_no_external_change()
    test_new_file_write_allowed()
    test_corrupt_log_fails_open()
    test_malformed_stdin_fails_open()
    test_install_merges_without_clobbering()
    print("ALL EDIT-GUARD TESTS PASSED")
