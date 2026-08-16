#!/usr/bin/env bash
# Install (or reinstall) the movie-digest folder watcher as a launchd agent.
#
#   ./install.sh                 # install using ~/.movie-digest-watch.json
#   ./install.sh --config PATH   # install using a specific config
#   ./install.sh --uninstall     # unload and remove the agent
#
# Idempotent: safe to re-run after editing the config.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.bloodyfinger.moviedigest.watch"
PLIST_DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
TEMPLATE="$HERE/$LABEL.plist.template"
WATCHER="$HERE/digest_watch.py"
CONFIG="$HOME/.movie-digest-watch.json"
LOG_DIR="$HOME/Library/Logs"
INTERVAL=300

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)    CONFIG="$2"; shift 2 ;;
    --interval)  INTERVAL="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ -n "${UNINSTALL:-}" ]]; then
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
  rm -f "$PLIST_DEST"
  echo "Uninstalled $LABEL"
  exit 0
fi

# --- preflight -------------------------------------------------------------

if [[ ! -f "$CONFIG" ]]; then
  echo "No config at $CONFIG"
  echo "Creating one now..."
  /usr/bin/python3 "$WATCHER" --init --config "$CONFIG"
  echo
  echo "Edit $CONFIG (at minimum set watch_dir), then re-run this script."
  exit 1
fi

PYTHON_BIN=$(/usr/bin/python3 -c "
import json,sys
cfg=json.load(open('$CONFIG'))
print(cfg.get('python_bin') or sys.executable)
")

WATCH_DIR=$(/usr/bin/python3 -c "
import json,os
cfg=json.load(open('$CONFIG'))
d=cfg.get('watch_dir')
print(os.path.abspath(os.path.expanduser(d)) if d else '')
")

if [[ -z "$WATCH_DIR" ]]; then
  echo "watch_dir is not set in $CONFIG" >&2
  exit 1
fi

mkdir -p "$WATCH_DIR" "$LOG_DIR"

echo "Checking dependencies..."
command -v ffmpeg  >/dev/null || { echo "  MISSING: ffmpeg  (brew install ffmpeg)" >&2; exit 1; }
command -v ffprobe >/dev/null || { echo "  MISSING: ffprobe (brew install ffmpeg)" >&2; exit 1; }
"$PYTHON_BIN" -c "import faster_whisper" 2>/dev/null \
  || echo "  WARNING: $PYTHON_BIN has no faster-whisper — digests will have no transcript."

# --- render and load -------------------------------------------------------

mkdir -p "$(dirname "$PLIST_DEST")"
sed \
  -e "s|__PYTHON_BIN__|$PYTHON_BIN|g" \
  -e "s|__WATCHER__|$WATCHER|g" \
  -e "s|__CONFIG__|$CONFIG|g" \
  -e "s|__WATCH_DIR__|$WATCH_DIR|g" \
  -e "s|__LOG_DIR__|$LOG_DIR|g" \
  -e "s|__INTERVAL__|$INTERVAL|g" \
  "$TEMPLATE" > "$PLIST_DEST"

plutil -lint "$PLIST_DEST" >/dev/null

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST_DEST"
launchctl enable "gui/$UID/$LABEL"

echo
echo "Installed $LABEL"
echo "  watching : $WATCH_DIR"
echo "  config   : $CONFIG"
echo "  python   : $PYTHON_BIN"
echo "  logs     : $LOG_DIR/movie-digest-watch.{out,err}.log"
echo "             $HOME/Library/Logs/movie-digest-watch.log"
echo
echo "Drop a video into the watch folder to test, or run:"
echo "  launchctl kickstart -k gui/$UID/$LABEL"
