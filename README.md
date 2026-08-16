# qa-digest

A [Claude Code](https://claude.com/claude-code) **skill** for reviewing local
video files. It transcribes the audio and exports keyframes so Claude can read
what happened. I developed it as part of [The Ad Bench](https://theadbench.ai),
my creative scoring platform.

Claude cannot decode video or hear audio, so the skill splits the work:

- `scripts/qa_digest.py` transcribes the spoken dialogue with timestamps
  (faster-whisper) and exports keyframes.
- **Optimized for QA and bug-report recordings.** By default it keeps only the
  frames that *changed*. This avoids sending 20 duplicate views of a static
  screen and estimates the **pointer** location from the changed area. A
  failure can show up as the *absence* of change. `--no-dedup` restores plain
  scene or interval sampling.
- Claude then reads `transcript.md` and views the frames to write a structured
  digest.

**Audio matters most for narrated recordings.** Frames alone tell you little
when someone is talking through a bug or reviewing a UI. The report is in the
voice. Transcribe first, read the transcript, *then* look at the frames.

## Install as a skill

Clone straight into your Claude Code skills directory:

```bash
git clone --depth 1 https://github.com/ShreddyKrueger75/qa-digest \
  ~/.claude/skills/qa-digest
rm -rf ~/.claude/skills/qa-digest/.git
# or, per-project: clone into <project>/.claude/skills/qa-digest
```

Already have a checkout? Copy it without the repo metadata. A plain `cp -R`
also copies `.git`, which adds most of the installed size without helping the
installed skill:

```bash
rsync -a --exclude '.git' --exclude '.DS_Store' \
  qa-digest/ ~/.claude/skills/qa-digest/
```

Install the runtime deps once, then verify:

```bash
brew install ffmpeg
pip install Pillow numpy faster-whisper scenedetect
python3 ~/.claude/skills/qa-digest/scripts/qa_digest.py --check
```

`--check` prints each dependency's status and the Python interpreter that has it.
Run it when more than one interpreter is installed. If the selected interpreter
does not have `faster-whisper`, the script continues with frames but no
transcript.

The script handles several common failure cases. If macOS uses a narrow no-break
space before "PM" and the literal path misses, it resolves the path. If more
than 30% of transcript segments have low confidence, it reruns transcription
with the next larger model and keeps the better result. Concurrent digests wait
behind a lock instead of hanging.

For efficient agent runs, use `--mode insano --no-report --json` and follow the
bounded evidence loop in `SKILL.md`: inspect the transcript and manifest first,
then use `scripts/evidence_queue.py` to open only frames that can change the
conclusion.

Diff sampling is streaming. ffmpeg sends the dense sample through a pipe, and
qa-digest encodes only the keyframes that survive selection. It does not write
intermediate sampled JPEGs to disk, which avoids the old write, read, and copy
cycle on longer or higher-resolution clips.

Then tell Claude Code: **"digest this video: /path/to/clip.mov"**. You can also
hand it a screen recording and ask what happens.

## Run the script directly

```bash
python3 scripts/qa_digest.py "/path/to/clip.mov" \
  --out "/path/to/clip.digest" --model small --max-frames 25
```

- `--model tiny|base|small|medium|large-v3`: accuracy versus speed. `tiny` is
  safe only for short (<~2 min) continuously narrated clips and can fabricate
  text otherwise. `small` is the safe default. Bigger models are slower but
  more accurate.
- `--max-frames N`: keyframe cap (default 60).
- `--no-frames`: transcript only, fast.
- `--no-transcribe`: frames only (silent footage).
- `--no-report`: skip the HTML report.
- `--clean-output`: clear qa-digest artifacts before rerunning into an existing output directory.

See `SKILL.md` for the full agent workflow, all flags, and details about
macOS narrow-space filenames, `tiny` mishearings, and the scenedetect/OpenCV
fallback.

## What you get

Every digest includes:

**Primary outputs** for the QA workflow:
- **digest.md**: transcript and keyframes woven together by time. Read it from top to bottom. Each frame includes pointer and region information. It also has an "Unmatched frames" section for silent gaps and frames outside transcript segments.
- **report.html**: self-contained HTML review with no external dependencies. The transcript is on the left, keyframes are on the right, and the pointer is overlaid. It also shows unmatched frames.
- **transcript.md**: timestamped narration. Segments marked with `⚠️ low-confidence` may be misheard. Re-check them with a larger `--model` before quoting them.
- **clicks.json**: detected click or action moments based on small, localized changes. The JSON list includes a frame index, estimated timestamp, and pointer region for each suspected interaction.

**Reference outputs**:
- **frames_index.md**: pointer and change-score table for every kept frame. Use it to decide where to look before opening an image.
- **transcript files**: `.md`, `.srt`, and `.json` files for grepping, remixing, or feeding to other tools.
- **manifest.json**: metadata, the frame list with pointers, and transcript paths.

## Modes: diff-threshold presets

`--mode insano|strict|standard|lenient`: controls how comprehensive frame capture is.

- **`insano`**: every change. It produces 100+ frames per 2-min clip for forensic analysis of interactions, cursor movement, and pixel shifts.
- **`strict`**: 20-40 frames per 2-min clip for detailed walkthroughs and parameter-level specs.
- **`standard`** (default): 10-20 frames per 2-min clip. It catches blocks being placed, menus opening, and dialogs appearing.
- **`lenient`**: 5-10 frames per 2-min clip for high-level demos or reviews that need only the major state changes.

All modes keep the pointer and change-score columns, `digest.md`, and
`report.html`. Only the number of kept frames changes.

## File the bugs as GitHub issues

Run this from the project you are QA'ing. Claude reads the digest and lists
the bugs it finds as one-line summaries. You choose which ones are real, then
it files them:

```bash
python3 scripts/file_issues.py --digest "/path/to/CLIP.digest" --issues bugs.json --dry-run
python3 scripts/file_issues.py --digest "/path/to/CLIP.digest" --issues bugs.json
```

The repository comes from the current directory's git remote. Override it with
`--repo owner/name`.

Keyframes referenced by a bug are pushed to an orphan `qa-assets` branch and
linked as raw URLs in the issue body. GitHub's REST API cannot attach images to
an issue the way the web UI can, so the script uses this workaround. The branch
shares no history with your code.

On a private repository, those raw URLs need authentication. The images render
only for people who have signed-in access. The script detects this and says so
in the issue.

The `gh` CLI must be authenticated with `repo` scope. The script does not store
a token.

## Folder watch mode

`watch/` installs a launchd agent that digests anything dropped into a folder.
Point it at a synced folder to process an iPad recording without touching the
Mac.

```bash
cd watch && ./install.sh
```

macOS blocks background agents from reading iCloud Drive, Google Drive, and
anything else under `~/Library/CloudStorage`. Grant Full Disk Access to the
interpreter, or watch a folder outside those locations. See `watch/README.md`.

## Requirements

- `ffmpeg` + `ffprobe` on PATH: required.
- `faster-whisper`: optional. Without it, there is no transcript.
- `scenedetect`: optional. Without it, the script uses interval frame sampling.

## Notes

- **Local files only.** DRM-protected streaming (Netflix, etc.) cannot be
  captured.
- For silent footage, `transcript.md` has 0 segments, as expected. The digest
  relies on the frames.
- Cost and processing time scale with `--max-frames` and Whisper `--model` size.

## Development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install pytest Pillow numpy
bash scripts/ci.sh
```

The local CI runner checks Python compilation and the unit suite. The tests
cover the pure logic: timestamp formatting, frame selection, pointer math,
click detection, and markdown emitters. They do not need a video download or a
Whisper model.

## License

MIT. See `LICENSE`.

Built by Bloody Finger Software.
