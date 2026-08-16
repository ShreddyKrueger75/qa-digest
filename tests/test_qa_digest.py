"""Unit tests for the pure logic in scripts/qa_digest.py.

No ffmpeg, whisper, or network needed — these cover timestamp formatting,
frame selection, pointer math, click detection, and the markdown emitters.

Run:  python3 -m pytest tests/
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "qa_digest",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "qa_digest.py"),
)
dm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dm)

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


# --- timestamp formatting ----------------------------------------------------

def test_hms():
    assert dm.hms(0) == "00:00:00"
    assert dm.hms(3661.5) == "01:01:01"
    assert dm.hms(-5) == "00:00:00"
    assert dm.hms(3661, compact=True) == "01h01m01s"


def test_srt_time():
    assert dm.srt_time(0) == "00:00:00,000"
    assert dm.srt_time(3661.25) == "01:01:01,250"
    assert dm.srt_time(-1) == "00:00:00,000"


# --- region / pointer math ---------------------------------------------------

def test_region_label():
    assert dm.region_label(0.5, 0.5) == "center"
    assert dm.region_label(0.1, 0.1) == "top-left"
    assert dm.region_label(0.9, 0.9) == "bottom-right"
    assert dm.region_label(0.5, 0.1) == "top-center"
    assert dm.region_label(0.1, 0.5) == "middle-left"


def test_pointer_of_localizes_change():
    prev = np.zeros((90, 160), dtype="int16")
    cur = prev.copy()
    cur[10:20, 120:140] = 200  # change in the top-right
    p = dm.pointer_of(prev, cur)
    assert p is not None
    assert p["region"] == "top-right"
    assert 0 < p["changed_fraction"] < 0.05


def test_pointer_of_none_when_static():
    frame = np.zeros((90, 160), dtype="int16")
    assert dm.pointer_of(frame, frame) is None


# --- scene picking -----------------------------------------------------------

def test_interval_scenes():
    scenes = dm.interval_scenes(10.0, 5)
    assert len(scenes) == 5
    assert scenes[0] == (0.0, 2.0)
    assert scenes[-1] == (8.0, 10.0)
    assert dm.interval_scenes(0, 5) == []
    assert dm.interval_scenes(10, 0) == []


def test_pick_scenes_no_downsample():
    scenes = [(0, 1), (1, 2)]
    idxs, picked = dm.pick_scenes(scenes, 5)
    assert idxs == [0, 1]
    assert picked == scenes


def test_pick_scenes_downsamples_keeping_ends():
    scenes = [(i, i + 1) for i in range(10)]
    idxs, picked = dm.pick_scenes(scenes, 4)
    assert len(picked) <= 4
    assert idxs[0] == 0 and idxs[-1] == 9


def test_pick_scenes_edge_caps():
    scenes = [(i, i + 1) for i in range(10)]
    idxs, picked = dm.pick_scenes(scenes, 1)  # used to ZeroDivisionError
    assert idxs == [0]
    assert dm.pick_scenes(scenes, 0) == ([], [])


# --- diff-based keyframe selection --------------------------------------------

def _write_frame(path, brightness):
    Image.new("L", (160, 90), color=brightness).save(path, "JPEG")


def test_select_keyframes_keeps_changes_only(tmp_path):
    files = []
    # 0-2 identical, 3 bright (change), 4 identical to 3, 5 dark (change)
    for i, b in enumerate([10, 10, 10, 200, 200, 40]):
        p = str(tmp_path / f"d{i:05d}.jpg")
        _write_frame(p, b)
        files.append(p)
    kept = dm.select_keyframes(files, fps=2.0, diff_threshold=5.0, max_frames=60)
    ts = [k["ts"] for k in kept]
    assert ts[0] == 0.0  # first frame always kept
    assert 1.5 in ts  # frame 3, the bright flip
    assert 2.5 in ts  # frame 5, the dark flip (also the last frame)
    assert 0.5 not in ts and 1.0 not in ts and 2.0 not in ts  # static frames dropped


def test_select_keyframes_respects_max_frames(tmp_path):
    files = []
    for i in range(12):
        p = str(tmp_path / f"d{i:05d}.jpg")
        _write_frame(p, (i * 37) % 255)  # every frame changes
        files.append(p)
    kept = dm.select_keyframes(files, fps=2.0, diff_threshold=5.0, max_frames=5)
    assert len(kept) == 5
    assert kept[0]["ts"] == 0.0 and kept[-1]["ts"] == 11 / 2.0  # ends survive the cap


def test_select_keyframes_cap_one_and_invalid_inputs(tmp_path):
    files = []
    for i, brightness in enumerate([10, 200, 40]):
        p = str(tmp_path / f"d{i:05d}.jpg")
        _write_frame(p, brightness)
        files.append(p)

    kept = dm.select_keyframes(files, fps=2.0, diff_threshold=5.0, max_frames=1)
    assert len(kept) == 1
    assert kept[0]["ts"] == 0.0

    with pytest.raises(ValueError, match="max_frames"):
        dm.select_keyframes(files, fps=2.0, diff_threshold=5.0, max_frames=0)
    with pytest.raises(ValueError, match="fps"):
        dm.select_keyframes(files, fps=0.0, diff_threshold=5.0, max_frames=3)


def test_clean_generated_output_preserves_unrelated_files(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "0000_00h00m00s.jpg").write_bytes(b"old")
    (frames / "cover.jpg").write_bytes(b"keep")
    (frames / "notes.txt").write_text("keep")
    for name in ("manifest.json", "report.html", "digest.md"):
        (tmp_path / name).write_text("stale")
    (tmp_path / "user-notes.md").write_text("keep")

    dm.clean_generated_output(str(tmp_path))

    assert not (frames / "0000_00h00m00s.jpg").exists()
    assert (frames / "cover.jpg").exists()
    assert (frames / "notes.txt").exists()
    assert not (tmp_path / "manifest.json").exists()
    assert not (tmp_path / "report.html").exists()
    assert (tmp_path / "user-notes.md").exists()


def test_select_keyframes_empty():
    assert dm.select_keyframes([], 2.0, 1.5, 60) == []


def test_scaled_dimensions_match_even_ffmpeg_output():
    assert dm._scaled_dimensions(1920, 1080, 640) == (640, 360)
    assert dm._scaled_dimensions(640, 359, 320) == (320, 180)


def test_read_raw_frame_handles_short_reads():
    class ShortReader:
        def __init__(self):
            self.parts = [b"ab", b"c", b"def"]

        def read(self, _size):
            return self.parts.pop(0) if self.parts else b""

    assert dm._read_raw_frame(ShortReader(), 6) == b"abcdef"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_stream_keyframes_encodes_only_changed_states(tmp_path):
    video = tmp_path / "states.mp4"
    result = subprocess.run([
        "ffmpeg", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=64x32:r=2:d=1",
        "-f", "lavfi", "-i", "color=c=white:s=64x32:r=2:d=1",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p",
        "-c:v", "mpeg4", "-y", str(video),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    kept = dm.stream_keyframes(
        str(video), fps=2.0, width=32, source_width=64, source_height=32,
        diff_threshold=20.0, max_frames=10, tmpdir=str(tmp_path),
    )
    assert [item["ts"] for item in kept] == [0.0, 1.0, 1.5]
    assert Image.open(kept[0]["src"]).size == (32, 16)
    assert Image.open(kept[1]["src"]).size == (32, 16)

    first_only = dm.stream_keyframes(
        str(video), fps=2.0, width=32, source_width=64, source_height=32,
        diff_threshold=20.0, max_frames=1, tmpdir=str(tmp_path),
    )
    assert [item["ts"] for item in first_only] == [0.0]


# --- click detection -----------------------------------------------------------

def _frame(i, ts, score, pointer):
    return {"index": i, "file": f"frames/{i:04d}.jpg", "timestamp_seconds": ts,
            "timestamp_hms": dm.hms(ts), "change_score": score, "pointer": pointer}


def test_detect_clicks_includes_last_frame(tmp_path):
    ptr = {"nx": 0.5, "ny": 0.5, "region": "center", "changed_fraction": 0.001}
    frames = [
        _frame(0, 0.0, 0.0, None),        # first frame: no pointer, skipped
        _frame(1, 1.0, 10.0, ptr),        # big change: not a click
        _frame(2, 2.0, 1.2, ptr),         # click
        _frame(3, 3.0, 0.9, ptr),         # click, last frame (used to be skipped)
    ]
    path = dm.detect_clicks(str(tmp_path), frames)
    assert path is not None
    with open(path) as f:
        clicks = json.load(f)
    assert isinstance(clicks, list)
    assert [c["frame"] for c in clicks] == [2, 3]
    assert clicks[0]["region"] == "center"


def test_detect_clicks_none(tmp_path):
    frames = [_frame(0, 0.0, 50.0, None)]
    assert dm.detect_clicks(str(tmp_path), frames) is None
    assert not (tmp_path / "clicks.json").exists()


# --- confidence flagging ---------------------------------------------------------

def test_is_low_confidence():
    assert not dm.is_low_confidence({"avg_logprob": -0.3, "no_speech_prob": 0.1})
    assert dm.is_low_confidence({"avg_logprob": -1.5, "no_speech_prob": 0.1})
    assert dm.is_low_confidence({"avg_logprob": -0.3, "no_speech_prob": 0.9})
    assert not dm.is_low_confidence({})
    # 0.0 is a valid (confident) score, not "missing"
    assert not dm.is_low_confidence({"avg_logprob": 0.0, "no_speech_prob": 0.0})


def test_write_transcript_flags_low_confidence(tmp_path):
    transcript = {"language": "en", "segments": [
        {"start": 0.0, "end": 2.0, "text": "clear line", "avg_logprob": -0.2, "no_speech_prob": 0.05},
        {"start": 2.0, "end": 4.0, "text": "mumbled line", "avg_logprob": -1.8, "no_speech_prob": 0.1},
    ]}
    meta = {"duration_hms": "00:00:04"}
    paths = dm.write_transcript(str(tmp_path), transcript, meta)
    md = open(paths["transcript_md"]).read()
    assert "clear line\n" in md
    assert "mumbled line ⚠️ low-confidence" in md
    srt = open(paths["transcript_srt"]).read()
    assert "00:00:00,000 --> 00:00:02,000" in srt


# --- digest interleaving -----------------------------------------------------------

def test_digest_interleaved_matches_and_orphans(tmp_path):
    transcript = {"segments": [
        {"start": 0.0, "end": 3.0, "text": "I click the button"},
    ]}
    ptr = {"nx": 0.8, "ny": 0.2, "region": "top-right", "changed_fraction": 0.01}
    frames = [
        _frame(0, 1.0, 5.0, ptr),   # inside the segment
        _frame(1, 10.0, 5.0, None),  # after the last segment -> unmatched
    ]
    path = dm.digest_interleaved(str(tmp_path), transcript, frames, {"duration_hms": "00:00:12"})
    md = open(path).read()
    assert "I click the button" in md
    assert "frames/0000.jpg" in md
    assert "top-right" in md
    assert "## Unmatched Frames" in md
    assert "frames/0001.jpg" in md
