# qa-digest watch

Drop a screen recording into a synced folder from your iPad; the Mac digests it
and writes the report back into the same folder.

The skill itself can't run on iPad — it needs `ffmpeg`, `faster-whisper`, and a
real filesystem. This closes that gap: the iPad is the drop point and the
viewer, the Mac does the work.

## Install

```bash
cd watch
./install.sh          # writes a default config if you don't have one
$EDITOR ~/.qa-digest-watch.json
./install.sh          # again, to load the launchd agent
```

Uninstall with `./install.sh --uninstall`.

## How it fires

The launchd agent is triggered two ways, because neither alone is reliable:

- **`WatchPaths`** — fires the moment the drop folder changes. Fast.
- **`StartInterval`** (default 300s) — a safety net. iCloud can materialise a
  file without touching the directory's mtime, so a pure `WatchPaths` agent
  will miss clips that arrive from the iPad.

Runs are serialised with a lock file, so overlapping triggers are harmless.

## Sync safety

A clip that's still downloading is a truncated clip. Before digesting anything,
the watcher waits for the file size and mtime to hold steady for
`stable_seconds` (default 20). iCloud placeholder files (`.clip.mov.icloud`)
are handed to `brctl download` and picked up on a later pass.

## Configuration

All settings live in `~/.qa-digest-watch.json`. Nothing is hardcoded —
`qa_digest_watch.py --init` writes every key with its default.

### Where to look

| Key | Default | Notes |
| --- | --- | --- |
| `watch_dir` | iCloud `MovieDigest` folder | The drop point. Any synced folder works — iCloud, Dropbox, Syncthing. |
| `extensions` | `.mov .mp4 .m4v .avi .mkv` | Which files count as video. |
| `recursive` | `false` | Descend into subfolders. `.digest` output dirs are always skipped. |

### What to run

| Key | Default | Notes |
| --- | --- | --- |
| `python_bin` | current interpreter | **Must be the interpreter that has `faster-whisper` installed.** |
| `digest_script` | `../scripts/qa_digest.py` | Absolute path is safest. |
| `digest_timeout_seconds` | `3600` | A long clip on a big model can take a while. |

### Digest options

Mirror the `qa_digest.py` flags: `mode`, `model`, `max_frames`,
`no_report`, `no_transcribe`, `no_frames`, `clean_output`, `language`. Anything not exposed
goes in `extra_args` as a list, e.g. `["--frame-width", "800"]`.


`filename_mode_override` (default `true`) reads the mode out of the filename:
`bug-repro.strict.mov` runs in `strict`. Lets you pick per-recording from the
iPad without editing config.

### Output and cleanup

| Key | Default | Notes |
| --- | --- | --- |
| `output_dir` | `null` | `null` puts `<video>.digest` next to the clip. |
| `clean_output` | `true` | Replaces prior qa-digest artifacts when a clip is reprocessed. |
| `on_success` | `"leave"` | `leave`, `move` (needs `archive_dir`), or `trash`. |
| `reprocess_on_change` | `true` | Re-digest if a clip is replaced with a new take. |

### Notifications

`notify` is a list; combine freely.

- `status_file` — writes `STATUS.md` into the watch folder. Readable from the
  iPad Files app with no push setup. On by default.
- `macos` — a notification on the Mac.
- `email` — mails `report.html` as an attachment. Fill in the `email` block.
  The password comes from the env var named in `smtp_password_env`, never from
  the config file. For Gmail this must be an app password.

## Manual use

```bash
python3 qa_digest_watch.py --dry-run          # what would run
python3 qa_digest_watch.py --once clip.mov    # force one file
python3 qa_digest_watch.py                    # one pass, same as launchd
launchctl kickstart -k gui/$UID/com.bloodyfinger.qadigest.watch
```

## Logs

- `~/Library/Logs/qa-digest-watch.log` — the watcher's own log
- `~/Library/Logs/qa-digest-watch.{out,err}.log` — raw launchd capture

Set `log_level` to `debug` to see the exact command being run.

## Gotchas

- **launchd has no shell environment.** No `~/.zshrc`, no API keys, minimal
  `PATH`. The agent sets `PATH` explicitly for Homebrew's ffmpeg; anything else
  you need goes in the plist's `EnvironmentVariables`.
- **Full Disk Access.** If `watch_dir` is in iCloud Drive, macOS may need to
  grant the agent access on first run. If digests silently never happen, check
  `qa-digest-watch.err.log` for permission errors.
- **`qa_digest.py` prompts on first run.** It asks for a mode when
  `~/.qa-digest.json` is missing — which would hang under launchd. The
  watcher seeds that file and runs the script with stdin closed.
