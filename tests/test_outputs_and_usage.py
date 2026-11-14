"""Output contract, --check verdict, the how-to-use guide, and installer.

The end-to-end test builds a synthetic clip with ffmpeg and runs the real
script. It is skipped when ffmpeg is not on PATH.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "scripts", "qa_digest.py")

_SPEC = importlib.util.spec_from_file_location("qa_digest", SCRIPT)
dm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dm)


def _run(args, home, **kw):
    env = dict(os.environ, HOME=str(home))
    return subprocess.run([sys.executable, SCRIPT, *args], env=env,
                          stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=120, **kw)


# --- --check verdict ---------------------------------------------------------

def test_verdict_ready():
    assert dm.doctor_verdict(True, []) == "ready"


def test_verdict_optional_missing_is_still_ready():
    v = dm.doctor_verdict(True, ["no transcript — pip install faster-whisper"])
    assert v.startswith("ready (limited:")
    assert "faster-whisper" in v


def test_verdict_required_missing():
    assert dm.doctor_verdict(False, []).startswith("NOT ready")


# --- --json summary ----------------------------------------------------------

def test_json_summary_lists_only_existing_files(tmp_path):
    (tmp_path / "digest.md").write_text("x")
    (tmp_path / "frames_index.md").write_text("x")
    (tmp_path / "transcript.md").write_text("x")
    manifest = {
        "meta": {"duration": 12.5},
        "frames": [{"index": 0}, {"index": 1}],
        "scene_source": "diff",
        "frames_index_md": str(tmp_path / "frames_index.md"),
        "transcript": {"segment_count": 3,
                       "transcript_md": str(tmp_path / "transcript.md"),
                       "transcript_json": str(tmp_path / "missing.json")},
    }
    outputs = {"manifest": "m", "digest": str(tmp_path / "digest.md"),
               "html": str(tmp_path / "report.html"), "clicks": None}
    s = dm.build_json_summary(str(tmp_path), manifest, outputs)
    assert s["frame_count"] == 2
    assert s["transcript_segments"] == 3
    assert s["duration_seconds"] == 12.5
    assert set(s["outputs"]) == {"digest", "frames_index", "transcript_md"}


# --- usage guide -------------------------------------------------------------

def test_quickstart_covers_the_basics():
    for needle in ("digest this video", ".digest/", "report.html",
                   "file these as issues", "--usage", "~/.qa-digest.json"):
        assert needle in dm.QUICKSTART


def test_usage_flag_prints_guide(tmp_path):
    r = _run(["--usage"], tmp_path)
    assert r.returncode == 0
    assert "How to use qa-digest" in r.stdout
    assert not (tmp_path / ".qa-digest.json").exists()  # no side effects


def test_no_video_mentions_usage(tmp_path):
    r = _run([], tmp_path)
    assert r.returncode != 0
    assert "--usage" in r.stderr


# --- installer ----------------------------------------------------------------

def test_installer_copies_runtime_files_only(tmp_path):
    dest = tmp_path / "skills" / "qa-digest"
    env = dict(os.environ, HOME=str(tmp_path))
    r = subprocess.run(["bash", os.path.join(ROOT, "install.sh"), "--dest", str(dest)],
                       env=env, capture_output=True, text=True, timeout=120)
    # exit code depends on the machine's deps; the copy must happen either way
    assert (dest / "SKILL.md").is_file()
    for name in ("qa_digest.py", "evidence_queue.py", "file_issues.py"):
        assert (dest / "scripts" / name).is_file()
    assert not (dest / ".git").exists()
    assert not (dest / "tests").exists()
    assert "How to use qa-digest" in r.stdout


# --- end to end (real ffmpeg) -------------------------------------------------

@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="ffmpeg not installed")
def test_end_to_end_silent_clip(tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=duration=6:size=640x360:rate=30",
                    "-pix_fmt", "yuv420p", str(clip)], check=True, timeout=60)
    out = tmp_path / "clip.digest"
    r = _run([str(clip), "--out", str(out), "--no-transcribe", "--json"], tmp_path)
    assert r.returncode == 0, r.stderr
    summary = json.loads(r.stdout)

    # Every documented output exists, even with no transcript.
    for name in ("manifest.json", "frames_index.md", "digest.md",
                 "report.html", "clicks.json"):
        assert (out / name).is_file(), name
    assert isinstance(json.loads((out / "clicks.json").read_text()), list)
    assert "Unmatched Frames" in (out / "digest.md").read_text()

    # --json lists what exists and counts match the manifest.
    manifest = json.loads((out / "manifest.json").read_text())
    assert summary["frame_count"] == len(manifest["frames"]) > 0
    for key in ("digest", "html", "clicks", "frames_index"):
        assert os.path.isfile(summary["outputs"][key]), key
    for fr in manifest["frames"]:
        assert (out / fr["file"]).is_file()
