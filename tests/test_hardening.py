"""
Tests for the hardening pass: U+202F filename resolution, model-in-config
precedence, confidence-based escalation, and the model ladder.

    python3 -m unittest tests.test_hardening -v
"""

import argparse
import importlib.util
import os
import shutil
import tempfile
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "qa_digest",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "qa_digest.py"),
)
qd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(qd)

NARROW = " "


class TestResolveSpaceVariants(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _touch(self, name):
        path = os.path.join(self.dir, name)
        open(path, "w").close()
        return path

    def test_narrow_space_file_found_from_plain_path(self):
        real = self._touch("Screen Recording at 5.39.03" + NARROW + "PM.mov")
        asked = os.path.join(self.dir, "Screen Recording at 5.39.03 PM.mov")
        self.assertEqual(qd.resolve_space_variants(asked), os.path.abspath(real))

    def test_plain_space_file_found_from_narrow_path(self):
        real = self._touch("clip a.mov")
        asked = os.path.join(self.dir, "clip" + NARROW + "a.mov")
        self.assertEqual(qd.resolve_space_variants(asked), os.path.abspath(real))

    def test_no_match_returns_none(self):
        asked = os.path.join(self.dir, "nothing here.mov")
        self.assertIsNone(qd.resolve_space_variants(asked))

    def test_ambiguous_match_returns_none(self):
        # Two files that both match the pattern: refuse to guess.
        self._touch("clip a.mov")
        self._touch("clip" + NARROW + "a.mov")
        asked = os.path.join(self.dir, "clip a.mov")
        # The literal file exists here, but resolve is only called on a miss;
        # simulate the miss by asking with a third space variant.
        asked = os.path.join(self.dir, "clip a.mov")
        self.assertIsNone(qd.resolve_space_variants(asked))

    def test_glob_chars_in_filename_are_escaped(self):
        real = self._touch("clip [1] a.mov")
        asked = os.path.join(self.dir, "clip [1]" + NARROW + "a.mov")
        self.assertEqual(qd.resolve_space_variants(asked), os.path.abspath(real))


class TestModelLadder(unittest.TestCase):
    def test_order(self):
        self.assertEqual(qd.MODEL_LADDER,
                         ["tiny", "base", "small", "medium", "large-v3"])

    def test_threshold_sane(self):
        self.assertGreater(qd.ESCALATE_THRESHOLD, 0)
        self.assertLess(qd.ESCALATE_THRESHOLD, 1)


def _args(model="base"):
    return argparse.Namespace(model=model, language=None, compute_type="int8")


def _seg(lp):
    return {"avg_logprob": lp, "no_speech_prob": 0.0, "text": "x"}


class TestMaybeEscalate(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.retry_result = None
        self._orig = qd.transcribe

        def fake_transcribe(wav, model, language, compute_type):
            self.calls.append(model)
            return self.retry_result
        qd.transcribe = fake_transcribe

    def tearDown(self):
        qd.transcribe = self._orig

    def test_good_transcript_is_untouched(self):
        tr = {"segments": [_seg(-0.2)] * 10}
        out = qd.maybe_escalate_model(tr, "w.wav", _args())
        self.assertIs(out, tr)
        self.assertEqual(self.calls, [])

    def test_bad_transcript_escalates_one_step(self):
        tr = {"segments": [_seg(-2.0)] * 10}
        self.retry_result = {"segments": [_seg(-0.2)] * 10}
        out = qd.maybe_escalate_model(tr, "w.wav", _args("base"))
        self.assertEqual(self.calls, ["small"])
        self.assertIs(out, self.retry_result)

    def test_no_improvement_keeps_original(self):
        tr = {"segments": [_seg(-2.0)] * 10}
        self.retry_result = {"segments": [_seg(-2.0)] * 10}
        out = qd.maybe_escalate_model(tr, "w.wav", _args("base"))
        self.assertIs(out, tr)

    def test_top_of_ladder_never_escalates(self):
        tr = {"segments": [_seg(-2.0)] * 10}
        out = qd.maybe_escalate_model(tr, "w.wav", _args("large-v3"))
        self.assertIs(out, tr)
        self.assertEqual(self.calls, [])

    def test_empty_transcript_is_untouched(self):
        self.assertIsNone(qd.maybe_escalate_model(None, "w.wav", _args()))
        tr = {"segments": []}
        self.assertIs(qd.maybe_escalate_model(tr, "w.wav", _args()), tr)

    def test_failed_retry_keeps_original(self):
        tr = {"segments": [_seg(-2.0)] * 10}
        self.retry_result = None
        out = qd.maybe_escalate_model(tr, "w.wav", _args("base"))
        self.assertIs(out, tr)

    def test_below_threshold_boundary(self):
        # exactly 30% low-confidence must NOT escalate (threshold is >)
        tr = {"segments": [_seg(-2.0)] * 3 + [_seg(-0.1)] * 7}
        out = qd.maybe_escalate_model(tr, "w.wav", _args())
        self.assertIs(out, tr)
        self.assertEqual(self.calls, [])


class TestAnalyzeGone(unittest.TestCase):
    def test_no_analyze_anywhere(self):
        src = open(os.path.join(os.path.dirname(__file__), "..", "scripts",
                                "qa_digest.py")).read()
        for token in ("analyze", "Anthropic", "ANTHROPIC_API_KEY",
                      "bug_report"):
            self.assertNotIn(token, src,
                             "%s still present after removal" % token)


if __name__ == "__main__":
    unittest.main()
