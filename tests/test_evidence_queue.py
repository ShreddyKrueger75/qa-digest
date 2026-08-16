import importlib.util
import json
import os

import pytest


_SPEC = importlib.util.spec_from_file_location(
    "evidence_queue",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "evidence_queue.py"),
)
eq = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(eq)


def frame(index, ts, score):
    return {
        "index": index,
        "file": "frames/%04d.jpg" % index,
        "timestamp_seconds": ts,
        "timestamp_hms": "00:00:%02d" % ts,
        "change_score": score,
        "pointer": None,
    }


def test_initial_queue_prioritizes_bookends_clicks_and_change():
    frames = [frame(i, float(i), float(i)) for i in range(10)]
    selected = eq.select_frames(
        frames,
        clicks=[{"frame": 4}],
        limit=4,
    )
    indices = {item["index"] for item in selected}
    assert {0, 4, 9}.issubset(indices)
    assert len(selected) == 4
    assert "click candidate" in next(
        item["reasons"] for item in selected if item["index"] == 4
    )


def test_focused_queue_uses_neighbors_and_excludes_seen_frames():
    frames = [frame(i, float(i), 1.0) for i in range(10)]
    selected = eq.select_frames(frames, limit=3, focus_times=[5.2], excluded=[5])
    indices = [item["index"] for item in selected]
    assert 5 not in indices
    assert 6 in indices
    assert set(indices).issubset({4, 6, 7})
    assert any("near 5.200s" in item["reasons"] for item in selected)


def test_empty_and_invalid_limits():
    assert eq.select_frames([], limit=2) == []
    with pytest.raises(ValueError, match="limit"):
        eq.select_frames([frame(0, 0.0, 1.0)], limit=0)


def test_load_run_reads_optional_clicks(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"frames": [frame(0, 0.0, 1.0)]})
    )
    (tmp_path / "clicks.json").write_text(json.dumps([{"frame": 0}]))
    digest_dir, frames, clicks = eq.load_run(str(tmp_path))
    assert digest_dir == str(tmp_path)
    assert len(frames) == 1
    assert clicks == [{"frame": 0}]
