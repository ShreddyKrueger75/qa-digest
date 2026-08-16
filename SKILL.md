---
name: qa-digest
description: >-
  Watch and digest a local video file — transcribe its narration and export
  keyframes so Claude can actually read what happens. Use whenever someone
  points at a video on disk (.mp4, .mkv, .mov, .webm, .avi) and wants it
  watched, summarized, broken down, analyzed, or when a screen recording
  narrates a bug/feature/review out loud: "digest this movie", "watch this",
  "what's in this footage", "transcribe and break down this clip", or any
  bug-report / UI-review screen recording. Also files the bugs it finds as
  GitHub issues, with keyframes attached, when asked to "file these", "open
  tickets for these", or "turn this into issues". Local files only.
---

# qa-digest

## Introduction

`qa-digest` is a local-only QA tool for narrated screen recordings. It
transcribes the audio, extracts meaningful keyframes, and produces a
timestamped digest so visual bugs can be verified against what was said. It
does not upload recordings or file issues unless explicitly requested.

Claude can't decode video or hear audio. This skill splits the job: a script
does the mechanical part — transcribes the spoken audio with timestamps and
exports one keyframe per scene — and then **Claude reads the transcript and
views the frames** to write the digest.

**The audio is the point.** A narrated screen recording (someone talking through
a bug) is nearly useless as frames alone — the actual report is in the voice.
Transcribe first, read `transcript.md`, *then* look at the frames. Skipping the
transcript and pulling frames with raw `ffmpeg` is the #1 way to miss the whole
message.

## Prerequisites

```bash
brew install ffmpeg
pip install Pillow numpy faster-whisper scenedetect
```

`ffmpeg`/`ffprobe` are required. `faster-whisper` and `scenedetect` are
optional and auto-detected — without the first it skips the transcript; without
the second it samples frames at even time intervals (fine for screen
recordings).

## Setup (first run)

On first run in an interactive terminal, the script prompts for four defaults:

```
(1) Diff-threshold mode — how aggressive frame selection is
(2) Generate HTML report by default?
(3) Output directory
(4) Whisper model
```

Your choices save to `~/.qa-digest.json` — reconfigure anytime by deleting that file.

Non-interactive runs (e.g., Claude running the script) auto-write defaults (mode `insano`, HTML report on) without prompting.

## Run (the one path)

```bash
python3 scripts/qa_digest.py "/path/to/CLIP.mov" \
  --out "/path/to/CLIP.digest" --model small --max-frames 25
```

Then **read `<out>/transcript.md` first**, then view the frames listed in
`<out>/frames_index.md` in batches of up to 8. Anchor every claim to a timestamp.

## Agent loop: minimum evidence, maximum certainty

Use this bounded loop for every recording. The goal is to spend tokens on
evidence that can change the conclusion, not on repeated narration or
near-duplicate frames.

### Pass 0 — prepare once

Run the digest once and skip the HTML report unless the user explicitly asks
for a shareable visual report:

```bash
python3 scripts/qa_digest.py "/path/to/CLIP.mov" \
  --out "/path/to/CLIP.digest" --model small --mode insano \
  --max-frames 60 --sample-fps 2 --no-report --clean-output --json
```

For a quick, non-authoritative triage pass, use `--model base --mode lenient
--max-frames 10 --sample-fps 1`. Do not quote that pass as fact if the
transcript is low-confidence; rerun with `small` before reporting findings.

### Pass 1 — cheap triage

Read only these files first, in this order:

1. `transcript.md` — what was said and where.
2. `manifest.json` — run parameters, frame count, and fallback mode.
3. `clicks.json` — suspected localized actions, when present.

Do not open `report.html`, `digest.md`, `frames_index.md`, or every image yet.
Ask the standard-library queue for a compact first batch:

```bash
python3 scripts/evidence_queue.py --digest "/path/to/CLIP.digest" \
  --limit 8
```

For a focused follow-up, exclude the frame indices already viewed and provide
the unresolved narration timestamps:

```bash
python3 scripts/evidence_queue.py --digest "/path/to/CLIP.digest" \
  --limit 4 --at 15.2 --at 31.7 \
  --exclude-index 0 --exclude-index 4
```

The queue returns relative frame paths, timestamps, change scores, pointer
metadata, and selection reasons. Open `frames_index.md` only if the queue or
the evidence table leaves a selection ambiguous. Build a small evidence table
with one row per possible finding:

```text
claim | narration timestamp | candidate frames | confidence | next question
```

Treat silence gaps and low-confidence transcript segments as candidates for
inspection, not as proof of a bug.

### Pass 2 — first evidence batch

View at most eight unique frames in the first batch:

The queue performs the bookend/click/high-change ranking and deduplication.
Record the returned indices as `seen`; the first batch should answer the broad
question: *what state changed, and did the claimed action produce the expected
state?*

### Pass 3 — targeted escalation

Only open more frames when a specific claim remains unresolved. Add at most
four frames per iteration, chosen around the unresolved timestamp or from the
next-highest change score. After each batch, update the evidence table.

Stop when either:

- every finding has an observed actual state, expected state, timestamp, and
  supporting frame; or
- two consecutive batches add no new state, or 20 total frames have been
  viewed.

If narration is the uncertainty, use the transcript confidence markers and the
script's model escalation. If the screen state is the uncertainty, rerun with
`--mode insano --max-frames 120` only for that recording. Do not rerun the full
pipeline merely to reread evidence already seen.

### Pass 4 — finalize without inventing evidence

For each finding, write a concise title, reproduction steps, expected behavior,
actual behavior, and key timestamps. Mark anything not visible or confidently
spoken as unknown rather than filling the gap with an inference. Present the
numbered issue list and wait for the user to choose before running
`file_issues.py`; use `--dry-run` before any real filing.

Flags that matter:
- `--mode insano|strict|standard|lenient` — frame selection comprehensiveness (default insano).
- `--no-report` — skip HTML report (frames + digest.md only).
- `--check` — dependency doctor: reports ffmpeg/whisper/Pillow/numpy status
  and which interpreter has them, then exits. Run it first on a new machine.
- `--model tiny|base|small|medium|large-v3` — accuracy vs speed (default `small`); choose based on clip length and importance:
  - `tiny` — only for short clips (under ~2 minutes) with continuous narration. On longer or sparsely-narrated recordings it fabricates plausible-sounding text instead of failing.
  - `small` — the safe default for anything longer, and for anything where the narration is the point (bug reports, reviews).
  - Bigger models (`base`, `medium`, `large-v3`) = slower but more accurate. Use `base` for a real film or when a transcript reads like nonsense — re-run with a larger model before acting on it.
  - Segments marked `⚠️ low-confidence` in transcript.md have low Whisper confidence and may be misheard — re-check with a larger model before quoting.
- `--max-frames N` — cap on keyframes (default 60; use 12–20 only for a deliberately capped triage pass).
- `--diff-threshold N` — override mode's threshold (lower = more frames; default 0.2 for insano, 1.5 for standard).
- `--no-dedup` — turn OFF diff selection + pointer; use plain interval/scene
  sampling instead.
- `--no-frames` — transcript only, fast. Use when you only need the narration.
- `--no-transcribe` — frames only (silent footage).
- `--language en` — skip auto-detect.
- `--clean-output` — remove qa-digest artifacts from the output directory before running.

Outputs under `--out`:

```
transcript.md      timestamped, grep-friendly   <- READ FIRST
transcript.srt     subtitles
transcript.json    raw {start,end,text} segments
frames/            NNNN_HHhMMmSSs.jpg keyframes (640px)
frames_index.md    frame -> timestamp + change score + POINTER table
digest.md          transcript + keyframes woven by time
report.html        self-contained HTML review document
clicks.json        suspected click/flash moments (diff mode)
manifest.json      metadata + full frame list (+ pointer) + transcript paths
```

## Filing bugs as GitHub issues

After you've read the digest, you can turn findings into GitHub issues. **Never
file without the user picking first.** The flow is:

1. Read `digest.md` and write up every distinct bug you found.
2. Present them as a **numbered list of one-line summaries** — title plus the
   timestamp it happens at. Nothing else; the user is choosing, not reading.
3. Ask which to file. Accept "1 and 3", "all", "none".
4. Write only the chosen ones to a JSON file and run:

```bash
python3 scripts/file_issues.py --digest "/path/to/CLIP.digest" --issues bugs.json
```

The repo is inferred from the current directory's git remote, so run this from
inside the project being QA'd. Override with `--repo owner/name`.

`bugs.json` is a list of objects:

```json
[
  {
    "title": "Grid connector drops on second placement",
    "body": "**Steps**\n1. ...\n\n**Expected** ...\n\n**Actual** ...",
    "frames": ["frames/0012_00h01m04s.jpg"],
    "labels": ["bug"]
  }
]
```

`frames` are paths relative to the digest dir. They're uploaded to an orphan
`qa-assets` branch (created on first use, sharing no history with your code) and
rewritten as raw URLs in the issue body — the REST API can't attach images to an
issue the way the web UI can.

Flags: `--dry-run` prints what would be filed and uploads nothing — **use it
first**. `--no-images` files text-only. `--assets-branch` renames the asset
branch.

Requires the `gh` CLI authenticated with `repo` scope (`gh auth login`).

**Caveat:** on a **private** repo, `raw.githubusercontent.com` URLs need auth,
so the images only render for signed-in users with access. The script detects
this and adds a note to the issue.

Every issue body should anchor to timestamps and quote the narration. If a
segment was marked `⚠️ low-confidence`, do not quote it as fact in a ticket —
re-run with a larger `--model` first.

## QA mode (default): changed frames only + pointer

This is built for **bug-report / UI-review screen recordings**, so by default it
does two things a plain frame-dump can't:

- **Diff-based selection.** A screen recording is ~90% static. Instead of one
  frame every N seconds (a pile of duplicates), it samples densely, then keeps
  only the frames that *changed* from the last kept one — a block placed, a menu
  opened, a cable drawn. 169 sampled → ~12 meaningful.
- **Streaming implementation.** The dense sample travels through an ffmpeg pipe;
  only selected frames become JPEGs. This keeps the same evidence while avoiding
  a temporary JPEG for every sampled frame, which matters on long or high-resolution
  recordings.
- **Pointer localization.** Each kept frame's `pointer` column is the centroid of
  what changed vs the previous frame ≈ **where the cursor / action was**, as a
  region (`top-right`, `center`, …) + normalized `(x,y)`. `change` is the
  magnitude — a big number is a new screen/dialog; a small one is a local edit.

Read `frames_index.md` and let the pointer + change columns tell you *where to
look* in each frame before you open it. A **failure often shows as the absence of
change** — the user says "wire it across" and the next frames don't change: that
gap IS the bug. Needs Pillow + numpy (auto-detected; falls back to interval if
missing). `--no-dedup` restores plain sampling.

## Enhanced outputs

Every digest now includes:

- **digest.md** — the quick read: transcript segments woven together with
  keyframes that fall within each segment's time window. Pointer/region marked
  inline. One document = one bug report. Trailing "Unmatched frames" section for
  frames that fall outside any transcript segment (silence gaps, after the last spoken line).
- **report.html** — self-contained (no external assets): transcript on the left,
  keyframes on the right, pointer overlay. Shareable, no post-processing needed.
  Also includes the unmatched frames section.
- **clicks.json** — detects small, localized changes (likely click flashes or
  menu appearances). A JSON list of `{frame, ts, score, region, nx, ny}` per
  suspected action. For QA mode only; absent if no candidate clicks found.

All auto-generated and graceful when transcript is absent (frames only).
Claude reads `digest.md` and writes the findings itself — no API key involved.

## Gotchas (learned the hard way)

- **macOS screen-recording filenames contain a narrow no-break space (U+202F)**
  before "PM" — `Screen Recording 2026-07-23 at 9.40.00 PM.mov`. A literal path
  copied from a message will NOT match on the command line. Resolve with a glob,
  or copy to a space-free path first:
  ```bash
  f=$(ls *Recording*9.40*.mov); cp "$f" /tmp/clip.mov
  python3 scripts/qa_digest.py /tmp/clip.mov --out /tmp/clip.digest --model small
  ```
  The script prints this exact hint if it can't find the file.
- **`--model tiny` mishears a word or two** — it heard "tempo" as "VPN" and
  "chorus" fine but garbled a product name once. Cross-check any load-bearing
  term against the matching frame before quoting it as fact.
- **`scenedetect` can import but fail** if its OpenCV backend is missing/broken.
  The script catches that and falls back to interval sampling automatically —
  you'll see `WARN: scene detection failed ... falling back`. Not an error.
- **Transcription is the slow part.** On Apple Silicon `tiny`/`base` run faster
  than real time; `large-v3` is much slower. For a 1–2 min screen recording,
  `tiny --no-frames` returns in seconds.
- **Silent clip → 0 segments.** Expected; lean on the frames.
- **Digests must run sequentially.** Running several concurrently has hung. Sequential throughput is fine: ~40 minutes of video transcribed in about 5 minutes.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ERROR: file not found` on a path that exists | Filename has a U+202F space — glob it (see Gotchas). |
| `WARN: faster-whisper not installed` | `pip install faster-whisper` — transcript was skipped. |
| `WARN: scenedetect not installed` / `scene detection failed` | Harmless; frames sampled at intervals instead. |
| 0 segments on a clip you know has talking | Wrong `--language`, or the audio track is silent/very quiet — try `--model base` and confirm `audio=yes` in the `[probe]` line. |
