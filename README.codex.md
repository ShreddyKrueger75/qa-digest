# qa-digest for Codex

`qa-digest` is a local Codex skill for reviewing screen recordings and other
video files. It turns a recording into a timestamped transcript, a compact set
of keyframes, and machine-readable metadata. Codex can then use that evidence
to summarize the recording or identify possible bugs.

The skill works with local files. It does not fetch or decode remote video.

## Install

Install the skill into the Codex skills directory:

```bash
git clone --depth 1 https://github.com/ShreddyKrueger75/qa-digest \
  ~/.codex/skills/qa-digest
rm -rf ~/.codex/skills/qa-digest/.git
```

If you already have a checkout, copy the skill and its helper scripts:

```bash
mkdir -p ~/.codex/skills/qa-digest/scripts
cp SKILL.md ~/.codex/skills/qa-digest/SKILL.md
cp scripts/qa_digest.py scripts/evidence_queue.py scripts/file_issues.py \
  ~/.codex/skills/qa-digest/scripts/
```

Install the runtime dependencies and check them:

```bash
brew install ffmpeg
pip install Pillow numpy faster-whisper scenedetect
python3 ~/.codex/skills/qa-digest/scripts/qa_digest.py --check
```

`ffmpeg` and `ffprobe` are required. `faster-whisper` is optional and provides
transcription. `scenedetect` is optional and provides the fallback scene
sampling mode.

## Use it

In a new Codex turn, invoke the skill and provide a local video path:

```text
$qa-digest
Digest /path/to/recording.mov
```

The skill supports `.mp4`, `.mkv`, `.mov`, `.webm`, and `.avi` files. It asks
for first-run defaults in an interactive terminal. Non-interactive runs use
the saved configuration or the standard defaults.

For direct use from the checkout:

```bash
python3 scripts/qa_digest.py "/path/to/recording.mov" \
  --out "/path/to/recording.digest" --model small --max-frames 25
```

For a fast agent run, skip the HTML report and cap the evidence:

```bash
python3 scripts/qa_digest.py "/path/to/recording.mov" \
  --out "/path/to/recording.digest" --model small --mode standard \
  --max-frames 20 --sample-fps 2 --no-report --clean-output --json
```

Use `--no-transcribe` for silent footage. Use `--no-frames` when you only need
the narration. Use `--model base` or a larger model when the transcript has
low-confidence segments or the narration is difficult to understand.

## Low-token evidence loop

Read evidence in this order:

1. `transcript.md`, for what was said and when.
2. `manifest.json`, for run parameters, frame count, and fallback mode.
3. `clicks.json`, when present, for suspected interaction moments.

Then ask the standard-library queue for a small first batch:

```bash
python3 scripts/evidence_queue.py --digest "/path/to/recording.digest" \
  --limit 8
```

For a follow-up, focus on unresolved timestamps and exclude frames already
viewed:

```bash
python3 scripts/evidence_queue.py --digest "/path/to/recording.digest" \
  --limit 4 --at 15.2 --at 31.7 \
  --exclude-index 0 --exclude-index 4
```

Open no more than eight frames in the first batch. Add up to four frames per
follow-up. Stop when every finding has supporting evidence, or when two batches
add no new state, or when 20 total frames have been viewed.

Do not treat silence gaps or low-confidence transcript segments as proof of a
bug. If the screen state is still unclear, rerun that recording with a higher
frame cap or a stricter mode instead of rerunning every recording.

## Output

Each digest can include:

- `transcript.md`, `transcript.srt`, and `transcript.json`
- `frames/` with selected JPEG keyframes
- `frames_index.md` with timestamps, change scores, and pointer data
- `digest.md` with the transcript and frames in time order
- `report.html` when report generation is enabled
- `clicks.json` for suspected click or action moments
- `manifest.json` with metadata and the complete frame list

Diff sampling streams frames through ffmpeg and writes JPEGs only for selected
keyframes. This avoids creating a temporary JPEG for every sampled frame.

## Filing issues

When asked to file bugs, review the numbered findings first and use dry-run
mode before creating anything:

```bash
python3 scripts/file_issues.py \
  --digest "/path/to/recording.digest" --issues bugs.json --dry-run
```

The `gh` CLI must be authenticated with `repo` scope. The script does not store
a token.

## Development

From the repository checkout:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install pytest Pillow numpy
bash scripts/ci.sh
```

The local runner compiles the Python scripts and runs the test suite. GitHub
Actions runs the same runner on Ubuntu and macOS.

See [SKILL.md](SKILL.md) for the complete workflow and all command-line flags.
