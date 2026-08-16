"""
Unit tests for the pure logic in scripts/file_issues.py.

Nothing here touches the network or the gh CLI — these cover input validation,
path namespacing, and body assembly, which is where a mistake would either
crash mid-upload or silently produce a broken issue.

    python3 -m unittest tests.test_file_issues -v
"""

import importlib.util
import json
import os
import tempfile
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "file_issues",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "file_issues.py"),
)
fi = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fi)


def write_json(payload):
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    if isinstance(payload, str):
        fh.write(payload)
    else:
        json.dump(payload, fh)
    fh.close()
    return fh.name


class TestLoadIssues(unittest.TestCase):
    def test_accepts_a_list(self):
        p = write_json([{"title": "t", "body": "b"}])
        try:
            self.assertEqual(len(fi.load_issues(p)), 1)
        finally:
            os.unlink(p)

    def test_wraps_a_bare_object(self):
        p = write_json({"title": "t", "body": "b"})
        try:
            self.assertEqual(len(fi.load_issues(p)), 1)
        finally:
            os.unlink(p)

    def test_rejects_missing_title(self):
        p = write_json([{"body": "b"}])
        try:
            with self.assertRaises(SystemExit):
                fi.load_issues(p)
        finally:
            os.unlink(p)

    def test_rejects_missing_body(self):
        p = write_json([{"title": "t"}])
        try:
            with self.assertRaises(SystemExit):
                fi.load_issues(p)
        finally:
            os.unlink(p)

    def test_rejects_empty_list(self):
        p = write_json([])
        try:
            with self.assertRaises(SystemExit):
                fi.load_issues(p)
        finally:
            os.unlink(p)

    def test_rejects_malformed_json(self):
        p = write_json("{not json")
        try:
            with self.assertRaises(SystemExit):
                fi.load_issues(p)
        finally:
            os.unlink(p)


class TestRemotePath(unittest.TestCase):
    def test_namespaces_by_digest_and_stamp(self):
        p = fi.remote_path_for("/x/clip.digest", "/x/clip.digest/frames/a.jpg",
                               "20260815-120000")
        self.assertEqual(p, "frames/clip-20260815-120000/a.jpg")

    def test_sanitises_awkward_names(self):
        p = fi.remote_path_for("/x/Screen Recording 2.18 PM.digest",
                               "/f/b.jpg", "S")
        self.assertNotIn(" ", p)
        self.assertTrue(p.startswith("frames/"))

    def test_two_runs_do_not_collide(self):
        a = fi.remote_path_for("/x/clip.digest", "/f/a.jpg", "stamp1")
        b = fi.remote_path_for("/x/clip.digest", "/f/a.jpg", "stamp2")
        self.assertNotEqual(a, b)


class TestBuildBody(unittest.TestCase):
    def test_no_images_returns_body_unchanged(self):
        self.assertEqual(fi.build_body({"body": "hello"}, [], False), "hello")

    def test_images_are_appended_as_markdown(self):
        out = fi.build_body({"body": "b"}, [("http://x/a.jpg", "a.jpg")], False)
        self.assertIn("![a.jpg](http://x/a.jpg)", out)
        self.assertIn("### Keyframes", out)

    def test_public_repo_gets_no_warning(self):
        out = fi.build_body({"body": "b"}, [("http://x/a.jpg", "a")], False)
        self.assertNotIn("private", out)

    def test_private_repo_warns_images_may_not_render(self):
        out = fi.build_body({"body": "b"}, [("http://x/a.jpg", "a")], True)
        self.assertIn("private", out)

    def test_warning_only_when_images_present(self):
        self.assertNotIn("private", fi.build_body({"body": "b"}, [], True))


class TestResolveFrame(unittest.TestCase):
    def test_missing_frame_is_fatal(self):
        with self.assertRaises(SystemExit):
            fi.resolve_frame("/nope", "frames/missing.jpg")

    def test_non_image_is_rejected(self):
        fh = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        fh.write(b"nope")
        fh.close()
        try:
            with self.assertRaises(SystemExit):
                fi.resolve_frame(os.path.dirname(fh.name),
                                 os.path.basename(fh.name))
        finally:
            os.unlink(fh.name)

    def test_absolute_path_is_accepted(self):
        fh = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        fh.write(b"\xff\xd8\xff")
        fh.close()
        try:
            self.assertEqual(fi.resolve_frame("/ignored", fh.name), fh.name)
        finally:
            os.unlink(fh.name)


class TestRawUrl(unittest.TestCase):
    def test_url_shape(self):
        url = fi.RAW_URL.format(repo="o/r", branch="qa-assets",
                                path="frames/x/a.jpg")
        self.assertEqual(
            url, "https://raw.githubusercontent.com/o/r/qa-assets/frames/x/a.jpg")


if __name__ == "__main__":
    unittest.main()
