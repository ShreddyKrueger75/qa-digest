#!/usr/bin/env python3
"""Select a small, deterministic evidence queue from a qa-digest run.

This is intentionally standard-library-only so an agent can use it before the
optional video dependencies are installed. It ranks frames for visual review;
it does not inspect image contents or make QA claims.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("expected a positive integer")
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def timestamp(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("expected a timestamp in seconds")
    if number < 0:
        raise argparse.ArgumentTypeError("timestamp must not be negative")
    return number


def read_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except OSError as exc:
        raise SystemExit("ERROR: could not read %s: %s" % (path, exc))
    except ValueError as exc:
        raise SystemExit("ERROR: %s is not valid JSON: %s" % (path, exc))


def load_run(digest_dir):
    digest_dir = os.path.abspath(os.path.expanduser(digest_dir))
    manifest_path = os.path.join(digest_dir, "manifest.json")
    manifest = read_json(manifest_path)
    frames = manifest.get("frames") or []
    if not isinstance(frames, list):
        raise SystemExit("ERROR: manifest frames must be a list")

    clicks_path = os.path.join(digest_dir, "clicks.json")
    clicks = read_json(clicks_path) if os.path.isfile(clicks_path) else []
    if not isinstance(clicks, list):
        clicks = []
    return digest_dir, frames, clicks


def _frame_key(frame):
    return int(frame.get("index", -1))


def _nearest(frames, seconds):
    """Return the closest frame at or before and at or after a timestamp."""
    before = [f for f in frames if f.get("timestamp_seconds", 0) <= seconds]
    after = [f for f in frames if f.get("timestamp_seconds", 0) >= seconds]
    result = []
    if before:
        result.append(max(before, key=lambda f: f.get("timestamp_seconds", 0)))
    if after:
        result.append(min(after, key=lambda f: f.get("timestamp_seconds", 0)))
    return result


def select_frames(frames, clicks=None, limit=8, focus_times=None, excluded=None):
    """Return ranked, deduplicated frame records for an agent review batch."""
    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    frames = list(frames or [])
    if not frames:
        return []

    excluded = {int(index) for index in (excluded or [])}
    by_index = {_frame_key(frame): frame for frame in frames}
    selected = {}

    def add(frame, reason):
        if not frame:
            return
        index = _frame_key(frame)
        if index in excluded or index not in by_index:
            return
        record = selected.setdefault(index, {"frame": frame, "reasons": []})
        if reason not in record["reasons"]:
            record["reasons"].append(reason)

    focused = bool(focus_times)
    if focused:
        for seconds in focus_times:
            nearby = sorted(
                frames,
                key=lambda frame: abs(frame.get("timestamp_seconds", 0) - seconds),
            )
            for frame in nearby:
                add(frame, "near %.3fs" % seconds)
                if len(selected) >= limit:
                    break

    if not focused:
        add(frames[0], "first frame")
        add(frames[-1], "final frame")

    if len(selected) < limit:
        click_indices = {int(click.get("frame")) for click in (clicks or [])
                         if isinstance(click, dict) and click.get("frame") is not None}
        for frame in frames:
            if _frame_key(frame) in click_indices:
                add(frame, "click candidate")

    if len(selected) < limit:
        scored = sorted(
            frames,
            key=lambda frame: float(frame.get("change_score") or 0),
            reverse=True,
        )
        for frame in scored:
            add(frame, "high change")

    ranked = list(selected.values())
    chosen = ranked[:limit]
    chosen.sort(key=lambda item: item["frame"].get("timestamp_seconds", 0))

    output = []
    for item in chosen:
        frame = item["frame"]
        output.append({
            "index": _frame_key(frame),
            "file": frame.get("file"),
            "timestamp_seconds": frame.get("timestamp_seconds"),
            "timestamp_hms": frame.get("timestamp_hms"),
            "change_score": frame.get("change_score"),
            "pointer": frame.get("pointer"),
            "reasons": item["reasons"],
        })
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Select a compact visual evidence queue from a qa-digest run."
    )
    parser.add_argument("--digest", required=True, help="Path to a .digest directory")
    parser.add_argument("--limit", type=positive_int, default=8,
                        help="Maximum frames to select (default 8)")
    parser.add_argument("--at", dest="focus_times", action="append", type=timestamp,
                        default=[], metavar="SECONDS",
                        help="Focus around a timestamp; repeat for multiple moments")
    parser.add_argument("--exclude-index", action="append", type=int, default=[],
                        help="Frame index already viewed; repeat as needed")
    args = parser.parse_args(argv)

    digest_dir, frames, clicks = load_run(args.digest)
    selected = select_frames(
        frames,
        clicks=clicks,
        limit=args.limit,
        focus_times=args.focus_times,
        excluded=args.exclude_index,
    )
    payload = {
        "digest_dir": digest_dir,
        "limit": args.limit,
        "selected": selected,
        "available_frames": len(frames),
        "remaining_after_selection": max(0, len(frames) - len(selected) - len(args.exclude_index)),
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
