"""Tests for edit-guard inject: session-start decision injection."""

import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from edit_guard import inject, cli  # noqa: E402

ADR_ONE = """# ADR-0001: Use stdlib only

Status: accepted

## Context

Third-party dependencies kept breaking installs.

## Decision

The tool ships with zero third-party dependencies. All parsing,
hashing, and CLI work uses the Python standard library.
"""


def make_repo(files):
    """Create a temp repo with {relpath: content}; return the dir."""
    repo = tempfile.mkdtemp()
    for rel, content in files.items():
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    return repo


class CwdTestCase(unittest.TestCase):
    def setUp(self):
        self.old_cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self.old_cwd)

    def in_repo(self, files):
        repo = make_repo(files)
        os.chdir(repo)
        return repo


class TestInject(CwdTestCase):
    def test_adr_dir_discovery_and_summary(self):
        self.in_repo({
            "docs/adr/0001-stdlib.md": ADR_ONE,
            "docs/adr/0002-other.md": (
                "# ADR-0002: JSONL state\n\nStatus: proposed\n\n"
                "## Decision\n\nState lives in one JSONL file.\n"),
        })
        out = inject.render()
        self.assertIn("ADR-0001: Use stdlib only", out)
        self.assertIn("accepted", out)
        self.assertIn("zero third-party dependencies", out)
        self.assertIn("ADR-0002: JSONL state", out)  # both files, sorted
        self.assertLess(out.index("ADR-0001"), out.index("ADR-0002"))

    def test_decisions_file_fallback(self):
        self.in_repo({"DECISIONS.md": "# Decisions\n\n- We use sqlite, not postgres.\n"})
        self.assertIn("sqlite", inject.render())

    def test_empty_repo_returns_empty(self):
        self.in_repo({})
        self.assertEqual(inject.render(), "")

    def test_max_chars_truncation(self):
        files = {"docs/adr/%04d.md" % i:
                 "# ADR-%d: Decision number %d\n\nStatus: accepted\n\n## Decision\n\n%s\n"
                 % (i, i, "x" * 5000)
                 for i in range(10)}
        self.in_repo(files)
        out = inject.render(max_chars=1000)
        self.assertLessEqual(len(out), 1000)

    def test_adr_dir_override(self):
        self.in_repo({"custom/0001-x.md": ADR_ONE})
        self.assertEqual(inject.render(), "")  # default dirs miss custom/
        self.assertIn("stdlib only", inject.render(adr_dir="custom"))

    def test_decisions_file_override(self):
        self.in_repo({"notes/CHOICES.md": "# Choices\n\n- Picked boring tech.\n"})
        self.assertIn("boring tech", inject.render(decisions_file="notes/CHOICES.md"))

    def test_no_status_no_decision_section(self):
        self.in_repo({"adr/0001.md": "# Just a title\n\nSome free text here.\n"})
        out = inject.render()
        self.assertIn("Just a title", out)
        self.assertIn("Some free text here", out)

    def test_malformed_adr_never_raises(self):
        self.in_repo({"docs/adr/0001.md": "\x00\x01 binary-ish \xff\xfe"})
        self.assertIsInstance(inject.render(), str)


class TestInjectCLI(CwdTestCase):
    def run_cmd(self, argv):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            ns = cli.build_parser().parse_args(argv)
            rc = ns.func(ns)
            out = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout
        return rc, out

    def test_cli_end_to_end(self):
        self.in_repo({"docs/adr/0001-stdlib.md": ADR_ONE})
        rc, out = self.run_cmd(["inject", "--session-start", "--max-chars", "2000"])
        self.assertEqual(rc, 0)
        self.assertIn("Recorded project decisions", out)
        self.assertIn("Do not contradict", out)

    def test_cli_empty_repo(self):
        self.in_repo({})
        rc, out = self.run_cmd(["inject", "--session-start"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
