"""Tests for edit-guard: staleness detection across fake sessions."""

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from edit_guard import guard, cli  # noqa: E402


class GuardTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        os.environ["EDIT_GUARD_DIR"] = os.path.join(tmp, "state")
        self.tmp = tmp
        self.old_home = os.environ.get("HOME")

    def tearDown(self):
        if self.old_home is not None:
            os.environ["HOME"] = self.old_home
        elif "HOME" in os.environ:
            del os.environ["HOME"]

    def write_file(self, path, text):
        with open(path, "w") as fh:
            fh.write(text)

    def hook_stdin(self, session, tool, path):
        return json.dumps({"session_id": session, "tool_name": tool,
                           "tool_input": {"file_path": path}})

    def run_hook(self, stdin_text, observe=False, post_write=False):
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

    def do_edit(self, session, f, new_text):
        """Simulate the full hook cycle: PreToolUse -> write -> PostToolUse."""
        rc, err = self.run_hook(self.hook_stdin(session, "Edit", f))
        self.assertEqual(rc, 0, "pre-edit unexpectedly blocked: %s" % err)
        self.write_file(f, new_text)
        rc, _ = self.run_hook(self.hook_stdin(session, "Edit", f),
                              post_write=True)
        self.assertEqual(rc, 0, "post-write hook should never block")

    def test_cross_session_stale_write_blocked(self):
        f = os.path.join(self.tmp, "shared.py")
        self.write_file(f, "v1")
        self.assertEqual(
            self.run_hook(self.hook_stdin("sess-A", "Read", f), observe=True)[0], 0)
        self.do_edit("sess-B", f, "v2")  # B's edit lands via the full cycle
        # A now tries to edit without re-reading: B's write is newer than A's read.
        rc, err = self.run_hook(self.hook_stdin("sess-A", "Edit", f))
        self.assertEqual(rc, 2, "expected block, got %d" % rc)
        self.assertIn("Stale edit blocked", err)
        self.assertIn("sess-B", err)  # the writer is correctly blamed

    def test_same_session_rewrite_allowed(self):
        f = os.path.join(self.tmp, "mine.py")
        self.write_file(f, "v1")
        self.assertEqual(
            self.run_hook(self.hook_stdin("sess-A", "Read", f), observe=True)[0], 0)
        self.do_edit("sess-A", f, "v2")
        # Second edit: my post-write digest matches current -> allowed directly.
        rc, err = self.run_hook(self.hook_stdin("sess-A", "Edit", f))
        self.assertEqual(rc, 0, "own second edit blocked: %s" % err)

    def test_read_then_write_no_external_change(self):
        f = os.path.join(self.tmp, "calm.py")
        self.write_file(f, "v1")
        self.assertEqual(
            self.run_hook(self.hook_stdin("sess-A", "Read", f), observe=True)[0], 0)
        rc, _ = self.run_hook(self.hook_stdin("sess-A", "Edit", f))
        self.assertEqual(rc, 0)

    def test_new_file_write_allowed(self):
        f = os.path.join(self.tmp, "brand-new.py")  # does not exist
        rc, _ = self.run_hook(self.hook_stdin("sess-A", "Write", f))
        self.assertEqual(rc, 0)

    def test_corrupt_log_fails_open(self):
        os.makedirs(os.environ["EDIT_GUARD_DIR"], exist_ok=True)
        with open(guard.log_path(), "w") as fh:
            fh.write("garbage{{{\nnot json\n")
        f = os.path.join(self.tmp, "x.py")
        self.write_file(f, "v1")
        rc, _ = self.run_hook(self.hook_stdin("sess-A", "Edit", f))
        self.assertEqual(rc, 0)

    def test_malformed_stdin_fails_open(self):
        rc, _ = self.run_hook("this is not json at all")
        self.assertEqual(rc, 0)
        rc, _ = self.run_hook("{}")
        self.assertEqual(rc, 0)

    def test_install_merges_without_clobbering(self):
        fake_home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(fake_home, ".claude"))
        sp = os.path.join(fake_home, ".claude", "settings.json")
        with open(sp, "w") as fh:
            json.dump({"theme": "dark", "hooks": {"PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command",
                                              "command": "my-check"}]}
            ]}}, fh)
        os.environ["HOME"] = fake_home
        rc = cli.cmd_install(type("A", (), {})())
        self.assertEqual(rc, 0)
        with open(sp) as fh:
            settings = json.load(fh)
        self.assertEqual(settings["theme"], "dark")  # untouched
        matchers = [h["matcher"] for h in settings["hooks"]["PreToolUse"]]
        self.assertIn("Bash", matchers)
        self.assertIn("Edit|Write|MultiEdit|NotebookEdit", matchers)
        self.assertIn("Read", matchers)
        # idempotent: second install adds nothing
        rc = cli.cmd_install(type("A", (), {})())
        self.assertEqual(rc, 0)
        with open(sp) as fh:
            settings = json.load(fh)
        # PreToolUse: original Bash entry + edit-guard Edit + edit-guard Read
        self.assertEqual(len(settings["hooks"]["PreToolUse"]), 3)
        # PostToolUse: edit-guard post-write entry
        self.assertEqual(len(settings["hooks"]["PostToolUse"]), 1)
        self.assertIn("--post-write",
                      settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"])

    def test_other_session_read_never_blocks(self):
        # Regression: session B only READS the file. Session A edits twice in
        # a row. The second edit must be allowed -- a Read cannot modify the
        # file, so B must never be blamed for a stale edit.
        f = os.path.join(self.tmp, "shared2.py")
        self.write_file(f, "v1")
        self.assertEqual(
            self.run_hook(self.hook_stdin("sess-B", "Read", f), observe=True)[0], 0)
        self.do_edit("sess-A", f, "v2")  # A's first edit lands via full cycle
        self.assertEqual(
            self.run_hook(self.hook_stdin("sess-B", "Read", f), observe=True)[0], 0)
        # A's post-write digest matches current -> allowed, B not blamed.
        rc, err = self.run_hook(self.hook_stdin("sess-A", "Edit", f))
        self.assertEqual(rc, 0, "second edit wrongly blocked: %s" % err)
        self.assertNotIn("sess-B", err)

    def test_post_write_hook_never_blocks_and_records(self):
        # The PostToolUse hook records the post-write digest and always exits 0.
        f = os.path.join(self.tmp, "post.py")
        self.write_file(f, "v1")
        rc, _ = self.run_hook(self.hook_stdin("sess-A", "Edit", f),
                              post_write=True)
        self.assertEqual(rc, 0)
        entries = [e for e in guard._read_log()
                   if e.get("session") == "sess-A" and e.get("path") == f]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["digest"], guard.digest_of(f))


if __name__ == "__main__":
    unittest.main()
