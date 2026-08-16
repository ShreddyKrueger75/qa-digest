"""
Unit tests for the folder watcher's pure logic.

No ffmpeg, no Whisper, no launchd — these cover the decisions the watcher makes
before it ever shells out: config merging, mode detection, retry budgeting, and
command construction.

    python3 -m unittest discover tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "watch"))

import qa_digest_watch as dw  # noqa: E402


def cfg(**overrides):
    return dw.deep_merge(dw.DEFAULTS, overrides)


class TestDeepMerge(unittest.TestCase):
    def test_scalar_override(self):
        self.assertEqual(cfg(mode="strict")["mode"], "strict")

    def test_untouched_keys_survive(self):
        self.assertEqual(cfg(mode="strict")["model"], dw.DEFAULTS["model"])

    def test_nested_dict_merges_rather_than_replaces(self):
        merged = cfg(email={"to": "a@b.com"})
        self.assertEqual(merged["email"]["to"], "a@b.com")
        # The rest of the email block must survive a partial override.
        self.assertEqual(merged["email"]["smtp_port"],
                         dw.DEFAULTS["email"]["smtp_port"])


class TestModeFromFilename(unittest.TestCase):
    def test_reads_mode_token(self):
        self.assertEqual(dw.mode_from_filename("/x/bug.strict.mov", "standard"),
                         "strict")

    def test_case_insensitive(self):
        self.assertEqual(dw.mode_from_filename("/x/bug.INSANO.mp4", "standard"),
                         "insano")

    def test_falls_back_when_absent(self):
        self.assertEqual(dw.mode_from_filename("/x/plain.mov", "lenient"),
                         "lenient")

    def test_ignores_non_mode_tokens(self):
        self.assertEqual(dw.mode_from_filename("/x/v1.2.final.mov", "standard"),
                         "standard")

    def test_last_mode_token_wins(self):
        self.assertEqual(
            dw.mode_from_filename("/x/a.lenient.strict.mov", "standard"),
            "strict")


class TestPlaceholder(unittest.TestCase):
    def test_detects_icloud_placeholder(self):
        self.assertTrue(dw.is_icloud_placeholder(".clip.mov.icloud"))

    def test_plain_file_is_not_placeholder(self):
        self.assertFalse(dw.is_icloud_placeholder("clip.mov"))

    def test_resolves_target_name(self):
        self.assertEqual(dw.placeholder_target("/d/.clip.mov.icloud"),
                         "/d/clip.mov")


class TestBuildCommand(unittest.TestCase):
    def test_output_dir_defaults_beside_video(self):
        _, outdir, _ = dw.build_command("/v/clip.mov", cfg())
        self.assertEqual(outdir, "/v/clip.mov.digest")

    def test_explicit_output_dir_is_used(self):
        _, outdir, _ = dw.build_command("/v/clip.mov", cfg(output_dir="/out"))
        self.assertEqual(outdir, "/out/clip.digest")

    def test_flags_are_passed_through(self):
        cmd, _, _ = dw.build_command("/v/clip.mov", cfg(no_report=True,
                                                        no_transcribe=True))
        self.assertIn("--no-report", cmd)
        self.assertIn("--no-transcribe", cmd)

    def test_flags_absent_when_disabled(self):
        cmd, _, _ = dw.build_command("/v/clip.mov", cfg())
        self.assertNotIn("--no-report", cmd)
        # --analyze was removed entirely (needed an API key)
        self.assertNotIn("--analyze", cmd)

    def test_extra_args_appended(self):
        cmd, _, _ = dw.build_command(
            "/v/clip.mov", cfg(extra_args=["--frame-width", "800"]))
        self.assertEqual(cmd[-2:], ["--frame-width", "800"])

    def test_filename_override_beats_config(self):
        _, _, mode = dw.build_command("/v/clip.strict.mov", cfg(mode="lenient"))
        self.assertEqual(mode, "strict")

    def test_filename_override_can_be_disabled(self):
        _, _, mode = dw.build_command(
            "/v/clip.strict.mov", cfg(mode="lenient",
                                      filename_mode_override=False))
        self.assertEqual(mode, "lenient")


class TestNeedsProcessing(unittest.TestCase):
    """The retry budget is the part that can burn CPU forever if it's wrong."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".mov", delete=False)
        self.tmp.write(b"x" * 128)
        self.tmp.close()
        self.path = self.tmp.name
        self.key = os.path.abspath(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def _record(self, **kw):
        base = dict(dw.file_key(self.path))
        base.update(kw)
        return {self.key: base}

    def test_unknown_file_is_processed(self):
        self.assertTrue(dw.needs_processing(self.path, {}, cfg()))

    def test_succeeded_and_unchanged_is_skipped(self):
        state = self._record(status="ok")
        self.assertFalse(dw.needs_processing(self.path, state, cfg()))

    def test_succeeded_but_changed_is_reprocessed(self):
        state = self._record(status="ok", size=1)
        self.assertTrue(dw.needs_processing(self.path, state, cfg()))

    def test_changed_ignored_when_reprocess_disabled(self):
        state = self._record(status="ok", size=1)
        self.assertFalse(dw.needs_processing(
            self.path, state, cfg(reprocess_on_change=False)))

    def test_failure_retries_within_budget(self):
        state = self._record(status="error", attempts=1)
        self.assertTrue(dw.needs_processing(
            self.path, state, cfg(max_retries=3)))

    def test_failure_stops_at_budget(self):
        state = self._record(status="error", attempts=3)
        self.assertFalse(dw.needs_processing(
            self.path, state, cfg(max_retries=3)))

    def test_edited_file_gets_a_fresh_budget(self):
        state = self._record(status="error", attempts=99, size=1)
        self.assertTrue(dw.needs_processing(
            self.path, state, cfg(max_retries=3)))

    def test_missing_file_is_not_processed(self):
        state = self._record(status="ok")
        os.unlink(self.path)
        self.assertFalse(dw.needs_processing(self.path, state, cfg()))
        open(self.path, "w").close()  # so tearDown succeeds


class TestConfigValidation(unittest.TestCase):
    def test_move_without_archive_dir_is_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as fh:
            fh.write('{"on_success": "move"}')
            path = fh.name
        try:
            with self.assertRaises(SystemExit):
                dw.load_config(path)
        finally:
            os.unlink(path)

    def test_bad_mode_is_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as fh:
            fh.write('{"mode": "nonsense"}')
            path = fh.name
        try:
            with self.assertRaises(SystemExit):
                dw.load_config(path)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
