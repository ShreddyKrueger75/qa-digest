# qa-digest

A [Claude Code](https://claude.com/claude-code) **skill** that lets Claude
**watch and digest a local video file** — by transcribing its audio and
exporting keyframes, so the model can actually read what happens. I developed
it as part of [The Ad Bench](https://theadbench.ai), my creative scoring
platform.

Claude can't decode video or hear audio. This skill splits the work:

- `scripts/qa_digest.py` transcribes the spoken dialogue with timestamps
  (faster-whisper) and exports keyframes.
- **Optimized for QA / bug-report recordings.** By default it keeps only the
  frames that *changed* (diff-based selection — no more 20 duplicate frames of a
  static screen) and localizes the **pointer** on each: where the screen changed
  vs the previous frame ≈ where the cursor / action was. A failure often shows as
  the *absence* of change. `--no-dedup` restores plain scene/interval sampling.
- Claude then reads `transcript.md` and views the frames to write a structured
  digest.

**The audio is the point.** A narrated screen recording — someone talking
through a bug or reviewing a UI — is nearly useless as frames alone; the actual
report is in the voice. Transcribe first, read the transcript, *then* look at
the frames.

## Install as a skill

Clone straight into your Claude Code skills directory:

```bash
git clone --depth 1 https://github.com/ShreddyKrueger75/qa-digest \
  ~/.claude/skills/qa-digest
rm -rf ~/.claude/skills/qa-digest/.git
# or, per-project: clone into <project>/.claude/skills/qa-digest
```

Already have a checkout? Copy it without the repo metadata — a plain `cp -R`
drags `.git` along, which is most of the installed size and none of the use:

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

`--check` prints each dependency's status and which Python has it — worth the
five seconds, because a second interpreter missing `faster-whisper` fails
silently (you get frames but no transcript).

Quality guardrails are built in: if the literal file path misses because of
macOS's narrow no-break space before "PM", the script resolves it and moves on;
if more than 30% of transcript segments come back low-confidence, it re-runs
transcription one model size up and keeps the better result; and concurrent
digests queue behind a lock instead of hanging.

Then tell Claude Code: **"digest this video: /path/to/clip.mov"** — or just hand
it a screen recording and ask what happens.

## Run the script directly

```bash
python3 scripts/qa_digest.py "/path/to/clip.mov" \
  --out "/path/to/clip.digest" --model small --max-frames 25
```

- `--model tiny|base|small|medium|large-v3` — accuracy vs. speed. `tiny` only safe
  for short (<~2 min) continuously-narrated clips; fabricates text otherwise.
  `small` is the safe default; bigger models slower but more accurate.
- `--max-frames N` — keyframe cap (default 60).
- `--no-frames` — transcript only, fast.
- `--no-transcribe` — frames only (silent footage).
- `--no-report` — skip the HTML report.

See `SKILL.md` for the full agent workflow, all flags, and the gotchas
(macOS narrow-space filenames, `tiny` mishearings, the scenedetect/OpenCV
fallback).

## What you get

Every digest includes:

**Primary outputs** (the QA workflow):
- **digest.md** — transcript + keyframes woven by time. Read top-to-bottom; pointer + region marked inline on each frame. Includes "Unmatched frames" section for silent gaps and frames outside transcript segments.
- **report.html** — self-contained HTML review (email-friendly, no external deps). Transcript on left, keyframes on right, pointer overlay. Shareable as-is. Also shows unmatched frames.
- **transcript.md** — timestamped narration. Segments marked with `⚠️ low-confidence` may be misheard (low Whisper confidence); re-check those with a larger `--model` before quoting.
- **clicks.json** — detected click/action moments (small, localized changes). JSON list with frame index, estimated timestamp, and pointer region for each suspected interaction.

**Reference outputs**:
- **frames_index.md** — pointer + change-score table for every kept frame. Glanceable; tells you where to look before opening an image.
- **transcript files** — `.md`, `.srt`, `.json` for grepping, remixing, or feeding to other tools.
- **manifest.json** — metadata, frame list with pointers, and transcript paths.

## Modes — diff-threshold presets

`--mode insano|strict|standard|lenient` — tune how comprehensive frame capture is.

- **`insano`** — every single change. 100+ frames per 2-min clip. For forensic analysis of every interaction, cursor twitch, pixel shift.
- **`strict`** — capture everything. 20–40 frames per 2-min clip. Strict adherence to what you said/did; nothing missed. For detailed walkthroughs and parameter-level specs.
- **`standard`** (default) — balanced. 10–20 frames per 2-min clip. Catches blocks placed, menus opened, dialogs appeared.
- **`lenient`** — landmark moments only. 5–10 frames per 2-min clip. For high-level demos or quick reviews where you only need major state shifts.

All modes keep the pointer and change-score columns, digest.md, and report.html. Only the number of kept frames changes.

## File the bugs as GitHub issues

Run it from inside the project you're QA'ing. Claude reads the digest, lists
the bugs it found as one-line summaries, you pick which ones are real, and it
files them:

```bash
python3 scripts/file_issues.py --digest "/path/to/CLIP.digest" --issues bugs.json --dry-run
python3 scripts/file_issues.py --digest "/path/to/CLIP.digest" --issues bugs.json
```

The repo comes from the current directory's git remote. Override with
`--repo owner/name`.

Keyframes referenced by a bug get pushed to an orphan `qa-assets` branch and
linked as raw URLs in the issue body. GitHub's REST API can't attach images to
an issue the way the web UI can, so this is the workaround. The branch shares no
history with your code.

On a private repo those raw URLs need auth, so the images only render for people
signed in with access. The script detects that and says so in the issue.

Needs the `gh` CLI authenticated with `repo` scope. No token is stored anywhere.

## Walk-away mode: watch a folder

`watch/` installs a launchd agent that digests anything dropped into a folder.
Point it at a synced folder and you can hand it a recording from an iPad and get
the report back without touching the Mac.

```bash
cd watch && ./install.sh
```

One caveat worth knowing before you try it: macOS blocks background agents from
reading iCloud Drive, Google Drive, and anything else under
`~/Library/CloudStorage`. Either grant Full Disk Access to the interpreter or
watch a folder outside those locations. See `watch/README.md`.

## Requirements

- `ffmpeg` + `ffprobe` on PATH — required.
- `faster-whisper` — optional (no transcript without it).
- `scenedetect` — optional (interval frame sampling without it).

## Notes

- **Local files only.** DRM-protected streaming (Netflix, etc.) can't be
  captured.
- Silent footage → `transcript.md` has 0 segments (expected); the digest leans
  on the frames.
- Cost and time scale with `--max-frames` and Whisper `--model` size.

## Development

```bash
pip install pytest
python3 -m pytest tests/
```

The tests cover the pure logic (timestamp formatting, frame selection, pointer
math, click detection, markdown emitters) — no ffmpeg or Whisper needed.

## License

MIT — see `LICENSE`.

Built by Bloody Finger Software.
